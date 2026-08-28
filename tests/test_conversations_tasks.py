"""
Endpoint-level tests for chat-reply generation now that it runs as a Celery
task (core/tasks/conversations.py). See tests/test_statements_tasks.py's
module docstring for how the autouse _celery_eager_mode fixture and the
explicit fake_redis fixture combine here — the same "response reflects
pre-enqueue state, DB reflects the task's result" pattern applies: POST
.../messages returns 202 with only the user's own message; the assistant's
reply only exists once you look at persisted Message rows afterward.
"""

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import (
    Budget,
    BudgetAllocation,
    Category,
    ConsentRecord,
    Conversation,
    Message,
    User,
)
from core.tasks.conversations import generate_chat_reply
from services import ai_service, event_bus


@pytest.fixture
def user(db):
    user = User.objects.create_user(
        email="conversations-test@example.com", password="x", name="Conversations Test"
    )
    # Sending a message requires data_processing consent (core/permissions.py's
    # HasDataProcessingConsent) — granted here to match what onboarding does
    # for a real signup, so these tests exercise the normal-consent path.
    ConsentRecord.objects.create(
        user=user, consent_type="data_processing", policy_version="v2.0", granted_at=timezone.now()
    )
    return user


@pytest.fixture
def client(user):
    api_client = APIClient()
    api_client.force_authenticate(user=user)
    return api_client


@pytest.fixture
def conversation(user):
    return Conversation.objects.create(user=user)


def test_post_message_enqueues_reply_and_persists_assistant_message(
    client, conversation, fake_redis
):
    response = client.post(
        f"/chat/conversations/{conversation.id}/messages/",
        {"content": "what can you help with?"},
        format="json",
    )

    assert response.status_code == 202
    assert response.data["sender"] == "user"
    assert response.data["content"] == "what can you help with?"

    messages = list(Message.objects.filter(conversation=conversation).order_by("created_at"))
    assert len(messages) == 2
    assert messages[0].sender == "user"
    assert messages[1].sender == "assistant"
    assert messages[1].content  # the mock's canned reply, non-empty
    assert messages[1].suggestions_json  # the mock's canned suggestions, non-empty


def test_post_message_mentioning_budget_produces_allocation_widget(
    client, user, conversation, fake_redis
):
    budget = Budget.objects.create(user=user)
    BudgetAllocation.objects.create(
        budget=budget,
        category=Category.objects.get(name="housing"),
        allocated_percentage="30.00",
        allocated_amount="3000.00",
    )

    client.post(
        f"/chat/conversations/{conversation.id}/messages/",
        {"content": "show me my budget allocation"},
        format="json",
    )

    assistant_message = Message.objects.get(conversation=conversation, sender="assistant")
    assert assistant_message.widget_json["type"] == "allocation_slider"
    assert assistant_message.references.filter(target_type="budget", target_id=budget.id).exists()


def test_generate_chat_reply_skips_message_when_stream_has_no_terminal_event(
    user, conversation, fake_redis, monkeypatch
):
    """
    A stream_chat() implementation that ends without a "done"/"error" event
    (e.g. the ai-service crashed mid-stream) must not fall through to
    persisting a Message from an unset result — this is the backstop
    core/tasks/conversations.py adds on top of stream_chat()'s own guard for
    the real branch specifically.
    """
    user_message = Message.objects.create(
        conversation=conversation, sender="user", content="hello", stage="general"
    )

    def _stream_with_no_terminal_event(*args, **kwargs):
        yield {"event": "token", "data": "partial "}

    class _FakeClient:
        stream_chat = staticmethod(_stream_with_no_terminal_event)

    monkeypatch.setattr(ai_service, "get_client", lambda: _FakeClient())

    generate_chat_reply(str(conversation.id), str(user_message.id))

    assert not Message.objects.filter(conversation=conversation, sender="assistant").exists()


def test_generate_chat_reply_relays_tool_call_events_as_chat_tool_status(
    user, conversation, fake_redis, monkeypatch
):
    """The if/elif chain in generate_chat_reply has no else branch — an
    unmatched/mistyped event name silently no-ops. This asserts the new
    "tool_call" branch actually publishes chat_tool_status with the expected
    shape, and that persisting the assistant Message afterward still works
    (regression coverage: the new branch mustn't disturb the done handling)."""
    user_message = Message.objects.create(
        conversation=conversation, sender="user", content="show my spending", stage="general"
    )

    def _stream_with_tool_call(*args, **kwargs):
        yield {
            "event": "tool_call",
            "data": {"call_id": "call_1", "tool": "get_transactions", "status": "started"},
        }
        yield {"event": "token", "data": "You spent "}
        yield {
            "event": "tool_call",
            "data": {"call_id": "call_1", "tool": "get_transactions", "status": "completed"},
        }
        yield {
            "event": "done",
            "data": {
                "content": "You spent 100 EGP.",
                "widget": {"type": None, "payload": None},
                "references": [],
                "suggestions": [],
            },
        }

    class _FakeClient:
        stream_chat = staticmethod(_stream_with_tool_call)

    monkeypatch.setattr(ai_service, "get_client", lambda: _FakeClient())

    published = []
    original_publish = event_bus.publish_user_event

    def _recording_publish(user_id, event_type, data):
        published.append((user_id, event_type, data))
        return original_publish(user_id, event_type, data)

    monkeypatch.setattr(event_bus, "publish_user_event", _recording_publish)

    generate_chat_reply(str(conversation.id), str(user_message.id))

    tool_status_events = [p for p in published if p[1] == "chat_tool_status"]
    assert tool_status_events == [
        (
            user.id,
            "chat_tool_status",
            {
                "conversation_id": str(conversation.id),
                "call_id": "call_1",
                "tool": "get_transactions",
                "status": "started",
            },
        ),
        (
            user.id,
            "chat_tool_status",
            {
                "conversation_id": str(conversation.id),
                "call_id": "call_1",
                "tool": "get_transactions",
                "status": "completed",
            },
        ),
    ]
    assistant_message = Message.objects.get(conversation=conversation, sender="assistant")
    assert assistant_message.content == "You spent 100 EGP."
    # Persisted onto the row itself, not just relayed live — this is what
    # lets a step list survive a refetch of this conversation's history
    # instead of only ever existing in the SSE stream a client happened to
    # be listening to when the turn ran.
    assert assistant_message.thinking_json is not None
    assert assistant_message.thinking_json["steps"] == [
        {"call_id": "call_1", "tool": "get_transactions", "status": "started"},
        {"call_id": "call_1", "tool": "get_transactions", "status": "completed"},
    ]
    assert assistant_message.thinking_json["duration_ms"] >= 0

    message_events = [p for p in published if p[1] == "chat_message"]
    assert len(message_events) == 1
    assert message_events[0][2]["thinking"] == assistant_message.thinking_json


def test_generate_chat_reply_leaves_thinking_json_null_when_no_tool_called(
    client, conversation, fake_redis
):
    """A turn with no tool calls (e.g. routed to general) must not get a
    spurious "Thought for 0 seconds" summary — thinking_json stays null."""
    client.post(
        f"/chat/conversations/{conversation.id}/messages/",
        {"content": "what can you help with?"},
        format="json",
    )

    assistant_message = Message.objects.get(conversation=conversation, sender="assistant")
    assert assistant_message.thinking_json is None
