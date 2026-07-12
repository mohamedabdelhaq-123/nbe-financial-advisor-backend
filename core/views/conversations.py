import json

from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import generics, mixins
from rest_framework.exceptions import ValidationError
from rest_framework.generics import GenericAPIView
from rest_framework.pagination import CursorPagination, LimitOffsetPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from core.filters.conversations import ConversationFilterSet, MessageFilterSet
from core.models import Budget, Conversation, Message
from core.openapi import error_responses
from core.serializers.conversations import (
    ConversationAttachmentRequestSerializer,
    ConversationAttachmentResponseSerializer,
    ConversationListItemSerializer,
    ConversationSerializer,
    MessageCreateSerializer,
    MessageDoneEventSerializer,
    MessageSerializer,
)
from core.views.statements import create_statement_from_upload
from services import ai_service


@extend_schema_view(
    get=extend_schema(
        description=(
            "List the current user's chat sessions, newest-active-first. "
            "Supports filtering/sorting via the query parameters below. "
            "`preview` on each row is the most recent message's first ~80 "
            "characters, for a session-list UI that doesn't want to fetch "
            "every message just to show a snippet."
        )
    )
)
class ConversationListCreateView(generics.ListCreateAPIView):
    """List the current user's chat sessions, or start a new one."""

    pagination_class = LimitOffsetPagination
    filter_backends = [DjangoFilterBackend]
    filterset_class = ConversationFilterSet

    def get_serializer_class(self):
        return (
            ConversationSerializer
            if self.request.method == "POST"
            else ConversationListItemSerializer
        )

    def get_queryset(self):
        # swagger_fake_view: see aggregations.py's TransactionListCreateView.get_queryset().
        if getattr(self, "swagger_fake_view", False):
            return Conversation.objects.none()
        return Conversation.objects.filter(user=self.request.user).order_by("-last_message_at")

    @extend_schema(
        description=(
            "Start a new chat session. Takes no request body at all — a "
            "session needs no initial data to start, so there's nothing "
            "for a client to send here."
        ),
        request=None,
        responses={201: ConversationSerializer},
    )
    def post(self, request, *args, **kwargs):
        conversation = Conversation.objects.create(user=request.user)
        return Response(ConversationSerializer(conversation).data, status=201)


@extend_schema_view(delete=extend_schema(responses={204: None, **error_responses(404)}))
class ConversationDetailView(generics.DestroyAPIView):
    """Delete a chat session and every message in it. 404 if the
    conversation doesn't exist or doesn't belong to the current user."""

    # DestroyAPIView wants a serializer_class even though DELETE returns no
    # body — reusing ConversationSerializer rather than duplicating it just
    # for this attribute.
    serializer_class = ConversationSerializer
    lookup_url_kwarg = "conversation_id"

    def get_queryset(self):
        return Conversation.objects.filter(user=self.request.user)


class MessageCursorPagination(CursorPagination):
    # Always oldest->newest (Data_Shapes_Conversations.md: "no overridable
    # sort param" — cursor pagination assumes one fixed scroll direction).
    ordering = "created_at"
    page_size = 50


class ConversationMessagesView(mixins.ListModelMixin, GenericAPIView):
    """
    List a conversation's messages (oldest first, cursor-paginated since
    new messages keep arriving at one end — there's no meaningful "jump to
    an arbitrary offset" here), or send a new one and get the assistant's
    reply streamed back.

    Combines ListModelMixin directly with GenericAPIView rather than using
    ListCreateAPIView, since POST's SSE-streaming response is a fully
    custom method that ListCreateAPIView's generic create() flow can't
    express.
    """

    serializer_class = MessageSerializer
    pagination_class = MessageCursorPagination
    filter_backends = [DjangoFilterBackend]
    filterset_class = MessageFilterSet

    def _get_conversation(self, request, conversation_id):
        return get_object_or_404(Conversation, id=conversation_id, user=request.user)

    def get_queryset(self):
        # swagger_fake_view: same rationale as TransactionListCreateView's
        # get_queryset() (aggregations.py), plus this view has no
        # conversation_id kwarg at all during schema generation.
        if getattr(self, "swagger_fake_view", False):
            return Message.objects.none()
        conversation = self._get_conversation(self.request, self.kwargs["conversation_id"])
        return conversation.messages.order_by("created_at")

    @extend_schema(responses={200: MessageSerializer(many=True), **error_responses(404)})
    def get(self, request, conversation_id):
        # Reopening a conversation to read it does NOT bump last_message_at
        # — only posting a new message does, so a "this session may be
        # stale" warning elsewhere can be computed from last_message_at's
        # age at read time without racing this read.
        return self.list(request, conversation_id=conversation_id)

    @extend_schema(
        request=MessageCreateSerializer,
        responses={
            200: OpenApiResponse(
                response=MessageDoneEventSerializer,
                description=(
                    'text/event-stream — a sequence of {"event": "token", "data": str} chunks '
                    'followed by one terminal {"event": "done", "data": <this shape>} event. '
                    "Not a single JSON body; documented here as the terminal event's payload only."
                ),
            ),
            **error_responses(404, 422),
        },
    )
    def post(self, request, conversation_id):
        conversation = self._get_conversation(request, conversation_id)
        serializer = MessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        content = serializer.validated_data["content"]

        Message.objects.create(
            conversation=conversation, sender="user", content=content, stage="general"
        )

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

        return StreamingHttpResponse(
            _sse_stream(assistant_message), content_type="text/event-stream"
        )


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
    Upload a bank statement from within a chat session — a shortcut into
    the same statement-ingestion pipeline `POST /statements` uses (same
    202-Accepted-and-poll contract, same `status` progression), just tagged
    to this conversation: an assistant message is posted announcing the
    upload, referencing the new statement, rather than the statement
    gaining any new field of its own to track which conversation it came
    from.
    """

    @extend_schema(
        request=ConversationAttachmentRequestSerializer,
        responses={202: ConversationAttachmentResponseSerializer, **error_responses(404, 422)},
    )
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
            {
                "statement_id": str(statement.id),
                "status": statement.status,
                "message_id": str(message.id),
            },
            status=202,
        )
