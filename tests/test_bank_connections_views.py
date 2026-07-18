"""
Endpoint-level tests for the bank-connection linking flow
(core/views/bank_connections.py). Uses a fake BankConnector (monkeypatched
in place of get_connector) rather than a real HTTP call, so these tests
exercise the view/model wiring — BankConnection state transitions,
BankAccount creation, the initial-backfill task — without depending on
mock-bank-oauth/mock-bank-sync actually running.

fake_redis is required because ingest_synced_transactions (run synchronously
by tests/conftest.py's autouse _celery_eager_mode fixture) publishes SSE
events on every run — same reasoning as tests/test_statements_tasks.py.
"""

import pytest
from rest_framework.test import APIClient

import core.views.bank_connections as bank_connections_view_module
from core.models import BankAccount, BankConnection, Transaction, User
from services.bank_connectors import BankConnectorError


class _FakeConnector:
    slug = "mock_bank"

    def __init__(self, accounts=None, transactions_by_account=None, exchange_error=None):
        self._accounts = accounts if accounts is not None else []
        self._transactions_by_account = transactions_by_account or {}
        self._exchange_error = exchange_error
        self.exchange_calls = []

    def get_authorize_url(self, state, redirect_uri):
        return f"http://fake-mock-bank-oauth/authorize?state={state}"

    def exchange_code_for_token(self, code):
        self.exchange_calls.append(code)
        if self._exchange_error:
            raise self._exchange_error
        return {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_in": 3600,
            "external_customer_id": "cust-1",
        }

    def fetch_accounts(self, access_token):
        return self._accounts

    def fetch_transactions(self, access_token, external_account_id, since=None):
        return self._transactions_by_account.get(external_account_id, [])


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email="bank-connections-test@example.com", password="x", name="Bank Connections Test"
    )


@pytest.fixture
def client(user):
    api_client = APIClient()
    api_client.force_authenticate(user=user)
    return api_client


def _patch_connector(monkeypatch, connector):
    monkeypatch.setattr(bank_connections_view_module, "get_connector", lambda slug: connector)


# ============================================================================
# POST /bank-connections/ (initiate)
# ============================================================================


def test_initiate_creates_pending_connection_and_returns_authorize_url(client, user, monkeypatch):
    _patch_connector(monkeypatch, _FakeConnector())

    response = client.post("/bank-connections/", {"provider_slug": "mock_bank"})

    assert response.status_code == 201
    assert "authorize_url" in response.data
    connection = BankConnection.objects.get(id=response.data["connection_id"])
    assert connection.user == user
    assert connection.provider_slug == "mock_bank"
    assert connection.status == BankConnection.STATUS_PENDING_OTP
    assert connection.oauth_state


def test_initiate_unknown_provider_404s(client, user):
    response = client.post("/bank-connections/", {"provider_slug": "some_other_bank"})
    assert response.status_code == 404


def test_initiate_reuses_existing_connection_row_on_relink(client, user, monkeypatch):
    _patch_connector(monkeypatch, _FakeConnector())

    first = client.post("/bank-connections/", {"provider_slug": "mock_bank"}).data
    second = client.post("/bank-connections/", {"provider_slug": "mock_bank"}).data

    assert first["connection_id"] == second["connection_id"]
    assert BankConnection.objects.filter(user=user, provider_slug="mock_bank").count() == 1


def test_list_connections_scoped_to_current_user(client, user, monkeypatch):
    _patch_connector(monkeypatch, _FakeConnector())
    client.post("/bank-connections/", {"provider_slug": "mock_bank"})

    other_user = User.objects.create_user(email="other@example.com", password="x", name="Other")
    BankConnection.objects.create(user=other_user, provider_slug="mock_bank")

    response = client.get("/bank-connections/")
    assert response.status_code == 200
    assert len(response.data) == 1


# ============================================================================
# POST /bank-connections/{id}/callback/
# ============================================================================


def test_callback_links_connection_and_creates_synced_accounts(
    client, user, monkeypatch, fake_redis
):
    connector = _FakeConnector(
        accounts=[
            {
                "external_account_id": "acc-1",
                "bank_name": "Mock National Bank",
                "account_type": "checking",
                "masked_account_number": "****1234",
                "currency": "EGP",
            }
        ],
        transactions_by_account={
            "acc-1": [
                {
                    "external_transaction_id": "txn-1",
                    "transaction_date": "2026-07-01",
                    "merchant_raw": "Carrefour",
                    "amount": "150.00",
                    "transaction_type": "debit",
                    "currency": "EGP",
                    "balance": "4850.00",
                }
            ]
        },
    )
    _patch_connector(monkeypatch, connector)

    initiate = client.post("/bank-connections/", {"provider_slug": "mock_bank"}).data
    connection = BankConnection.objects.get(id=initiate["connection_id"])

    response = client.post(
        f"/bank-connections/{connection.id}/callback/",
        {"code": "auth-code-1", "state": connection.oauth_state},
    )

    assert response.status_code == 200
    connection.refresh_from_db()
    assert connection.status == BankConnection.STATUS_LINKED
    assert connection.access_token == "fake-access-token"
    assert connection.external_customer_id == "cust-1"
    assert connection.oauth_state is None
    assert connection.linked_at is not None

    account = BankAccount.objects.get(connection=connection, external_account_id="acc-1")
    assert account.link_type == BankAccount.LINK_TYPE_SYNCED
    assert account.user == user
    assert account.is_synced is True

    # Initial backfill landed via the same task the ongoing webhook uses.
    transaction = Transaction.objects.get(account=account)
    assert transaction.source == "synced"
    assert transaction.merchant_raw == "Carrefour"
    assert str(transaction.amount) == "150.00"


def test_callback_rejects_state_mismatch(client, user, monkeypatch):
    _patch_connector(monkeypatch, _FakeConnector())
    initiate = client.post("/bank-connections/", {"provider_slug": "mock_bank"}).data
    connection = BankConnection.objects.get(id=initiate["connection_id"])

    response = client.post(
        f"/bank-connections/{connection.id}/callback/",
        {"code": "auth-code-1", "state": "wrong-state"},
    )

    assert response.status_code == 422
    assert response.data["error"]["code"] == "invalid_oauth_state"
    connection.refresh_from_db()
    assert connection.status == BankConnection.STATUS_PENDING_OTP


def test_callback_marks_connection_failed_on_connector_error(client, user, monkeypatch):
    connector = _FakeConnector(exchange_error=BankConnectorError("token exchange failed"))
    _patch_connector(monkeypatch, connector)
    initiate = client.post("/bank-connections/", {"provider_slug": "mock_bank"}).data
    connection = BankConnection.objects.get(id=initiate["connection_id"])

    response = client.post(
        f"/bank-connections/{connection.id}/callback/",
        {"code": "bad-code", "state": connection.oauth_state},
    )

    assert response.status_code == 422
    assert response.data["error"]["code"] == "bank_connection_failed"
    connection.refresh_from_db()
    assert connection.status == BankConnection.STATUS_FAILED
    assert connection.error_reason
