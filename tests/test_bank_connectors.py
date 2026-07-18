"""
Unit tests for services/bank_connectors/ — the registry (get_connector) and
MockBankConnector's request-building, without ever hitting the network. Same
module-level requests.Session monkeypatching convention as
tests/test_ai_service.py's _FakeSession/_FakeResponse.
"""

import pytest
import requests

from services.bank_connectors import BankConnectorError, get_connector
from services.bank_connectors.mock_bank import MockBankConnector


class _FakeResponse:
    def __init__(self, json_data=None, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            error = requests.exceptions.HTTPError(f"{self.status_code} error")
            error.response = self
            raise error

    def json(self):
        return self._json_data


class _FakeSession:
    def __init__(self, response):
        self._response = response
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        return self._response


@pytest.fixture
def connector_settings(settings):
    settings.MOCK_BANK_OAUTH_SERVICE_URL = "http://fake-mock-bank-oauth:8002"
    settings.MOCK_BANK_OAUTH_CLIENT_ID = "test-client-id"
    settings.MOCK_BANK_OAUTH_CLIENT_SECRET = "test-client-secret"
    settings.MOCK_BANK_OAUTH_REDIRECT_URI = "http://frontend.test/bank-connect/callback"
    settings.MOCK_BANK_SYNC_SERVICE_URL = "http://fake-mock-bank-sync:8003"


# ============================================================================
# Registry
# ============================================================================


def test_get_connector_returns_mock_bank_connector():
    connector = get_connector("mock_bank")
    assert isinstance(connector, MockBankConnector)
    assert connector.slug == "mock_bank"


def test_get_connector_raises_for_unknown_provider():
    with pytest.raises(BankConnectorError):
        get_connector("some_other_bank")


# ============================================================================
# get_authorize_url — pure URL construction, no network call
# ============================================================================


def test_get_authorize_url_builds_expected_query(connector_settings):
    connector = MockBankConnector()
    url = connector.get_authorize_url(
        state="abc123", redirect_uri="http://frontend.test/bank-connect/callback"
    )

    assert url.startswith("http://fake-mock-bank-oauth:8002/authorize?")
    assert "client_id=test-client-id" in url
    assert "state=abc123" in url
    assert "response_type=code" in url
    assert "redirect_uri=" in url


# ============================================================================
# exchange_code_for_token
# ============================================================================


def test_exchange_code_for_token_posts_to_token_endpoint(connector_settings, monkeypatch):
    fake = _FakeSession(
        _FakeResponse(
            {
                "access_token": "fake-jwt",
                "refresh_token": "fake-refresh",
                "expires_in": 3600,
                "external_customer_id": "cust-1",
                "email": "customer@example.com",
                "name": "Test Customer",
            }
        )
    )
    monkeypatch.setattr("services.bank_connectors.mock_bank._session", fake)

    connector = MockBankConnector()
    result = connector.exchange_code_for_token("auth-code-1")

    assert fake.calls[0]["method"] == "POST"
    assert fake.calls[0]["url"] == "http://fake-mock-bank-oauth:8002/token"
    assert fake.calls[0]["data"]["grant_type"] == "authorization_code"
    assert fake.calls[0]["data"]["code"] == "auth-code-1"
    assert fake.calls[0]["data"]["client_id"] == "test-client-id"
    assert fake.calls[0]["data"]["client_secret"] == "test-client-secret"
    assert result == {
        "access_token": "fake-jwt",
        "refresh_token": "fake-refresh",
        "expires_in": 3600,
        "external_customer_id": "cust-1",
        "email": "customer@example.com",
        "name": "Test Customer",
    }


def test_exchange_code_for_token_raises_on_http_failure(connector_settings, monkeypatch):
    fake = _FakeSession(_FakeResponse(status_code=400))
    monkeypatch.setattr("services.bank_connectors.mock_bank._session", fake)

    connector = MockBankConnector()
    with pytest.raises(BankConnectorError):
        connector.exchange_code_for_token("bad-code")


def test_exchange_code_for_token_raises_on_malformed_response(connector_settings, monkeypatch):
    fake = _FakeSession(_FakeResponse({"token_type": "bearer"}))  # no access_token
    monkeypatch.setattr("services.bank_connectors.mock_bank._session", fake)

    connector = MockBankConnector()
    with pytest.raises(BankConnectorError):
        connector.exchange_code_for_token("some-code")


# ============================================================================
# fetch_accounts / fetch_transactions
# ============================================================================


def test_fetch_accounts_sends_bearer_token(connector_settings, monkeypatch):
    fake = _FakeSession(_FakeResponse([{"external_account_id": "acc-1", "bank_name": "Mock Bank"}]))
    monkeypatch.setattr("services.bank_connectors.mock_bank._session", fake)

    connector = MockBankConnector()
    result = connector.fetch_accounts("fake-jwt")

    assert fake.calls[0]["method"] == "GET"
    assert fake.calls[0]["url"] == "http://fake-mock-bank-sync:8003/accounts"
    assert fake.calls[0]["headers"]["Authorization"] == "Bearer fake-jwt"
    assert result == [{"external_account_id": "acc-1", "bank_name": "Mock Bank"}]


def test_fetch_transactions_omits_since_when_not_given(connector_settings, monkeypatch):
    fake = _FakeSession(_FakeResponse([]))
    monkeypatch.setattr("services.bank_connectors.mock_bank._session", fake)

    connector = MockBankConnector()
    connector.fetch_transactions("fake-jwt", "acc-1")

    assert fake.calls[0]["url"] == "http://fake-mock-bank-sync:8003/accounts/acc-1/transactions"
    assert fake.calls[0]["params"] == {}


def test_fetch_transactions_includes_since_when_given(connector_settings, monkeypatch):
    fake = _FakeSession(_FakeResponse([]))
    monkeypatch.setattr("services.bank_connectors.mock_bank._session", fake)

    connector = MockBankConnector()
    connector.fetch_transactions("fake-jwt", "acc-1", since="2026-07-01")

    assert fake.calls[0]["params"] == {"since": "2026-07-01"}
