"""
Endpoint-level tests for the two machine-to-machine endpoints
(core/views/webhooks.py) — no end-user JWT, shared-secret authenticated
(core/authentication.py's BankSyncServiceAuthentication/
MockBankServiceAuthentication). Plain APIClient (no force_authenticate)
since these callers have no User at all — identity comes from the header,
not request.user.
"""

import pytest
from django.core import mail
from rest_framework.test import APIClient

from core.models import BankAccount, BankConnection, Transaction, User
from services import notification_service


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email="webhook-test@example.com", password="x", name="Webhook Test"
    )


@pytest.fixture
def synced_account(user):
    connection = BankConnection.objects.create(
        user=user, provider_slug="mock_bank", status=BankConnection.STATUS_LINKED
    )
    return BankAccount.objects.create(
        user=user,
        bank_name="Mock National Bank",
        masked_account_number="****1234",
        link_type=BankAccount.LINK_TYPE_SYNCED,
        connection=connection,
        external_account_id="acc-1",
    )


# ============================================================================
# POST /webhooks/bank-sync/
# ============================================================================


def _webhook_payload(**overrides):
    payload = {
        "provider_slug": "mock_bank",
        "external_account_id": "acc-1",
        "transactions": [
            {
                "external_transaction_id": "txn-1",
                "transaction_date": "2026-07-01",
                "merchant_raw": "Carrefour",
                "amount": "150.00",
                "transaction_type": "debit",
                "currency": "EGP",
                "balance": "4850.00",
            }
        ],
    }
    payload.update(overrides)
    return payload


def test_webhook_with_correct_secret_ingests_transaction(
    client, settings, synced_account, fake_redis
):
    settings.BANK_SYNC_WEBHOOK_SECRET = "test-webhook-secret"

    response = client.post(
        "/webhooks/bank-sync/",
        _webhook_payload(),
        format="json",
        HTTP_X_WEBHOOK_SECRET="test-webhook-secret",
    )

    assert response.status_code == 202
    # tests/conftest.py's autouse _celery_eager_mode makes .delay() run
    # synchronously, so the transaction already exists by the time this
    # request returns.
    transaction = Transaction.objects.get(account=synced_account)
    assert transaction.source == "synced"
    assert transaction.merchant_raw == "Carrefour"


def test_webhook_missing_secret_401s(client, settings, synced_account):
    settings.BANK_SYNC_WEBHOOK_SECRET = "test-webhook-secret"

    response = client.post("/webhooks/bank-sync/", _webhook_payload(), format="json")

    assert response.status_code == 401
    assert Transaction.objects.count() == 0


def test_webhook_wrong_secret_401s(client, settings, synced_account):
    settings.BANK_SYNC_WEBHOOK_SECRET = "test-webhook-secret"

    response = client.post(
        "/webhooks/bank-sync/",
        _webhook_payload(),
        format="json",
        HTTP_X_WEBHOOK_SECRET="wrong-secret",
    )

    assert response.status_code == 401
    assert Transaction.objects.count() == 0


def test_webhook_unknown_account_404s(client, settings, synced_account):
    settings.BANK_SYNC_WEBHOOK_SECRET = "test-webhook-secret"

    response = client.post(
        "/webhooks/bank-sync/",
        _webhook_payload(external_account_id="acc-does-not-exist"),
        format="json",
        HTTP_X_WEBHOOK_SECRET="test-webhook-secret",
    )

    assert response.status_code == 404


# ============================================================================
# POST /internal/notifications/email/
# ============================================================================


def test_internal_email_with_correct_token_sends_email(client, settings):
    settings.MOCK_BANK_SERVICE_TOKEN = "test-service-token"

    response = client.post(
        "/internal/notifications/email/",
        {"to": "customer@example.com", "subject": "Your code", "body": "123456"},
        format="json",
        HTTP_X_SERVICE_TOKEN="test-service-token",
    )

    assert response.status_code == 202
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["customer@example.com"]
    assert mail.outbox[0].subject == "Your code"


def test_internal_email_wrong_token_401s(client, settings):
    settings.MOCK_BANK_SERVICE_TOKEN = "test-service-token"

    response = client.post(
        "/internal/notifications/email/",
        {"to": "customer@example.com", "subject": "Your code", "body": "123456"},
        format="json",
        HTTP_X_SERVICE_TOKEN="wrong-token",
    )

    assert response.status_code == 401
    assert len(mail.outbox) == 0


def test_internal_email_notification_failure_returns_502(client, settings, monkeypatch):
    settings.MOCK_BANK_SERVICE_TOKEN = "test-service-token"
    monkeypatch.setattr(
        notification_service,
        "send_email",
        lambda *a, **kw: (_ for _ in ()).throw(
            notification_service.NotificationServiceError("smtp down")
        ),
    )

    response = client.post(
        "/internal/notifications/email/",
        {"to": "customer@example.com", "subject": "Your code", "body": "123456"},
        format="json",
        HTTP_X_SERVICE_TOKEN="test-service-token",
    )

    assert response.status_code == 502
    assert response.data["error"]["code"] == "notification_service_unavailable"
