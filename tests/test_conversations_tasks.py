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
from rest_framework.test import APIClient

from core.models import Budget, BudgetAllocation, Category, Conversation, Message, User
from core.tasks.conversations import generate_chat_reply
from services import ai_service


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email="conversations-test@example.com", password="x", name="Conversations Test"
    )


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

    monkeypatch.setattr(ai_service, "stream_chat", _stream_with_no_terminal_event)

    generate_chat_reply(str(conversation.id), str(user_message.id))

    assert not Message.objects.filter(conversation=conversation, sender="assistant").exists()
