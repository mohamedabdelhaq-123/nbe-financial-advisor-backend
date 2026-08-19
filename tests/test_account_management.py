"""
Endpoint-level tests for the profile page's Account Management actions
(core/views/profile.py): GET /users/me/consent (consent history) and
POST /users/me/data-export (async "request my account data" export).
"""

import json

from django.core import mail
from rest_framework.test import APIClient

from core.models import Budget, BudgetAllocation, Category, ConsentRecord, Goal, User


def _client(user):
    api_client = APIClient()
    api_client.force_authenticate(user=user)
    return api_client


class TestConsentHistory:
    def test_lists_grants_and_revokes_newest_first(self, db):
        user = User.objects.create_user(email="consent-history@example.com", password="x", name="X")
        client = _client(user)

        client.post(
            "/users/me/consent/",
            {"consent_type": "terms", "policy_version": "v1"},
            format="json",
        )
        grant_resp = client.post(
            "/users/me/consent/",
            {"consent_type": "privacy", "policy_version": "v1"},
            format="json",
        )
        client.delete(f"/users/me/consent/{grant_resp.data['id']}/")

        resp = client.get("/users/me/consent/")
        assert resp.status_code == 200
        assert len(resp.data) == 3
        # Newest first: the revoke event (just created) leads.
        assert resp.data[0]["revoked_at"] is not None
        assert resp.data[0]["consent_type"] == "privacy"

    def test_only_shows_the_requesting_users_own_records(self, db):
        user = User.objects.create_user(email="consent-me@example.com", password="x", name="X")
        other = User.objects.create_user(email="consent-other@example.com", password="x", name="Y")
        ConsentRecord.objects.create(user=other, consent_type="terms", policy_version="v1")

        resp = _client(user).get("/users/me/consent/")
        assert resp.status_code == 200
        assert resp.data == []

    def test_requires_authentication(self, db):
        resp = APIClient().get("/users/me/consent/")
        assert resp.status_code == 401


class TestDataExport:
    def test_enqueues_and_emails_a_json_attachment_with_the_users_data(self, db):
        user = User.objects.create_user(
            email="export-me@example.com",
            password="x",
            name="Export Me",
            monthly_income="10000.00",
            email_verified=True,
        )
        food = Category.objects.get_or_create(
            name="food", defaults={"label": "Food", "category_type": "expense"}
        )[0]
        budget = Budget.objects.create(user=user, selected_template_key="balanced")
        BudgetAllocation.objects.create(
            budget=budget,
            category=food,
            allocated_percentage="100.00",
            allocated_amount="10000.00",
        )
        Goal.objects.create(user=user, name="Emergency fund", target_amount="5000.00", timeline_months=6)
        ConsentRecord.objects.create(user=user, consent_type="terms", policy_version="v1")

        resp = _client(user).post("/users/me/data-export/")
        assert resp.status_code == 202

        assert len(mail.outbox) == 1
        sent = mail.outbox[0]
        assert sent.to == ["export-me@example.com"]
        assert len(sent.attachments) == 1
        filename, content, mimetype = sent.attachments[0]
        assert filename == "account-data-export.json"
        assert mimetype == "application/json"

        export = json.loads(content)
        assert export["account"]["email"] == "export-me@example.com"
        assert export["budget"]["allocations"] == [
            {
                "category": "food",
                "allocated_percentage": 100.0,
                "allocated_amount": 10000.0,
                "currency": "EGP",
            }
        ]
        assert export["goal"]["name"] == "Emergency fund"
        assert export["consent_records"][0]["consent_type"] == "terms"

    def test_no_budget_or_goal_exports_as_null_not_an_error(self, db):
        user = User.objects.create_user(
            email="export-bare@example.com", password="x", name="X", email_verified=True
        )

        resp = _client(user).post("/users/me/data-export/")
        assert resp.status_code == 202

        content = mail.outbox[0].attachments[0][1]
        export = json.loads(content)
        assert export["budget"] is None
        assert export["goal"] is None

    def test_requires_authentication(self, db):
        resp = APIClient().post("/users/me/data-export/")
        assert resp.status_code == 401

    def test_unverified_local_account_is_rejected(self, db):
        user = User.objects.create_user(email="unverified@example.com", password="x", name="X")
        assert user.email_verified is False

        resp = _client(user).post("/users/me/data-export/")
        assert resp.status_code == 403
        assert len(mail.outbox) == 0

    def test_verified_local_account_is_allowed(self, db):
        user = User.objects.create_user(
            email="verified@example.com", password="x", name="X", email_verified=True
        )
        resp = _client(user).post("/users/me/data-export/")
        assert resp.status_code == 202
        assert len(mail.outbox) == 1

    def test_bank_login_account_skips_the_verification_gate(self, db):
        # Bank-login accounts (services/bank_connectors/, core/views/auth.py's
        # BankLoginCallbackView) are provisioned with an unusable password and
        # never go through the emailed-verification flow — their identity was
        # already proven by bank OTP, so email_verified staying False forever
        # must not block them.
        user = User.objects.create_user(email="bank-login@example.com", password=None, name="X")
        assert user.email_verified is False
        assert not user.has_usable_password()

        resp = _client(user).post("/users/me/data-export/")
        assert resp.status_code == 202
        assert len(mail.outbox) == 1
