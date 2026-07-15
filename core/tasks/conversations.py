"""
Chat-reply generation, moved here from core/views/conversations.py's inline
ai_service.chat() call + _sse_stream() so it runs as a Celery task instead
of blocking the POST /messages request/response cycle — symmetric with
core/tasks/statements.py's process_statement_pipeline, and what makes the
single multiplexed SSE connection (core/views/events.py) a genuine multiplex
of two independent async producers rather than one Celery producer and one
inline-request producer sharing a pipe by convention only.
"""

from celery import shared_task

from core.models import Conversation, Message
from services import ai_service, event_bus
from services.ai_service import AIServiceError


@shared_task
def generate_chat_reply(conversation_id: str, user_message_id: str) -> None:
    """
    Runs in the Celery worker — re-fetches by id (task args must be JSON-
    serializable). Consumes ai_service.stream_chat()'s {"event", "data"}
    envelope: each "token" event is forwarded immediately as a chat_token SSE
    event (a genuine relay of the AI service's own stream, mock or real —
    see services/ai_service.py), then the terminal "done" event's content is
    persisted as the assistant Message (+ its references) exactly as the old
    inline code did, and published as one terminal chat_message event
    carrying the same fields MessageDoneEventSerializer already documents,
    plus conversation_id since the connection is multiplexed across all of a
    user's conversations. An "error" event — or a request-level failure —
    publishes chat_error instead, with no assistant Message persisted.

    Context-gathering (e.g. the user's Budget, for a possible
    allocation_slider widget — Architectural_Guidelines.md §7: chat never
    writes to Budget directly, only reads it) is each implementation's own
    concern now, not fetched here: the mock branch reads it in-process, the
    real ai-service reads it via its own read-only DB connection.
    """
    conversation = Conversation.objects.select_related("user").get(id=conversation_id)
    user_message = Message.objects.get(id=user_message_id)

    try:
        for envelope in ai_service.stream_chat(
            str(conversation.id), str(conversation.user_id), user_message.content
        ):
            event = envelope["event"]
            if event == "token":
                event_bus.publish_user_event(
                    conversation.user_id,
                    "chat_token",
                    {"conversation_id": str(conversation.id), "data": envelope["data"]},
                )
            elif event == "done":
                result = envelope["data"]
                break
            elif event == "error":
                raise AIServiceError(envelope["data"].get("message", "AI service chat failed"))
    except AIServiceError as exc:
        event_bus.publish_user_event(
            conversation.user_id,
            "chat_error",
            {"conversation_id": str(conversation.id), "message": str(exc)},
        )
        return

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
    # when *this* row is saved — creating the Message row above doesn't
    # touch it automatically, so it's bumped explicitly here.
    conversation.save()

    event_bus.publish_user_event(
        conversation.user_id,
        "chat_message",
        {
            "conversation_id": str(conversation.id),
            "id": str(assistant_message.id),
            "content": assistant_message.content,
            "widget": assistant_message.widget_json or {"type": None, "payload": None},
            "references": [
                {"target_type": r.target_type, "target_id": str(r.target_id)}
                for r in assistant_message.references.all()
            ],
        },
    )
