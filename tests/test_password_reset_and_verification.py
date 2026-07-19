"""
Endpoint-level tests for password reset / email verification
(core/views/auth.py) — PLAN.md Checkpoint 5. Scoped to local (email+password)
core.User accounts only, never AdminUser or bank-linked accounts.

Django's test environment already swaps EMAIL_BACKEND for the locmem backend
(see tests/test_notification_service.py's module docstring), so emails are
asserted against django.core.mail.outbox directly.

Confirm endpoints take an opaque `t` link ticket (services/link_tickets.py)
rather than `user_id`/`token` directly — _extract_ticket pulls it out of the
emailed link the same way the frontend does (a plain query-string read, no
decoding), and _mint_ticket lets a test mint one directly for cases that
need to construct a link ticket without going through a real email send.
"""

from urllib.parse import parse_qs, urlparse

from django.conf import settings
from django.core import mail
from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from core.auth_tokens import email_verification_token_generator, password_reset_token_generator
from core.models import User
from services import link_tickets


def _client():
    return APIClient()


def _extract_ticket(email_body: str) -> str:
    """Pulls `t` out of the one link a verification/reset email body
    contains — same shape as the frontend's own `searchParams.get("t")`."""
    for line in email_body.splitlines():
        if f"{settings.FRONTEND_URL}/" in line and "?t=" in line:
            return parse_qs(urlparse(line.strip()).query)["t"][0]
    raise AssertionError(f"No link with a t= ticket found in email body:\n{email_body}")


def _mint_ticket(user, token: str) -> str:
    return link_tickets.mint_link_ticket(user.id, token, settings.PASSWORD_RESET_TIMEOUT)


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
        assert f"{settings.FRONTEND_URL}/verify-email?t=" in mail.outbox[0].body
        # user_id/token never appear in the link — only the opaque ticket does.
        assert str(user.id) not in mail.outbox[0].body

    def test_verification_email_has_an_html_alternative(self, db):
        resp = _client().post(
            "/auth/signup/",
            {"email": "html-signup@example.com", "password": "a-real-password", "name": "New User"},
            format="json",
        )
        assert resp.status_code == 201
        message = mail.outbox[0]
        assert len(message.alternatives) == 1
        html_body, mimetype = message.alternatives[0]
        assert mimetype == "text/html"
        assert "Verify your email" in html_body
        assert "href=" in html_body


class TestPasswordResetRequest:
    def test_existing_email_gets_a_reset_email_and_a_202(self, db):
        User.objects.create_user(email="reset-me@example.com", password="old-password", name="X")

        resp = _client().post(
            "/auth/password-reset/request/", {"email": "reset-me@example.com"}, format="json"
        )
        assert resp.status_code == 202
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ["reset-me@example.com"]
        assert f"{settings.FRONTEND_URL}/reset-password?t=" in mail.outbox[0].body

    def test_nonexistent_email_still_returns_202_but_sends_nothing(self, db):
        resp = _client().post(
            "/auth/password-reset/request/", {"email": "nobody@example.com"}, format="json"
        )
        assert resp.status_code == 202
        assert len(mail.outbox) == 0


class TestPasswordResetConfirm:
    def test_valid_ticket_changes_password_and_blacklists_old_refresh_tokens(self, db):
        user = User.objects.create_user(
            email="confirm-reset@example.com", password="old-password", name="X"
        )
        old_refresh = RefreshToken.for_user(user)
        assert OutstandingToken.objects.filter(user=user).exists()

        _client().post(
            "/auth/password-reset/request/", {"email": "confirm-reset@example.com"}, format="json"
        )
        ticket = _extract_ticket(mail.outbox[0].body)

        resp = _client().post(
            "/auth/password-reset/confirm/",
            {"t": ticket, "new_password": "brand-new-password"},
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

    def test_used_ticket_cannot_be_replayed(self, db):
        user = User.objects.create_user(
            email="replay-reset@example.com", password="old-password", name="X"
        )
        token = password_reset_token_generator.make_token(user)
        ticket = _mint_ticket(user, token)

        first = _client().post(
            "/auth/password-reset/confirm/",
            {"t": ticket, "new_password": "first-new-password"},
            format="json",
        )
        assert first.status_code == 200

        second = _client().post(
            "/auth/password-reset/confirm/",
            {"t": ticket, "new_password": "second-new-password"},
            format="json",
        )
        assert second.status_code == 422

    def test_garbage_underlying_token_is_rejected(self, db):
        user = User.objects.create_user(
            email="garbage-token@example.com", password="old-password", name="X"
        )
        ticket = _mint_ticket(user, "not-a-real-token")
        resp = _client().post(
            "/auth/password-reset/confirm/",
            {"t": ticket, "new_password": "whatever123"},
            format="json",
        )
        assert resp.status_code == 422

    def test_unknown_user_id_is_rejected(self, db):
        import uuid

        ticket = link_tickets.mint_link_ticket(
            uuid.uuid4(), "irrelevant-token", settings.PASSWORD_RESET_TIMEOUT
        )
        resp = _client().post(
            "/auth/password-reset/confirm/",
            {"t": ticket, "new_password": "whatever123"},
            format="json",
        )
        assert resp.status_code == 422

    def test_unknown_ticket_is_rejected(self, db):
        resp = _client().post(
            "/auth/password-reset/confirm/",
            {"t": "not-a-real-ticket", "new_password": "whatever123"},
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
        assert f"{settings.FRONTEND_URL}/verify-email?t=" in mail.outbox[0].body

    def test_requires_authentication(self, db):
        resp = _client().post("/auth/verify-email/request/")
        assert resp.status_code == 401


class TestEmailVerificationConfirm:
    def test_valid_ticket_sets_email_verified(self, db):
        user = User.objects.create_user(email="confirm-verify@example.com", password="x", name="X")
        assert user.email_verified is False
        token = email_verification_token_generator.make_token(user)
        ticket = _mint_ticket(user, token)

        resp = _client().post(
            "/auth/verify-email/confirm/",
            {"t": ticket},
            format="json",
        )
        assert resp.status_code == 200
        user.refresh_from_db()
        assert user.email_verified is True

    def test_used_ticket_cannot_be_replayed(self, db):
        user = User.objects.create_user(email="replay-verify@example.com", password="x", name="X")
        token = email_verification_token_generator.make_token(user)
        ticket = _mint_ticket(user, token)

        first = _client().post(
            "/auth/verify-email/confirm/",
            {"t": ticket},
            format="json",
        )
        assert first.status_code == 200

        second = _client().post(
            "/auth/verify-email/confirm/",
            {"t": ticket},
            format="json",
        )
        assert second.status_code == 422

    def test_garbage_underlying_token_is_rejected(self, db):
        user = User.objects.create_user(email="verify-garbage@example.com", password="x", name="X")
        ticket = _mint_ticket(user, "not-a-real-token")
        resp = _client().post(
            "/auth/verify-email/confirm/",
            {"t": ticket},
            format="json",
        )
        assert resp.status_code == 422

    def test_unknown_ticket_is_rejected(self, db):
        resp = _client().post(
            "/auth/verify-email/confirm/",
            {"t": "not-a-real-ticket"},
            format="json",
        )
        assert resp.status_code == 422
