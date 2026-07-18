"""
Tests for GET /debug/challenges/{id} — see app/routes_debug.py's module
docstring for why this exists (integration tests reading an OTP without a
real email gateway).
"""

import re

from app import config


def test_returns_challenge_state_when_enabled(client):
    authorize_response = client.get(
        "/authorize",
        params={
            "client_id": "test-client",
            "redirect_uri": "http://frontend.test/callback",
            "response_type": "code",
        },
    )
    challenge_id = re.search(r'name="challenge_id" value="([^"]+)"', authorize_response.text).group(
        1
    )

    response = client.get(f"/debug/challenges/{challenge_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["challenge_id"] == challenge_id
    assert body["otp"] is None  # not yet generated — /login/start hasn't run


def test_unknown_challenge_404s(client):
    response = client.get("/debug/challenges/does-not-exist")
    assert response.status_code == 404


def test_404s_when_disabled(client, monkeypatch):
    monkeypatch.setattr(config, "DEBUG_ENDPOINTS_ENABLED", False)
    response = client.get("/debug/challenges/anything")
    assert response.status_code == 404
