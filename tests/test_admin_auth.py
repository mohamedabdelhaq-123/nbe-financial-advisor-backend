"""Tests for the admin refresh/logout flow (SEC-009)."""

import pytest
from django.contrib.auth.hashers import make_password
from rest_framework.test import APIClient

import core.views.administration as administration_views
from core.models import AdminBlacklistedToken, AdminUser

COOKIE_NAME = "admin_refresh_token"


@pytest.fixture
def admin_user(db):
    return AdminUser.objects.create(
        name="Admin",
        email="admin-auth-test@example.com",
        password_hash=make_password("correct-horse"),
        role="super_admin",
    )


@pytest.fixture
def client():
    return APIClient()


def _login(client, email="admin-auth-test@example.com", password="correct-horse"):
    return client.post("/admin/auth/login/", {"email": email, "password": password}, format="json")


class TestAdminLogin:
    def test_login_sets_httponly_refresh_cookie_and_no_refresh_token_in_body(
        self, client, admin_user
    ):
        response = _login(client)

        assert response.status_code == 200
        assert "refresh_token" not in response.data
        assert response.data["access_token"]
        assert response.data["admin_id"] == str(admin_user.id)
        assert response.data["role"] == "super_admin"

        cookie = response.cookies[COOKIE_NAME]
        assert cookie.value
        assert cookie["httponly"]
        assert cookie["samesite"] == "Lax"

    def test_wrong_password_is_generic_422(self, client, admin_user):
        response = _login(client, password="wrong")
        assert response.status_code == 422

    def test_unknown_email_is_the_same_generic_422(self, client, db):
        response = _login(client, email="nobody@example.com", password="whatever")
        assert response.status_code == 422

    def test_unknown_email_still_checks_a_dummy_hash(self, client, db, monkeypatch):
        checked_hashes = []

        def fake_check_password(password, encoded):
            checked_hashes.append(encoded)
            return False

        monkeypatch.setattr(administration_views, "check_password", fake_check_password)

        response = _login(client, email="nobody@example.com", password="whatever")

        assert response.status_code == 422
        assert checked_hashes == [administration_views._ADMIN_LOGIN_DUMMY_HASH]


class TestAdminRefresh:
    def test_refresh_with_no_cookie_is_rejected(self, client):
        response = client.post("/admin/auth/refresh/")
        assert response.status_code == 401

    def test_refresh_rotates_and_returns_new_access_token(self, client, admin_user):
        login_resp = _login(client)
        old_access = login_resp.data["access_token"]

        refresh_resp = client.post("/admin/auth/refresh/")

        assert refresh_resp.status_code == 200
        assert refresh_resp.data["access_token"] != old_access
        assert refresh_resp.data["admin_id"] == str(admin_user.id)
        assert refresh_resp.data["role"] == "super_admin"
        assert "refresh_token" not in refresh_resp.data
        # A new rotated cookie was set too, not just a new access token.
        assert client.cookies[COOKIE_NAME].value

    def test_expired_or_invalid_bearer_does_not_block_valid_refresh_cookie(
        self, client, admin_user
    ):
        login_resp = _login(client)
        assert login_resp.status_code == 200

        client.credentials(HTTP_AUTHORIZATION="Bearer expired-admin-access-token")
        refresh_resp = client.post("/admin/auth/refresh/")

        assert refresh_resp.status_code == 200
        assert refresh_resp.data["access_token"]

    def test_reusing_a_rotated_away_refresh_token_is_rejected(self, client, admin_user):
        _login(client)
        old_cookie_value = client.cookies[COOKIE_NAME].value

        first_refresh = client.post("/admin/auth/refresh/")
        assert first_refresh.status_code == 200

        # Replay the rotated-away token (stolen cookie / racing tab).
        client.cookies[COOKIE_NAME] = old_cookie_value
        second_refresh = client.post("/admin/auth/refresh/")

        assert second_refresh.status_code == 401
        assert "already been used" in str(second_refresh.data).lower()

    def test_end_user_refresh_token_is_not_accepted_here(self, client, admin_user, db):
        from core.models import User

        User.objects.create_user(email="enduser@example.com", password="x", name="X")
        # Copy a real end-user refresh cookie into the admin cookie slot.
        end_user_login = client.post(
            "/auth/login/", {"email": "enduser@example.com", "password": "x"}, format="json"
        )
        assert end_user_login.status_code == 200
        end_user_refresh_value = client.cookies["refresh_token"].value

        client.cookies[COOKIE_NAME] = end_user_refresh_value
        response = client.post("/admin/auth/refresh/")

        assert response.status_code == 401


class TestAdminLogout:
    def test_logout_requires_authentication(self, client):
        response = client.post("/admin/auth/logout/")
        assert response.status_code == 401

    def test_logout_blacklists_refresh_token_and_clears_cookie(self, client, admin_user):
        login_resp = _login(client)
        access_token = login_resp.data["access_token"]
        refresh_value = client.cookies[COOKIE_NAME].value

        client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")
        logout_resp = client.post("/admin/auth/logout/")

        assert logout_resp.status_code == 204
        import jwt as pyjwt

        payload = pyjwt.decode(refresh_value, options={"verify_signature": False})
        assert AdminBlacklistedToken.objects.filter(jti=payload["jti"]).exists()

        set_cookie_header = logout_resp.cookies[COOKIE_NAME]
        assert set_cookie_header.value == ""

    def test_refresh_after_logout_is_rejected(self, client, admin_user):
        login_resp = _login(client)
        access_token = login_resp.data["access_token"]

        client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")
        client.post("/admin/auth/logout/")

        client.credentials()  # logout doesn't require re-sending the cookie to work
        refresh_resp = client.post("/admin/auth/refresh/")

        assert refresh_resp.status_code == 401

    def test_logout_with_no_cookie_is_idempotent_204(self, client, admin_user):
        login_resp = _login(client)
        access_token = login_resp.data["access_token"]

        client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")
        client.cookies.clear()
        response = client.post("/admin/auth/logout/")

        assert response.status_code == 204

    def test_end_user_access_token_cannot_call_admin_logout(self, client, db):
        from core.models import User

        User.objects.create_user(email="enduser2@example.com", password="x", name="X")
        login_resp = client.post(
            "/auth/login/", {"email": "enduser2@example.com", "password": "x"}, format="json"
        )
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {login_resp.data['access_token']}")

        response = client.post("/admin/auth/logout/")

        assert response.status_code == 401
