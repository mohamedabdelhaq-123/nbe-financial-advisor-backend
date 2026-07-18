"""
BankConnector — one adapter per bank/provider, so the rest of the backend
never needs to know which bank it's talking to (see get_connector() in
services/bank_connectors/__init__.py).

None of these methods take or see a login credential of any kind (no email,
no customer id, no password/OTP). Credential collection happens entirely
inside each provider's own redirect-hosted OAuth+OTP flow — for mock_bank,
that's the mock-bank-oauth service — never through this interface. That's
what keeps this interface stable across banks with different login/MFA
shapes: swapping mock_bank for a real bank later means registering a new
BankConnector subclass under a new slug, not changing this contract.
"""

from abc import ABC, abstractmethod


class BankConnectorError(Exception):
    """Raised for any connector-side request/timeout/HTTP-status failure —
    mirrors AIServiceError's role in services/ai_service.py: callers catch
    this one type instead of requests' own exception hierarchy."""


class BankConnector(ABC):
    slug: str

    @abstractmethod
    def get_authorize_url(self, state: str, redirect_uri: str) -> str:
        """Build the URL the frontend redirects the user's browser to, to
        start this provider's login+consent flow. No network call — pure
        URL construction."""

    @abstractmethod
    def exchange_code_for_token(self, code: str) -> dict:
        """Redeem an authorization code for an access token, after the
        provider's own redirect has handed one back to us. Returns
        {"access_token", "refresh_token", "expires_in", "external_customer_id",
        "email", "name"} — email/name back a first-time bank login's User
        provisioning (core/views/auth.py's BankLoginCallbackView)."""

    @abstractmethod
    def fetch_accounts(self, access_token: str) -> list[dict]:
        """Returns a list of {"external_account_id", "bank_name",
        "account_type", "masked_account_number", "currency"}."""

    @abstractmethod
    def fetch_transactions(
        self, access_token: str, external_account_id: str, since=None
    ) -> list[dict]:
        """Returns a list of {"external_transaction_id", "transaction_date",
        "merchant_raw", "amount", "transaction_type", "currency", "balance"}."""
