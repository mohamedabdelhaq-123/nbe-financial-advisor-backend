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

from core.models import Budget, BudgetAllocation, Conversation, Message, User


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
        category="Rent",
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
