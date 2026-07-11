"""
Tests for the SSE ticket mint/redeem flow (services/sse_tickets.py,
core/authentication.py's SSETicketAuthentication) and the two views wiring
them together (core/views/events.py). Backed by fakeredis (fake_redis
fixture, tests/conftest.py) — no live Redis needed.

EventStreamView's happy-path test only asserts the response is constructed
correctly (200, text/event-stream) without consuming response.streaming_content
— Django's test client never forces evaluation of a StreamingHttpResponse's
generator, so this doesn't hang on the real, effectively-infinite
stream_user_events() generator (see tests/test_event_bus.py for testing
that generator directly).
"""

import pytest
from rest_framework.test import APIClient

from core.models import User
from services import sse_tickets


@pytest.fixture
def user(db):
    return User.objects.create_user(email="sse-test@example.com", password="x", name="SSE Test")


@pytest.fixture
def client():
    return APIClient()


def test_mint_ticket_returns_ticket_and_ttl(client, user, fake_redis, settings):
    client.force_authenticate(user=user)
    response = client.post("/events/ticket/")
    assert response.status_code == 200
    assert response.data["expires_in"] == settings.SSE_TICKET_TTL_SECONDS
    assert fake_redis.ttl(f"sse-ticket:{response.data['ticket']}") > 0


def test_redeem_ticket_is_single_use(user, fake_redis):
    ticket = sse_tickets.mint_ticket(user)
    assert sse_tickets.redeem_ticket(ticket) == str(user.id)
    assert sse_tickets.redeem_ticket(ticket) is None


def test_redeem_ticket_returns_none_for_unknown_ticket(fake_redis):
    assert sse_tickets.redeem_ticket("totally-bogus-ticket") is None


def test_event_stream_rejects_missing_ticket(client, fake_redis):
    response = client.get("/events/stream/")
    assert response.status_code == 401


def test_event_stream_rejects_invalid_ticket(client, fake_redis):
    response = client.get("/events/stream/?ticket=totally-bogus-ticket")
    assert response.status_code == 401


def test_event_stream_rejects_reused_ticket(client, user, fake_redis):
    ticket = sse_tickets.mint_ticket(user)
    first = client.get(f"/events/stream/?ticket={ticket}")
    assert first.status_code == 200

    second = client.get(f"/events/stream/?ticket={ticket}")
    assert second.status_code == 401


def test_event_stream_accepts_valid_ticket(client, user, fake_redis):
    ticket = sse_tickets.mint_ticket(user)
    response = client.get(f"/events/stream/?ticket={ticket}")
    assert response.status_code == 200
    assert response["Content-Type"] == "text/event-stream"
