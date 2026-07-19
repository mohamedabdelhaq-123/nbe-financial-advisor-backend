"""
Short-lived, single-use opaque tickets standing in for the (user_id, token)
pair in emailed verification/password-reset links (core/views/auth.py's
_verify_email_link/_reset_password_link). Keeps the raw user id and signed
token out of the URL — and therefore out of browser history, referrer
headers, server access logs, and email-client link-preview scanners — in
favor of one random-looking value that only resolves server-side.

Same mint/redeem-via-Redis shape as services/sse_tickets.py and
services/bank_login_states.py, for the same underlying reason: single-use
semantics need server-side state either way, and Redis is already
mandatory infra here.

This is a defense-in-depth layer, not a replacement for the underlying
token: EmailVerificationTokenGenerator/PasswordResetTokenGenerator (see
core/auth_tokens.py) are still what actually gets checked once a ticket is
redeemed — a ticket only ever resolves to a (user_id, token) pair that then
goes through the exact same validation as before this existed.
"""

import json
import secrets

import redis
from django.conf import settings

_redis_client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)


def _key(ticket: str) -> str:
    return f"link-ticket:{ticket}"


def mint_link_ticket(user_id, token: str, ttl_seconds: int) -> str:
    """Mints a ticket for one (user_id, token) pair, valid for exactly one
    redemption within ttl_seconds — callers pass the same window the
    underlying token generator itself honors (settings.PASSWORD_RESET_TIMEOUT
    for both generators, see core/auth_tokens.py), so the ticket never
    outlives the token it stands in for."""
    ticket = secrets.token_urlsafe(32)
    payload = json.dumps({"user_id": str(user_id), "token": token})
    _redis_client.set(_key(ticket), payload, ex=ttl_seconds)
    return ticket


def redeem_link_ticket(ticket: str) -> dict | None:
    """Atomic get-and-delete — a ticket is valid for exactly one redemption.
    Returns {"user_id", "token"}, or None if unknown/expired/already
    redeemed (a mere link-preview GET from an email client's scanner never
    triggers this — it's only called from the confirm POST)."""
    raw = _redis_client.getdel(_key(ticket))
    if raw is None:
        return None
    return json.loads(raw)
