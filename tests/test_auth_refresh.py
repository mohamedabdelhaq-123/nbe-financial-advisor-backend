"""Regression tests for end-user refresh-cookie session renewal."""

import pytest
from rest_framework.test import APIClient

from core.models import User


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email="refresh-test@example.com",
        password="correct-horse",
        name="Refresh Test",
    )


@pytest.fixture
def client():
    return APIClient()


def _login(client):
    return client.post(
        "/auth/login/",
        {"email": "refresh-test@example.com", "password": "correct-horse"},
        format="json",
    )


class TestRefresh:
    def test_refresh_with_no_cookie_is_rejected(self, client):
        response = client.post("/auth/refresh/")

        assert response.status_code == 401

    def test_refresh_rotates_cookie_and_returns_new_access_token(self, client, user):
        login_response = _login(client)
        old_access = login_response.data["access_token"]
        old_refresh = client.cookies["refresh_token"].value

        refresh_response = client.post("/auth/refresh/")

        assert refresh_response.status_code == 200
        assert refresh_response.data["access_token"] != old_access
        assert client.cookies["refresh_token"].value != old_refresh

    def test_expired_or_invalid_bearer_does_not_block_valid_refresh_cookie(self, client, user):
        login_response = _login(client)
        assert login_response.status_code == 200

        # This reproduces the browser state at access-token expiry: the stale
        # bearer may still be in memory, while the rotating refresh cookie is
        # valid and should be the only credential considered by this endpoint.
        client.credentials(HTTP_AUTHORIZATION="Bearer expired-access-token")
        refresh_response = client.post("/auth/refresh/")

        assert refresh_response.status_code == 200
        assert refresh_response.data["access_token"]


class TestSessionEntry:
    def test_stale_bearer_does_not_block_login(self, client, user):
        client.credentials(HTTP_AUTHORIZATION="Bearer expired-access-token")

        response = _login(client)

        assert response.status_code == 200
        assert response.data["access_token"]
