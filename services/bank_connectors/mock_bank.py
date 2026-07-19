"""
Client for the mock-bank-oauth (login/OTP/token issuance) and mock-bank-sync
(ledger — accounts/transactions) services, registered under the "mock_bank"
slug. Same request/error-handling shape as services/ai_service.py's
_session/_post/_describe helpers, but with no USE_MOCK_*/real toggle: this
connector *is* the mock for now. A real bank later is a second BankConnector
subclass under a new slug (see services/bank_connectors/__init__.py), not a
flag flip on this one.
"""

from urllib.parse import urlencode

import requests
from django.conf import settings

from .base import BankConnector, BankConnectorError

_session = requests.Session()
_REQUEST_TIMEOUT_SECONDS = 15


def _describe(exc: requests.exceptions.RequestException) -> str:
    """Surfaces the mock service's own {"detail"} or {"error"} body when
    available, same reasoning as services/ai_service.py's _describe()."""
    response = getattr(exc, "response", None)
    if response is None:
        return str(exc)
    try:
        body = response.json()
        detail = body.get("detail") or body.get("error")
    except (ValueError, AttributeError):
        detail = None
    return f"{exc} — {detail}" if detail else str(exc)


def _request(method: str, url: str, **kwargs) -> dict:
    """Shared real-HTTP-call helper — applies a timeout and normalizes any
    failure into BankConnectorError, so callers have one thing to catch."""
    resp = None
    try:
        resp = _session.request(method, url, timeout=_REQUEST_TIMEOUT_SECONDS, **kwargs)
        resp.raise_for_status()
    except requests.exceptions.RequestException as exc:
        raise BankConnectorError(
            f"mock_bank connector call to {url} failed: {_describe(exc)}"
        ) from exc
    try:
        return resp.json()
    except ValueError as exc:
        raise BankConnectorError(
            f"mock_bank connector response from {url} was not valid JSON: {exc}"
        ) from exc


class MockBankConnector(BankConnector):
    slug = "mock_bank"

    def get_authorize_url(self, state: str, redirect_uri: str) -> str:
        # Pure URL construction — the actual login/OTP round trip happens
        # entirely within mock-bank-oauth once the browser is sent here.
        # MOCK_BANK_OAUTH_PUBLIC_URL, not MOCK_BANK_OAUTH_SERVICE_URL: this
        # URL is handed to the frontend for the *browser* to navigate to
        # directly, not called server-to-server, so it needs a host-reachable
        # address rather than the Docker-internal service name.
        params = urlencode(
            {
                "client_id": settings.MOCK_BANK_OAUTH_CLIENT_ID,
                "redirect_uri": redirect_uri,
                "state": state,
                "response_type": "code",
            }
        )
        return f"{settings.MOCK_BANK_OAUTH_PUBLIC_URL}/authorize?{params}"

    def exchange_code_for_token(self, code: str) -> dict:
        data = _request(
            "POST",
            f"{settings.MOCK_BANK_OAUTH_SERVICE_URL}/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.MOCK_BANK_OAUTH_REDIRECT_URI,
                "client_id": settings.MOCK_BANK_OAUTH_CLIENT_ID,
                "client_secret": settings.MOCK_BANK_OAUTH_CLIENT_SECRET,
            },
        )
        if not isinstance(data, dict) or not data.get("access_token") or not data.get("email"):
            # No email, no way to provision a User on a first-time bank
            # login (core/views/auth.py's BankLoginCallbackView) — treated
            # as the same failure class as a missing access_token.
            raise BankConnectorError(
                "mock_bank connector's /token response was missing access_token or email."
            )
        return {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token"),
            "expires_in": data.get("expires_in"),
            # mock-bank-oauth embeds this as the JWT's `sub` claim, but the
            # backend never decodes the token itself — mock-bank-sync does
            # that. The token endpoint's own response is the one place this
            # id is available to us as plain JSON.
            "external_customer_id": data.get("external_customer_id"),
            "email": data["email"],
            "name": data.get("name"),
        }

    def fetch_accounts(self, access_token: str) -> list[dict]:
        return _request(
            "GET",
            f"{settings.MOCK_BANK_SYNC_SERVICE_URL}/accounts",
            headers={"Authorization": f"Bearer {access_token}"},
        )

    def fetch_transactions(
        self, access_token: str, external_account_id: str, since=None
    ) -> list[dict]:
        params = {"since": since} if since else {}
        return _request(
            "GET",
            f"{settings.MOCK_BANK_SYNC_SERVICE_URL}/accounts/{external_account_id}/transactions",
            headers={"Authorization": f"Bearer {access_token}"},
            params=params,
        )
