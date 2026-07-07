import json

from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import generics
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import CursorPagination, LimitOffsetPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import Budget, Conversation, Message
from core.serializers.conversations import (
    ConversationListItemSerializer,
    ConversationSerializer,
    MessageCreateSerializer,
    MessageSerializer,
)
from core.views.statements import create_statement_from_upload
from services import ai_service


class ConversationListCreateView(generics.ListCreateAPIView):
    """POST/GET /chat/conversations"""

    pagination_class = LimitOffsetPagination

    def get_serializer_class(self):
        return ConversationSerializer if self.request.method == "POST" else ConversationListItemSerializer

    def get_queryset(self):
        qs = Conversation.objects.filter(user=self.request.user)
        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)
        return qs.order_by("-last_message_at")

    def post(self, request, *args, **kwargs):
        # Empty body — a session needs no initial data, freely created
        # (Data_Governance_Specs.md §3).
        conversation = Conversation.objects.create(user=request.user)
        return Response(ConversationSerializer(conversation).data, status=201)


class ConversationDetailView(generics.DestroyAPIView):
    """DELETE /chat/conversations/{conversation_id}"""

    lookup_url_kwarg = "conversation_id"

    def get_queryset(self):
        return Conversation.objects.filter(user=self.request.user)


class MessageCursorPagination(CursorPagination):
    # Always oldest->newest (Data_Shapes_Conversations.md: "no overridable
    # sort param" — cursor pagination assumes one fixed scroll direction).
    ordering = "created_at"
    page_size = 50


class ConversationMessagesView(APIView):
    """GET/POST /chat/conversations/{conversation_id}/messages"""

    def _get_conversation(self, request, conversation_id):
        return get_object_or_404(Conversation, id=conversation_id, user=request.user)

    def get(self, request, conversation_id):
        # Reopening a conversation to read it does NOT bump last_message_at
        # (Data_Shapes_Conversations.md's stale-session note) — only POSTing
        # a new message does, so the "may be stale" warning can be computed
        # from last_message_at's age at read time without racing this read.
        conversation = self._get_conversation(request, conversation_id)
        qs = conversation.messages.order_by("created_at")
        stage = request.query_params.get("stage")
        if stage:
            qs = qs.filter(stage=stage)

        paginator = MessageCursorPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response(MessageSerializer(page, many=True).data)

    def post(self, request, conversation_id):
        conversation = self._get_conversation(request, conversation_id)
        serializer = MessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        content = serializer.validated_data["content"]

        Message.objects.create(conversation=conversation, sender="user", content=content, stage="general")

        # The assistant's power to change data is deliberately narrow
        # (Architectural_Guidelines.md §7) — chat never writes to Budget
        # directly here, it only reads the user's existing plan (if any) to
        # ground a possible allocation_slider widget; any actual edit still
        # goes through PATCH /budget once the user confirms inside that widget.
        budget = Budget.objects.filter(user=request.user).prefetch_related("allocations").first()
        result = ai_service.chat(content, budget=budget)

        assistant_message = Message.objects.create(
            conversation=conversation,
            sender="assistant",
            content=result["content"],
            stage="general",
            widget_json=result["widget"],
        )
        for ref in result["references"]:
            assistant_message.add_reference(ref["target_type"], ref["target_id"])

        # Conversation.last_message_at is auto_now=True, which only updates
        # when *this* row is saved — creating related Message rows above
        # doesn't touch it automatically, so it's bumped explicitly here.
        conversation.save()

        return StreamingHttpResponse(_sse_stream(assistant_message), content_type="text/event-stream")


def _sse_stream(message: Message):
    """
    Yields Server-Sent Events per Data_Shapes_Conversations.md's documented
    shape: a few "token" events (the mock's already-fully-computed reply
    chunked into words, simulating progressive generation — there's nothing
    genuinely incremental to stream from a synchronous mock), then one
    terminal "done" event carrying the already-persisted message's real
    id/content/widget/references.

    Runs under WSGI (gunicorn, per the Dockerfile), not the ASGI server real
    streaming needs (API Design Guidelines §9: "requires the backend to run
    under ASGI for that route") — Django's StreamingHttpResponse still works
    under WSGI for a synchronous generator like this one, just without async
    concurrency benefits. Swapping to true ASGI + an async generator is a
    follow-up alongside wiring the real AI service, not something this mock
    needs in order to exercise the endpoint's contract now.
    """
    for word in message.content.split(" "):
        yield f"data: {json.dumps({'event': 'token', 'data': word + ' '})}\n\n"

    yield "data: {}\n\n".format(
        json.dumps(
            {
                "event": "done",
                "data": {
                    "id": str(message.id),
                    "content": message.content,
                    "widget": message.widget_json or {"type": None, "payload": None},
                    "references": [
                        {"target_type": r.target_type, "target_id": str(r.target_id)}
                        for r in message.references.all()
                    ],
                },
            }
        )
    )


class ConversationAttachmentsView(APIView):
    """
    POST /chat/conversations/{conversation_id}/attachments

    Shortcut into the Statements pipeline — same underlying processing as
    POST /statements (create_statement_from_upload(), shared with
    core/views/statements.py), tagged with the originating conversation via
    a system message + reference rather than any new field on StatementFile.
    """

    def post(self, request, conversation_id):
        conversation = get_object_or_404(Conversation, id=conversation_id, user=request.user)
        file_obj = request.FILES.get("file")
        if not file_obj:
            raise ValidationError({"file": "This field is required."})

        statement = create_statement_from_upload(request.user, file_obj)

        message = Message.objects.create(
            conversation=conversation,
            sender="assistant",
            stage="extraction_review",
            content="I've started processing your uploaded statement.",
        )
        message.add_reference("statement", statement.id)
        conversation.save()

        return Response(
            {"statement_id": str(statement.id), "status": statement.status, "message_id": str(message.id)},
            status=202,
        )
