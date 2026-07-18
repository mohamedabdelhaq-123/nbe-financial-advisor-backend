"""Two independent auth dependencies:

1. `require_internal_secret` — a shared-secret header check for
   /internal/customers/lookup, called only by the sibling mock-bank-oauth
   service (server-to-server, no end-user token involved).
2. `require_customer` — Bearer JWT verification for /accounts and
   /accounts/{id}/transactions, called by the Django backend using the token
   mock-bank-oauth issued to a logged-in bank customer. Returns the
   customer_id (JWT `sub` claim) so route handlers can scope queries to it.
"""

import time

from authlib.jose import JoseError, JsonWebToken
from fastapi import Header, HTTPException, status

from app import config

# authlib.jose's module-level `jwt` singleton is constructed to accept every
# registered JWS algorithm, including "none" (see authlib.jose.rfc7519,
# `jwt = JsonWebToken(list(JsonWebSignature.ALGORITHMS_REGISTRY.keys()))`) —
# using it directly would let a caller present an unsigned {"alg": "none"}
# token with an arbitrary `sub` claim and have it accepted. This service
# only ever issues/verifies HS256, so restrict decoding to that explicitly.
_jwt = JsonWebToken(["HS256"])


def require_internal_secret(x_internal_secret: str | None = Header(default=None)) -> None:
    """FastAPI dependency: raises 401/403 unless X-Internal-Secret matches
    MOCK_BANK_INTERNAL_SECRET."""
    if not x_internal_secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Internal-Secret header",
        )
    if x_internal_secret != config.internal_secret():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid internal secret",
        )


def require_customer(authorization: str | None = Header(default=None)) -> str:
    """Verify the Bearer JWT issued by mock-bank-oauth and return the
    customer_id it authenticates (the `sub` claim)."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header",
        )
    token = authorization.split(" ", 1)[1].strip()

    try:
        claims = _jwt.decode(
            token, config.jwt_secret(), claims_options={"exp": {"essential": True}}
        )
        # jwt.decode() only verifies the signature; validate() is what
        # actually checks registered claims (exp/nbf/iat) against now, and
        # (with essential=True above) rejects a token that omits exp
        # entirely rather than treating a missing claim as "never expires".
        claims.validate()
    except JoseError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
        ) from exc

    # Belt-and-suspenders explicit expiry check in case a caller's claims
    # object skips validate()'s leeway-based comparison for any reason.
    exp = claims.get("exp")
    if exp is not None and time.time() > float(exp):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
        )

    customer_id = claims.get("sub")
    if not customer_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing 'sub' claim",
        )

    return str(customer_id)
