"""In-memory, process-lifetime storage for OAuth challenges, codes, and tokens.

This service owns no ledger/customer data and has no database. Everything
here is short-lived by design (challenges/codes expire in seconds-to-minutes)
and resetting on restart is acceptable — a client that loses an in-flight
login simply starts over at /authorize.

Not safe for multi-process deployment (e.g. multiple uvicorn workers) since
state isn't shared across processes. Fine for this mock; if that ever
becomes a real constraint, swap this module's dicts for Redis.
"""

import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from authlib.common.security import generate_token

from app.config import (
    AUTH_CODE_TTL_SECONDS,
    CHALLENGE_TTL_SECONDS,
    OTP_TTL_SECONDS,
)

_lock = threading.Lock()


@dataclass
class Challenge:
    challenge_id: str
    client_id: str
    redirect_uri: str
    state: Optional[str]
    scope: Optional[str]
    created_at: float
    expires_at: float
    # Populated once /login/start successfully resolves the customer.
    customer_id: Optional[str] = None
    email: Optional[str] = None
    otp: Optional[str] = None
    otp_expires_at: Optional[float] = None

    def is_expired(self, now: Optional[float] = None) -> bool:
        return (now if now is not None else time.time()) > self.expires_at

    def otp_is_expired(self, now: Optional[float] = None) -> bool:
        if self.otp_expires_at is None:
            return True
        return (now if now is not None else time.time()) > self.otp_expires_at


@dataclass
class AuthorizationCode:
    code: str
    client_id: str
    redirect_uri: str
    customer_id: str
    created_at: float
    expires_at: float
    used: bool = False

    def is_expired(self, now: Optional[float] = None) -> bool:
        return (now if now is not None else time.time()) > self.expires_at


@dataclass
class RefreshToken:
    token: str
    client_id: str
    customer_id: str
    created_at: float = field(default_factory=time.time)


_challenges: dict[str, Challenge] = {}
_auth_codes: dict[str, AuthorizationCode] = {}
_refresh_tokens: dict[str, RefreshToken] = {}


def create_challenge(
    client_id: str, redirect_uri: str, state: Optional[str], scope: Optional[str]
) -> Challenge:
    """Starts a new login attempt, recording the incoming /authorize params
    so /login/start and /login/verify can look them up by challenge_id."""
    now = time.time()
    challenge = Challenge(
        challenge_id=str(uuid.uuid4()),
        client_id=client_id,
        redirect_uri=redirect_uri,
        state=state,
        scope=scope,
        created_at=now,
        expires_at=now + CHALLENGE_TTL_SECONDS,
    )
    with _lock:
        _challenges[challenge.challenge_id] = challenge
    return challenge


def get_challenge(challenge_id: str) -> Optional[Challenge]:
    """Looks up a challenge, evicting and returning None if it's expired."""
    with _lock:
        challenge = _challenges.get(challenge_id)
        if challenge is None:
            return None
        if challenge.is_expired():
            del _challenges[challenge_id]
            return None
        return challenge


def set_challenge_otp(challenge_id: str, customer_id: str, email: str) -> str:
    """Generate and attach an OTP to an existing challenge; returns the OTP."""
    otp = f"{secrets.randbelow(1_000_000):06d}"
    with _lock:
        challenge = _challenges.get(challenge_id)
        if challenge is None:
            raise KeyError(challenge_id)
        challenge.customer_id = customer_id
        challenge.email = email
        challenge.otp = otp
        challenge.otp_expires_at = time.time() + OTP_TTL_SECONDS
    return otp


def pop_challenge(challenge_id: str) -> Optional[Challenge]:
    """Remove and return a challenge (used once login/verify succeeds)."""
    with _lock:
        return _challenges.pop(challenge_id, None)


def delete_challenge(challenge_id: str) -> None:
    """Discards a challenge (e.g. after a failed lookup) without returning it."""
    with _lock:
        _challenges.pop(challenge_id, None)


def create_authorization_code(
    client_id: str, redirect_uri: str, customer_id: str
) -> AuthorizationCode:
    """Mints a short-lived, single-use OAuth2 authorization code for a
    customer who just passed OTP verification."""
    now = time.time()
    code = AuthorizationCode(
        code=generate_token(48),
        client_id=client_id,
        redirect_uri=redirect_uri,
        customer_id=customer_id,
        created_at=now,
        expires_at=now + AUTH_CODE_TTL_SECONDS,
    )
    with _lock:
        _auth_codes[code.code] = code
    return code


def get_valid_authorization_code(code: str) -> Optional[AuthorizationCode]:
    """Return the code record only if it exists, is unused, and unexpired."""
    with _lock:
        record = _auth_codes.get(code)
        if record is None:
            return None
        if record.used or record.is_expired():
            return None
        return record


def mark_authorization_code_used(code: str) -> None:
    """Flags a code as consumed so a replay is rejected by
    get_valid_authorization_code()."""
    with _lock:
        record = _auth_codes.get(code)
        if record is not None:
            record.used = True


def create_refresh_token(client_id: str, customer_id: str) -> str:
    """Issues an opaque (non-JWT) refresh token alongside an access token."""
    token = generate_token(48)
    with _lock:
        _refresh_tokens[token] = RefreshToken(
            token=token, client_id=client_id, customer_id=customer_id
        )
    return token
