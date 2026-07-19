"""
Endpoint-level tests for password reset / email verification
(core/views/auth.py) — PLAN.md Checkpoint 5. Scoped to local (email+password)
core.User accounts only, never AdminUser or bank-linked accounts.

Django's test environment already swaps EMAIL_BACKEND for the locmem backend
(see tests/test_notification_service.py's module docstring), so emails are
asserted against django.core.mail.outbox directly.
"""

from django.conf import settings
from django.core import mail
from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from core.auth_tokens import email_verification_token_generator, password_reset_token_generator
from core.models import User


def _client():
    return APIClient()


class TestSignupSendsVerificationEmail:
    def test_signup_sends_exactly_one_verification_email(self, db):
        resp = _client().post(
            "/auth/signup/",
            {"email": "new-signup@example.com", "password": "a-real-password", "name": "New User"},
            format="json",
        )
        assert resp.status_code == 201
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ["new-signup@example.com"]

        user = User.objects.get(email="new-signup@example.com")
        assert user.email_verified is False
        assert f"{settings.FRONTEND_URL}/verify-email?user_id={user.id}&token=" in (
            mail.outbox[0].body
        )


class TestPasswordResetRequest:
    def test_existing_email_gets_a_reset_email_and_a_202(self, db):
        user = User.objects.create_user(
            email="reset-me@example.com", password="old-password", name="X"
        )

        resp = _client().post(
            "/auth/password-reset/request/", {"email": "reset-me@example.com"}, format="json"
        )
        assert resp.status_code == 202
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ["reset-me@example.com"]
        assert f"{settings.FRONTEND_URL}/reset-password?user_id={user.id}&token=" in (
            mail.outbox[0].body
        )

    def test_nonexistent_email_still_returns_202_but_sends_nothing(self, db):
        resp = _client().post(
            "/auth/password-reset/request/", {"email": "nobody@example.com"}, format="json"
        )
        assert resp.status_code == 202
        assert len(mail.outbox) == 0


class TestPasswordResetConfirm:
    def test_valid_token_changes_password_and_blacklists_old_refresh_tokens(self, db):
        user = User.objects.create_user(
            email="confirm-reset@example.com", password="old-password", name="X"
        )
        old_refresh = RefreshToken.for_user(user)
        assert OutstandingToken.objects.filter(user=user).exists()

        token = password_reset_token_generator.make_token(user)
        resp = _client().post(
            "/auth/password-reset/confirm/",
            {"user_id": str(user.id), "token": token, "new_password": "brand-new-password"},
            format="json",
        )
        assert resp.status_code == 200

        user.refresh_from_db()
        assert user.check_password("brand-new-password")
        assert not user.check_password("old-password")

        outstanding = OutstandingToken.objects.get(jti=old_refresh["jti"])
        assert BlacklistedToken.objects.filter(token=outstanding).exists()

        # Logging in with the OLD password now fails.
        resp = _client().post(
            "/auth/login/",
            {"email": "confirm-reset@example.com", "password": "old-password"},
            format="json",
        )
        assert resp.status_code == 422

        # ... but the new one works.
        resp = _client().post(
            "/auth/login/",
            {"email": "confirm-reset@example.com", "password": "brand-new-password"},
            format="json",
        )
        assert resp.status_code == 200

    def test_used_token_cannot_be_replayed(self, db):
        user = User.objects.create_user(
            email="replay-reset@example.com", password="old-password", name="X"
        )
        token = password_reset_token_generator.make_token(user)

        first = _client().post(
            "/auth/password-reset/confirm/",
            {"user_id": str(user.id), "token": token, "new_password": "first-new-password"},
            format="json",
        )
        assert first.status_code == 200

        second = _client().post(
            "/auth/password-reset/confirm/",
            {"user_id": str(user.id), "token": token, "new_password": "second-new-password"},
            format="json",
        )
        assert second.status_code == 422

    def test_garbage_token_is_rejected(self, db):
        user = User.objects.create_user(
            email="garbage-token@example.com", password="old-password", name="X"
        )
        resp = _client().post(
            "/auth/password-reset/confirm/",
            {"user_id": str(user.id), "token": "not-a-real-token", "new_password": "whatever123"},
            format="json",
        )
        assert resp.status_code == 422

    def test_unknown_user_id_is_rejected(self, db):
        import uuid

        resp = _client().post(
            "/auth/password-reset/confirm/",
            {
                "user_id": str(uuid.uuid4()),
                "token": "irrelevant-token",
                "new_password": "whatever123",
            },
            format="json",
        )
        assert resp.status_code == 422


class TestEmailVerificationRequest:
    def test_authenticated_user_can_request_a_verification_email(self, db):
        user = User.objects.create_user(email="verify-me@example.com", password="x", name="X")
        client = APIClient()
        client.force_authenticate(user=user)

        resp = client.post("/auth/verify-email/request/")
        assert resp.status_code == 202
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ["verify-me@example.com"]
        assert f"{settings.FRONTEND_URL}/verify-email?user_id={user.id}&token=" in (
            mail.outbox[0].body
        )

    def test_requires_authentication(self, db):
        resp = _client().post("/auth/verify-email/request/")
        assert resp.status_code == 401


class TestEmailVerificationConfirm:
    def test_valid_token_sets_email_verified(self, db):
        user = User.objects.create_user(email="confirm-verify@example.com", password="x", name="X")
        assert user.email_verified is False
        token = email_verification_token_generator.make_token(user)

        resp = _client().post(
            "/auth/verify-email/confirm/",
            {"user_id": str(user.id), "token": token},
            format="json",
        )
        assert resp.status_code == 200
        user.refresh_from_db()
        assert user.email_verified is True

    def test_used_token_cannot_be_replayed(self, db):
        user = User.objects.create_user(email="replay-verify@example.com", password="x", name="X")
        token = email_verification_token_generator.make_token(user)

        first = _client().post(
            "/auth/verify-email/confirm/",
            {"user_id": str(user.id), "token": token},
            format="json",
        )
        assert first.status_code == 200

        second = _client().post(
            "/auth/verify-email/confirm/",
            {"user_id": str(user.id), "token": token},
            format="json",
        )
        assert second.status_code == 422

    def test_garbage_token_is_rejected(self, db):
        user = User.objects.create_user(email="verify-garbage@example.com", password="x", name="X")
        resp = _client().post(
            "/auth/verify-email/confirm/",
            {"user_id": str(user.id), "token": "not-a-real-token"},
            format="json",
        )
        assert resp.status_code == 422
