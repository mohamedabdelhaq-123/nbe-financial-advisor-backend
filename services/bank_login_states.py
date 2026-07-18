"""
Short-lived, single-use OAuth `state` values for the bank-login flow
(core/views/auth.py's BankLoginInitiateView/BankLoginCallbackView).

There's no User yet at initiate time to persist a BankConnection(oauth_state=)
row against — the whole point of the flow is that identity isn't known until
the callback resolves it — so this can't be a DB row the way the existing
authenticated "link a bank" flow's oauth_state is. Same shape as
services/sse_tickets.py (mint/redeem via Redis SET.../GETDEL) for the same
underlying reason: single-use semantics need server-side state either way,
and Redis is already mandatory infra here.
"""

import secrets

import redis
from django.conf import settings

_redis_client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)


def _key(state: str) -> str:
    return f"bank-login-state:{state}"


def mint_state(provider_slug: str) -> str:
    """Starts a bank-login attempt, returning the `state` value to embed in
    the authorize_url — redeem_state() looks up provider_slug from it later,
    so the callback doesn't have to (and shouldn't) trust a client-supplied
    provider_slug on its own."""
    state = secrets.token_urlsafe(32)
    _redis_client.set(_key(state), provider_slug, ex=settings.BANK_LOGIN_STATE_TTL_SECONDS)
    return state


def redeem_state(state: str) -> str | None:
    """Atomic get-and-delete — a state is valid for exactly one redemption.
    Returns the provider_slug it was minted for, or None if unknown/expired/
    already redeemed."""
    return _redis_client.getdel(_key(state))
