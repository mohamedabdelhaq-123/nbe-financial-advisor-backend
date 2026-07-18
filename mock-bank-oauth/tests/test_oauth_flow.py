"""
End-to-end-ish tests for the OAuth2 authorization-code flow this service
implements by hand (see app/oauth_server.py's module docstring for why).
requests.get/requests.post calls out to the sibling services (mock-bank-sync's
customer lookup, the Django backend's notification endpoint) are monkeypatched
— these tests exercise this service's own logic, not the network.
"""

import re
from unittest.mock import patch

from authlib.jose import jwt


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self.ok = status_code < 400
        self._json_data = json_data or {}

    def json(self):
        return self._json_data


def _extract_challenge_id(html: str) -> str:
    match = re.search(r'name="challenge_id" value="([^"]+)"', html)
    assert match, f"no challenge_id found in response HTML: {html}"
    return match.group(1)


# ============================================================================
# GET /health
# ============================================================================


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ============================================================================
# GET /authorize
# ============================================================================


def test_authorize_serves_login_form(client):
    response = client.get(
        "/authorize",
        params={
            "client_id": "test-client",
            "redirect_uri": "http://frontend.test/callback",
            "response_type": "code",
            "state": "xyz",
        },
    )
    assert response.status_code == 200
    assert "customer_bank_id" in response.text
    assert _extract_challenge_id(response.text)
    assert "frame-ancestors" in response.headers["content-security-policy"]


def test_authorize_rejects_unknown_client_id(client):
    response = client.get(
        "/authorize",
        params={
            "client_id": "some-other-client",
            "redirect_uri": "http://frontend.test/callback",
            "response_type": "code",
        },
    )
    assert response.status_code == 400


def test_authorize_rejects_non_code_response_type(client):
    response = client.get(
        "/authorize",
        params={
            "client_id": "test-client",
            "redirect_uri": "http://frontend.test/callback",
            "response_type": "token",
        },
    )
    assert response.status_code == 400


# ============================================================================
# POST /login/start
# ============================================================================


def _authorize(client, state="xyz"):
    response = client.get(
        "/authorize",
        params={
            "client_id": "test-client",
            "redirect_uri": "http://frontend.test/callback",
            "response_type": "code",
            "state": state,
        },
    )
    return _extract_challenge_id(response.text)


def test_login_start_unknown_challenge_404s(client):
    response = client.post(
        "/login/start", data={"challenge_id": "does-not-exist", "customer_bank_id": "cust-001"}
    )
    assert response.status_code == 404


def test_login_start_unknown_customer_404s(client):
    challenge_id = _authorize(client)
    with patch("app.routes_login.requests.get", return_value=_FakeResponse(status_code=404)):
        response = client.post(
            "/login/start", data={"challenge_id": challenge_id, "customer_bank_id": "cust-001"}
        )
    assert response.status_code == 404

    from app import store

    assert store.get_challenge(challenge_id).otp is None


def test_login_start_generates_otp_and_sends_email(client):
    challenge_id = _authorize(client)
    lookup_response = _FakeResponse(
        json_data={"customer_id": "cust-uuid-1", "email": "customer@example.com"}
    )
    notify_response = _FakeResponse(status_code=202)

    with patch("app.routes_login.requests.get", return_value=lookup_response) as mock_get, patch(
        "app.routes_login.requests.post", return_value=notify_response
    ) as mock_post:
        response = client.post(
            "/login/start", data={"challenge_id": challenge_id, "customer_bank_id": "cust-001"}
        )

    assert response.status_code == 200
    assert "otp" in response.text.lower()
    assert mock_get.call_args.kwargs["params"] == {"customer_bank_id": "cust-001"}
    assert mock_get.call_args.kwargs["headers"]["X-Internal-Secret"] == "test-internal-secret"
    assert mock_post.call_args.kwargs["headers"]["X-Service-Token"] == "test-service-token"
    assert mock_post.call_args.kwargs["json"]["to"] == "customer@example.com"

    from app import store

    challenge = store.get_challenge(challenge_id)
    assert challenge.otp is not None
    assert len(challenge.otp) == 6
    assert challenge.customer_id == "cust-uuid-1"


def test_login_start_returns_502_when_notification_fails(client):
    challenge_id = _authorize(client)
    lookup_response = _FakeResponse(
        json_data={"customer_id": "cust-uuid-1", "email": "customer@example.com"}
    )
    with patch("app.routes_login.requests.get", return_value=lookup_response), patch(
        "app.routes_login.requests.post", return_value=_FakeResponse(status_code=500)
    ):
        response = client.post(
            "/login/start", data={"challenge_id": challenge_id, "customer_bank_id": "cust-001"}
        )
    assert response.status_code == 502


# ============================================================================
# POST /login/verify
# ============================================================================


def _authorize_and_start_login(client, customer_id="cust-uuid-1", email="customer@example.com"):
    challenge_id = _authorize(client)
    with patch(
        "app.routes_login.requests.get",
        return_value=_FakeResponse(json_data={"customer_id": customer_id, "email": email}),
    ), patch("app.routes_login.requests.post", return_value=_FakeResponse(status_code=202)):
        client.post(
            "/login/start", data={"challenge_id": challenge_id, "customer_bank_id": "cust-001"}
        )

    from app import store

    return challenge_id, store.get_challenge(challenge_id).otp


def test_login_verify_wrong_otp_rejected(client):
    challenge_id, _otp = _authorize_and_start_login(client)
    response = client.post("/login/verify", data={"challenge_id": challenge_id, "otp": "000000"})
    assert response.status_code == 400


def test_login_verify_correct_otp_redirects_with_code(client):
    challenge_id, otp = _authorize_and_start_login(client)

    response = client.post(
        "/login/verify",
        data={"challenge_id": challenge_id, "otp": otp},
        follow_redirects=False,
    )

    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("http://frontend.test/callback?")
    assert "code=" in location
    assert "state=xyz" in location


# ============================================================================
# POST /token
# ============================================================================


def _get_authorization_code(client):
    challenge_id, otp = _authorize_and_start_login(client)
    response = client.post(
        "/login/verify",
        data={"challenge_id": challenge_id, "otp": otp},
        follow_redirects=False,
    )
    location = response.headers["location"]
    return re.search(r"code=([^&]+)", location).group(1)


def test_token_issues_jwt_and_external_customer_id(client):
    code = _get_authorization_code(client)

    response = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "http://frontend.test/callback",
            "client_id": "test-client",
            "client_secret": "test-client-secret",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["external_customer_id"] == "cust-uuid-1"
    assert body["refresh_token"]

    claims = jwt.decode(body["access_token"], "test-jwt-secret")
    assert claims["sub"] == "cust-uuid-1"
    assert claims["provider"] == "mock_bank"


def test_token_rejects_wrong_client_secret(client):
    code = _get_authorization_code(client)

    response = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "http://frontend.test/callback",
            "client_id": "test-client",
            "client_secret": "wrong-secret",
        },
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_client"


def test_token_rejects_reused_code(client):
    code = _get_authorization_code(client)
    body = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": "http://frontend.test/callback",
        "client_id": "test-client",
        "client_secret": "test-client-secret",
    }
    first = client.post("/token", data=body)
    assert first.status_code == 200

    second = client.post("/token", data=body)
    assert second.status_code == 400
    assert second.json()["error"] == "invalid_grant"


def test_token_rejects_redirect_uri_mismatch(client):
    code = _get_authorization_code(client)

    response = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "http://attacker.test/callback",
            "client_id": "test-client",
            "client_secret": "test-client-secret",
        },
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"
