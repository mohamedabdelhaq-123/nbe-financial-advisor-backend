"""
Endpoint-level tests for PATCH /budget's `changed_via` field
(core/serializers/budgets.py::BudgetUpdateSerializer) — PLAN.md Checkpoint 3.

The chat allocation-confirmation widget (frontend's AllocationSliderTool)
sends changed_via: "chat", but the serializer only accepted "chat_hitl" (a
value nothing ever actually sent) — every chat-driven budget update 422'd.
"""

import pytest
from django.core import mail
from rest_framework.test import APIClient

from core.models import BudgetHistory, Category, User


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email="budget-changed-via-test@example.com", password="x", name="Budget Test"
    )


@pytest.fixture
def client(user):
    api_client = APIClient()
    api_client.force_authenticate(user=user)
    return api_client


@pytest.fixture
def food(db):
    category, _ = Category.objects.get_or_create(
        name="food", defaults={"label": "Food", "category_type": "expense"}
    )
    return category


@pytest.fixture
def existing_budget(client, food):
    resp = client.post(
        "/budget/",
        {"allocations": [{"category": "food", "allocated_percentage": "100.00"}]},
        format="json",
    )
    assert resp.status_code == 201
    return resp.data


class TestChangedVia:
    def test_chat_is_accepted(self, client, existing_budget, food):
        resp = client.patch(
            "/budget/",
            {
                "allocations": [{"category": "food", "allocated_percentage": "100.00"}],
                "changed_via": "chat",
            },
            format="json",
        )
        assert resp.status_code == 200
        latest = BudgetHistory.objects.latest("changed_at")
        assert latest.changed_via == "chat"

    def test_chat_hitl_is_no_longer_accepted(self, client, existing_budget, food):
        resp = client.patch(
            "/budget/",
            {
                "allocations": [{"category": "food", "allocated_percentage": "100.00"}],
                "changed_via": "chat_hitl",
            },
            format="json",
        )
        assert resp.status_code == 422

    def test_onboarding_still_works(self, client, existing_budget, food):
        resp = client.patch(
            "/budget/",
            {
                "allocations": [{"category": "food", "allocated_percentage": "100.00"}],
                "changed_via": "onboarding",
            },
            format="json",
        )
        assert resp.status_code == 200
        latest = BudgetHistory.objects.latest("changed_at")
        assert latest.changed_via == "onboarding"

    def test_default_is_dashboard(self, client, existing_budget, food):
        resp = client.patch(
            "/budget/",
            {"allocations": [{"category": "food", "allocated_percentage": "100.00"}]},
            format="json",
        )
        assert resp.status_code == 200
        latest = BudgetHistory.objects.latest("changed_at")
        assert latest.changed_via == "dashboard"


class TestBudgetChangeEmail:
    def test_patch_emails_the_user(self, client, user, existing_budget, food):
        mail.outbox.clear()
        resp = client.patch(
            "/budget/",
            {
                "allocations": [{"category": "food", "allocated_percentage": "100.00"}],
                "changed_via": "chat",
            },
            format="json",
        )
        assert resp.status_code == 200
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == [user.email]
