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

from core.models import Budget, Conversation, Message
from services import ai_service, event_bus


@shared_task
def generate_chat_reply(conversation_id: str, user_message_id: str) -> None:
    """
    Runs in the Celery worker — re-fetches by id (task args must be JSON-
    serializable). Calls ai_service.chat() (unchanged mock), persists the
    assistant Message + its references exactly as the old inline code did,
    then publishes the word-by-word "typing" effect as chat_token events
    (preserving the old _sse_stream()'s UX, now over the persistent
    connection instead of a per-request fake stream) followed by one
    terminal chat_message event carrying the same fields
    MessageDoneEventSerializer already documents, plus conversation_id since
    the connection is multiplexed across all of a user's conversations.
    """
    conversation = Conversation.objects.select_related("user").get(id=conversation_id)
    user_message = Message.objects.get(id=user_message_id)

    # The assistant's power to change data is deliberately narrow
    # (Architectural_Guidelines.md §7) — chat never writes to Budget
    # directly here, it only reads the user's existing plan (if any) to
    # ground a possible allocation_slider widget; any actual edit still
    # goes through PATCH /budget once the user confirms inside that widget.
    budget = Budget.objects.filter(user=conversation.user).prefetch_related("allocations").first()
    result = ai_service.chat(user_message.content, budget=budget)

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

    for word in result["content"].split(" "):
        event_bus.publish_user_event(
            conversation.user_id,
            "chat_token",
            {"conversation_id": str(conversation.id), "data": word + " "},
        )

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
