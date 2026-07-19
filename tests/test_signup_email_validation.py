"""
Tests for SignupSerializer.validate_email / core/validators.py's
validate_signup_email(). The MX/DNS deliverability branch is disabled
project-wide in tests (tests/conftest.py's _no_signup_dns_check autouse
fixture), so it's exercised directly here rather than through a live-network
view test.
"""

from rest_framework.test import APIClient

from core.models import User
from core.validators import validate_signup_email


def _client():
    return APIClient()


class TestSignupEmailSyntax:
    def test_malformed_email_is_rejected(self, db):
        resp = _client().post(
            "/auth/signup/",
            {"email": "not-an-email", "password": "a-real-password", "name": "New User"},
            format="json",
        )
        assert resp.status_code == 422
        assert not User.objects.filter(name="New User").exists()

    def test_valid_email_still_succeeds(self, db):
        resp = _client().post(
            "/auth/signup/",
            {"email": "valid-signup@example.com", "password": "a-real-password", "name": "X"},
            format="json",
        )
        assert resp.status_code == 201
        assert User.objects.filter(email="valid-signup@example.com").exists()


class TestSignupEmailDeliverability:
    def test_domain_with_no_mx_records_is_rejected(self, settings, monkeypatch):
        settings.SIGNUP_EMAIL_DNS_CHECK = True

        def _no_mx(*args, **kwargs):
            from email_validator import EmailNotValidError

            raise EmailNotValidError(
                "The domain name this-domain-does-not-exist.invalid does not exist."
            )

        monkeypatch.setattr("core.validators._validate_email", _no_mx)

        from rest_framework.exceptions import ValidationError

        try:
            validate_signup_email("user@this-domain-does-not-exist.invalid")
        except ValidationError:
            pass
        else:
            raise AssertionError("expected ValidationError for an undeliverable domain")

    def test_deliverable_domain_passes_through(self, settings, monkeypatch):
        settings.SIGNUP_EMAIL_DNS_CHECK = True

        class _Result:
            normalized = "user@example.com"

        monkeypatch.setattr("core.validators._validate_email", lambda *a, **kw: _Result())

        assert validate_signup_email("user@example.com") == "user@example.com"
