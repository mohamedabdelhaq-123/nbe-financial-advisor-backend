"""
Short-lived, single-use auth tickets for GET /events/stream (core/views/events.py,
core/authentication.py's SSETicketAuthentication). Native EventSource can't set an
Authorization header, and this project's access token is never cookie-based
(core/authentication.py's module docstring), so the stream is gated by a
ticket minted just-in-time via POST /events/ticket instead.

Redis-backed rather than a signed token: single-use semantics require
server-side state either way (a replay cache), and Redis is already
mandatory infra for the pub/sub side (services/event_bus.py) — no benefit to
adding JWT-signing complexity on top. GETDEL makes redemption an atomic
get-and-delete in one round trip.
"""

import secrets

import redis
from django.conf import settings

_redis_client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)


def _key(ticket: str) -> str:
    return f"sse-ticket:{ticket}"


def mint_ticket(user) -> str:
    ticket = secrets.token_urlsafe(32)
    _redis_client.setex(_key(ticket), settings.SSE_TICKET_TTL_SECONDS, str(user.id))
    return ticket


def redeem_ticket(ticket: str) -> str | None:
    """Atomic get-and-delete — a ticket is valid for exactly one redemption."""
    return _redis_client.getdel(_key(ticket))
