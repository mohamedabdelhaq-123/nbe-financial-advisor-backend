"""
Endpoint-level tests for the one machine-to-machine endpoint
(core/views/webhooks.py) — no end-user JWT, shared-secret authenticated
(core/authentication.py's BankSyncServiceAuthentication). Plain APIClient
(no force_authenticate) since this caller has no User at all — identity
comes from the header, not request.user.
"""

import pytest
from rest_framework.test import APIClient

from core.models import BankAccount, BankConnection, Transaction, User


@pytest.fixture
def client():
    return APIClient()


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email="webhook-test@example.com", password="x", name="Webhook Test"
    )


@pytest.fixture
def connection(user):
    return BankConnection.objects.create(
        user=user,
        provider_slug="mock_bank",
        status=BankConnection.STATUS_LINKED,
        external_customer_id="cust-1",
        access_token="fake-access-token",
    )


@pytest.fixture
def synced_account(user, connection):
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
        "external_customer_id": "cust-1",
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


def test_webhook_unknown_customer_404s(client, settings, synced_account):
    # Neither the account nor a connection for this customer exists — 404s
    # at the fallback connection lookup itself, before any network call.
    settings.BANK_SYNC_WEBHOOK_SECRET = "test-webhook-secret"

    response = client.post(
        "/webhooks/bank-sync/",
        _webhook_payload(
            external_account_id="acc-does-not-exist", external_customer_id="cust-does-not-exist"
        ),
        format="json",
        HTTP_X_WEBHOOK_SECRET="test-webhook-secret",
    )

    assert response.status_code == 404


def test_webhook_discovers_new_account_via_fallback(
    client, settings, connection, fake_redis, monkeypatch
):
    # external_account_id "acc-2" isn't a BankAccount yet, but the
    # connection is real — the fallback re-fetches accounts from the
    # connector and lands the newly-discovered one, rather than 404ing on
    # legitimate data the initial link simply hadn't seen yet.
    settings.BANK_SYNC_WEBHOOK_SECRET = "test-webhook-secret"
    monkeypatch.setattr(
        "services.bank_connectors.mock_bank.MockBankConnector.fetch_accounts",
        lambda self, access_token: [
            {
                "external_account_id": "acc-2",
                "bank_name": "Mock National Bank",
                "masked_account_number": "****5678",
                "account_type": "savings",
                "currency": "EGP",
            }
        ],
    )
    monkeypatch.setattr(
        "services.bank_connectors.mock_bank.MockBankConnector.fetch_transactions",
        lambda self, access_token, external_account_id, since=None: [],
    )

    response = client.post(
        "/webhooks/bank-sync/",
        _webhook_payload(external_account_id="acc-2"),
        format="json",
        HTTP_X_WEBHOOK_SECRET="test-webhook-secret",
    )

    assert response.status_code == 202
    account = BankAccount.objects.get(connection=connection, external_account_id="acc-2")
    assert account.link_type == BankAccount.LINK_TYPE_SYNCED
    transaction = Transaction.objects.get(account=account)
    assert transaction.merchant_raw == "Carrefour"
