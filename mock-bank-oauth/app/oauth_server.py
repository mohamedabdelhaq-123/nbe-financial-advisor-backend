"""RFC 6749 authorization-code grant semantics, hand-rolled on FastAPI.

See README.md ("Authlib usage") for why this isn't wired through Authlib's
`AuthorizationServer`/grant-class machinery: those ship a Flask/Django
request-response adapter, not a FastAPI/Starlette one, and hand-rolling that
adapter layer to satisfy Authlib's abstract server was judged more fiddly
than it was worth for a mock service. Instead, this module implements the
authorization-code flow's validation/issuance steps directly, while still
using Authlib's primitives for the security-sensitive bits: token generation
(`authlib.common.security.generate_token`, used in store.py) and JWS signing
(`authlib.jose.jwt`, used below).
"""

import time
from typing import Optional

from authlib.jose import jwt

from app.config import ACCESS_TOKEN_TTL_SECONDS, MOCK_BANK_JWT_SECRET, OAUTH_CLIENT_ID


class OAuthError(Exception):
    """Raised for any RFC 6749 §5.2 token-endpoint error condition.

    Carries the standard error code (`invalid_request`, `invalid_client`,
    `invalid_grant`, `unsupported_grant_type`) so routes_token.py can return
    the conventional `{"error": ...}` JSON body with a 400 status.
    """

    def __init__(self, error: str, description: Optional[str] = None):
        self.error = error
        self.description = description
        super().__init__(error)


def validate_client(client_id: str, client_secret: str, expected_secret: str) -> None:
    """Raises OAuthError("invalid_client", ...) unless both the client_id
    and client_secret match the configured values."""
    if client_id != OAUTH_CLIENT_ID or client_secret != expected_secret:
        raise OAuthError("invalid_client", "client_id or client_secret is incorrect")


def issue_access_token(customer_id: str) -> str:
    """Return a signed HS256 JWT access token for the given customer_id.

    Verified independently by mock-bank-sync using the same
    MOCK_BANK_JWT_SECRET — this service does not verify its own tokens.
    """
    header = {"alg": "HS256"}
    now = int(time.time())
    payload = {
        "sub": customer_id,
        "provider": "mock_bank",
        "iat": now,
        "exp": now + ACCESS_TOKEN_TTL_SECONDS,
    }
    token_bytes = jwt.encode(header, payload, MOCK_BANK_JWT_SECRET)
    return token_bytes.decode("utf-8")
