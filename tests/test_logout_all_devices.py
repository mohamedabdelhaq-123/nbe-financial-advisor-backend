"""
Endpoint-level tests for POST /auth/logout-all/ (core/views/auth.py's
LogoutAllDevicesView) — "log out of all other devices" on the profile
page's Account Management section. Blacklists every outstanding refresh
token for the user EXCEPT the one behind the calling request's own cookie.
"""

from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from core.models import User


def _client_with_cookie(user, refresh: RefreshToken) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    client.cookies["refresh_token"] = str(refresh)
    return client


class TestLogoutAllDevices:
    def test_blacklists_other_sessions_but_not_the_calling_one(self, db):
        user = User.objects.create_user(email="multi-device@example.com", password="x", name="X")
        this_device = RefreshToken.for_user(user)
        other_device = RefreshToken.for_user(user)

        client = _client_with_cookie(user, this_device)
        resp = client.post("/auth/logout-all/")
        assert resp.status_code == 204

        other_outstanding = OutstandingToken.objects.get(jti=other_device["jti"])
        assert BlacklistedToken.objects.filter(token=other_outstanding).exists()

        this_outstanding = OutstandingToken.objects.get(jti=this_device["jti"])
        assert not BlacklistedToken.objects.filter(token=this_outstanding).exists()

    def test_only_blacklists_the_requesting_users_own_tokens(self, db):
        user = User.objects.create_user(email="me-device@example.com", password="x", name="X")
        other_user = User.objects.create_user(
            email="other-user-device@example.com", password="x", name="Y"
        )
        my_refresh = RefreshToken.for_user(user)
        other_users_refresh = RefreshToken.for_user(other_user)

        client = _client_with_cookie(user, my_refresh)
        resp = client.post("/auth/logout-all/")
        assert resp.status_code == 204

        other_users_outstanding = OutstandingToken.objects.get(jti=other_users_refresh["jti"])
        assert not BlacklistedToken.objects.filter(token=other_users_outstanding).exists()

    def test_no_cookie_still_blacklists_every_session(self, db):
        # No current-device token to identify/exclude — safe fallback is to
        # blacklist everything rather than guess.
        user = User.objects.create_user(email="no-cookie-device@example.com", password="x", name="X")
        device_a = RefreshToken.for_user(user)
        device_b = RefreshToken.for_user(user)

        client = APIClient()
        client.force_authenticate(user=user)
        resp = client.post("/auth/logout-all/")
        assert resp.status_code == 204

        for token in (device_a, device_b):
            outstanding = OutstandingToken.objects.get(jti=token["jti"])
            assert BlacklistedToken.objects.filter(token=outstanding).exists()

    def test_requires_authentication(self, db):
        resp = APIClient().post("/auth/logout-all/")
        assert resp.status_code == 401
