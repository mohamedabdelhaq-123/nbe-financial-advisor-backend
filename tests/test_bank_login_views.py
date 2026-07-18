"""
Endpoint-level tests for the bank-login flow (core/views/auth.py's
BankLoginInitiateView/BankLoginCallbackView) — a secondary, unauthenticated
sign-in path alongside SignupView/LoginView. Uses a fake BankConnector
(monkeypatched in place of get_connector) rather than a real HTTP call, same
convention as tests/test_bank_connections_views.py for the existing
authenticated link flow.

fake_redis is required for every callback test — BankLoginInitiateView mints
a state via services/bank_login_states.py (Redis-backed), and callback tests
need a real initiate step first to get a state to redeem.
"""

import pytest
from rest_framework.test import APIClient

import core.views.auth as auth_view_module
from core.models import BankAccount, BankConnection, Transaction, User
from services.bank_connectors import BankConnectorError


class _FakeConnector:
    slug = "mock_bank"

    def __init__(
        self,
        accounts=None,
        transactions_by_account=None,
        exchange_error=None,
        fetch_accounts_error=None,
        external_customer_id="cust-1",
        email="bank-customer@example.com",
        name="Bank Customer",
    ):
        self._accounts = accounts if accounts is not None else []
        self._transactions_by_account = transactions_by_account or {}
        self._exchange_error = exchange_error
        self._fetch_accounts_error = fetch_accounts_error
        self._external_customer_id = external_customer_id
        self._email = email
        self._name = name

    def get_authorize_url(self, state, redirect_uri):
        return f"http://fake-mock-bank-oauth/authorize?state={state}"

    def exchange_code_for_token(self, code):
        if self._exchange_error:
            raise self._exchange_error
        return {
            "access_token": "fake-access-token",
            "refresh_token": "fake-refresh-token",
            "expires_in": 3600,
            "external_customer_id": self._external_customer_id,
            "email": self._email,
            "name": self._name,
        }

    def fetch_accounts(self, access_token):
        if self._fetch_accounts_error:
            raise self._fetch_accounts_error
        return self._accounts

    def fetch_transactions(self, access_token, external_account_id, since=None):
        return self._transactions_by_account.get(external_account_id, [])


_DEFAULT_ACCOUNTS = [
    {
        "external_account_id": "acc-1",
        "bank_name": "Mock National Bank",
        "account_type": "checking",
        "masked_account_number": "****1234",
        "currency": "EGP",
    }
]


@pytest.fixture
def client():
    return APIClient()


def _patch_connector(monkeypatch, connector):
    monkeypatch.setattr(auth_view_module, "get_connector", lambda slug: connector)


def _initiate(client, monkeypatch, connector, provider_slug="mock_bank"):
    _patch_connector(monkeypatch, connector)
    return client.post("/auth/bank-login/initiate/", {"provider_slug": provider_slug}).data


# ============================================================================
# POST /auth/bank-login/initiate/
# ============================================================================


def test_initiate_unknown_provider_404s(client):
    response = client.post("/auth/bank-login/initiate/", {"provider_slug": "some_other_bank"})
    assert response.status_code == 404


def test_initiate_returns_state_and_authorize_url(client, monkeypatch, fake_redis):
    data = _initiate(client, monkeypatch, _FakeConnector())
    assert "state" in data
    assert "authorize_url" in data


# ============================================================================
# POST /auth/bank-login/callback/ — first-time login
# ============================================================================


def test_callback_first_login_provisions_user_and_returns_tokens(
    client, monkeypatch, fake_redis, db
):
    connector = _FakeConnector(accounts=_DEFAULT_ACCOUNTS)
    initiate = _initiate(client, monkeypatch, connector)

    response = client.post(
        "/auth/bank-login/callback/", {"code": "auth-code-1", "state": initiate["state"]}
    )

    assert response.status_code == 201
    assert "access_token" in response.data
    user = User.objects.get(id=response.data["user_id"])
    assert user.email == "bank-customer@example.com"
    assert not user.has_usable_password()

    connection = BankConnection.objects.get(
        provider_slug="mock_bank", external_customer_id="cust-1"
    )
    assert connection.user == user
    assert connection.status == BankConnection.STATUS_LINKED

    account = BankAccount.objects.get(connection=connection, external_account_id="acc-1")
    assert account.link_type == BankAccount.LINK_TYPE_SYNCED
    assert account.user == user


def test_callback_state_reuse_rejected(client, monkeypatch, fake_redis, db):
    connector = _FakeConnector(accounts=_DEFAULT_ACCOUNTS)
    initiate = _initiate(client, monkeypatch, connector)

    first = client.post(
        "/auth/bank-login/callback/", {"code": "auth-code-1", "state": initiate["state"]}
    )
    assert first.status_code == 201

    second = client.post(
        "/auth/bank-login/callback/", {"code": "auth-code-2", "state": initiate["state"]}
    )
    assert second.status_code == 422
    assert second.data["error"]["code"] == "invalid_oauth_state"


def test_callback_unknown_state_rejected(client, fake_redis, db):
    response = client.post(
        "/auth/bank-login/callback/", {"code": "auth-code-1", "state": "unknown-state"}
    )
    assert response.status_code == 422
    assert response.data["error"]["code"] == "invalid_oauth_state"


def test_callback_new_connection_exchange_failure_persists_nothing(
    client, monkeypatch, fake_redis, db
):
    connector = _FakeConnector(exchange_error=BankConnectorError("token exchange failed"))
    initiate = _initiate(client, monkeypatch, connector)

    response = client.post(
        "/auth/bank-login/callback/", {"code": "bad-code", "state": initiate["state"]}
    )

    assert response.status_code == 422
    assert response.data["error"]["code"] == "bank_login_failed"
    assert User.objects.count() == 0
    assert BankConnection.objects.count() == 0


def test_callback_new_connection_fetch_accounts_failure_persists_nothing(
    client, monkeypatch, fake_redis, db
):
    connector = _FakeConnector(fetch_accounts_error=BankConnectorError("mock-bank-sync down"))
    initiate = _initiate(client, monkeypatch, connector)

    response = client.post(
        "/auth/bank-login/callback/", {"code": "auth-code-1", "state": initiate["state"]}
    )

    assert response.status_code == 422
    assert response.data["error"]["code"] == "bank_login_failed"
    assert User.objects.count() == 0
    assert BankConnection.objects.count() == 0


# ============================================================================
# POST /auth/bank-login/callback/ — repeat login
# ============================================================================


def test_callback_repeat_login_resolves_to_same_user(client, monkeypatch, fake_redis, db):
    connector = _FakeConnector(accounts=_DEFAULT_ACCOUNTS)
    first_initiate = _initiate(client, monkeypatch, connector)
    first = client.post(
        "/auth/bank-login/callback/", {"code": "auth-code-1", "state": first_initiate["state"]}
    )
    first_user_id = first.data["user_id"]

    second_initiate = _initiate(client, monkeypatch, connector)
    second = client.post(
        "/auth/bank-login/callback/", {"code": "auth-code-2", "state": second_initiate["state"]}
    )

    assert second.status_code == 200
    assert second.data["user_id"] == first_user_id
    assert User.objects.count() == 1
    assert BankConnection.objects.count() == 1


def test_callback_repeat_login_fetch_accounts_failure_still_logs_in(
    client, monkeypatch, fake_redis, db
):
    working_connector = _FakeConnector(accounts=_DEFAULT_ACCOUNTS)
    first_initiate = _initiate(client, monkeypatch, working_connector)
    first = client.post(
        "/auth/bank-login/callback/", {"code": "auth-code-1", "state": first_initiate["state"]}
    )
    first_user_id = first.data["user_id"]

    failing_connector = _FakeConnector(
        fetch_accounts_error=BankConnectorError("mock-bank-sync down")
    )
    second_initiate = _initiate(client, monkeypatch, failing_connector)
    second = client.post(
        "/auth/bank-login/callback/", {"code": "auth-code-2", "state": second_initiate["state"]}
    )

    assert second.status_code == 200
    assert second.data["user_id"] == first_user_id


# ============================================================================
# POST /auth/bank-login/callback/ — email collision / merge
# ============================================================================


def test_callback_email_collision_merges_into_existing_user_and_replaces_manual_data(
    client, monkeypatch, fake_redis, db
):
    existing_user = User.objects.create_user(
        email="bank-customer@example.com", password="x", name="Existing User"
    )
    manual_account = BankAccount.objects.create(
        user=existing_user,
        bank_name="Mock National Bank",
        masked_account_number="****9999",
        link_type=BankAccount.LINK_TYPE_MANUAL,
    )
    Transaction.objects.create(
        user=existing_user,
        account=manual_account,
        source="manual",
        transaction_date="2026-07-01",
        merchant_raw="Old Entry",
        amount="10.00",
    )
    other_manual_account = BankAccount.objects.create(
        user=existing_user,
        bank_name="Some Other Bank",
        masked_account_number="****0001",
        link_type=BankAccount.LINK_TYPE_MANUAL,
    )

    connector = _FakeConnector(accounts=_DEFAULT_ACCOUNTS, email="bank-customer@example.com")
    initiate = _initiate(client, monkeypatch, connector)

    response = client.post(
        "/auth/bank-login/callback/", {"code": "auth-code-1", "state": initiate["state"]}
    )

    assert response.status_code == 201
    assert response.data["user_id"] == str(existing_user.id)
    assert User.objects.count() == 1
    assert not BankAccount.objects.filter(id=manual_account.id).exists()
    assert not Transaction.objects.filter(account_id=manual_account.id).exists()
    assert BankAccount.objects.filter(id=other_manual_account.id).exists()

    synced_account = BankAccount.objects.get(
        user=existing_user, bank_name="Mock National Bank", link_type=BankAccount.LINK_TYPE_SYNCED
    )
    assert synced_account.external_account_id == "acc-1"


# ============================================================================
# POST /auth/bank-login/callback/ — multi-bank
# ============================================================================


def test_callback_login_via_second_bank_keeps_both_connections(client, monkeypatch, fake_redis, db):
    first_connector = _FakeConnector(
        accounts=_DEFAULT_ACCOUNTS, external_customer_id="cust-1", email="multi-bank@example.com"
    )
    first_initiate = _initiate(client, monkeypatch, first_connector, provider_slug="mock_bank")
    first = client.post(
        "/auth/bank-login/callback/", {"code": "auth-code-1", "state": first_initiate["state"]}
    )
    user_id = first.data["user_id"]

    second_accounts = [
        {
            "external_account_id": "acc-2",
            "bank_name": "Second Mock Bank",
            "account_type": "checking",
            "masked_account_number": "****4321",
            "currency": "EGP",
        }
    ]
    second_connector = _FakeConnector(
        accounts=second_accounts, external_customer_id="cust-2", email="multi-bank@example.com"
    )
    second_initiate = _initiate(
        client, monkeypatch, second_connector, provider_slug="second_mock_bank"
    )
    second = client.post(
        "/auth/bank-login/callback/", {"code": "auth-code-2", "state": second_initiate["state"]}
    )

    assert second.status_code == 201
    assert second.data["user_id"] == user_id
    assert User.objects.count() == 1
    assert (
        BankConnection.objects.filter(user_id=user_id, status=BankConnection.STATUS_LINKED).count()
        == 2
    )
