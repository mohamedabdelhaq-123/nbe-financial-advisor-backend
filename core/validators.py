import re

from django.conf import settings
from email_validator import EmailNotValidError
from email_validator import validate_email as _validate_email
from rest_framework import serializers

PHONE_PATTERN = re.compile(r"^\+[1-9]\d{7,14}$")


def validate_phone_format(value: str) -> str:
    """Apply a small, deterministic E.164-shaped format check.

    This deliberately does not claim that the number exists or send a
    verification code. The mock bank supplies the same normalized shape
    when it provisions a bank-login user.
    """
    normalized = value.strip()
    if not PHONE_PATTERN.fullmatch(normalized):
        raise serializers.ValidationError(
            "Enter a phone number in international format, for example +201001234567."
        )
    return normalized


def validate_signup_email(value: str) -> str:
    """RFC-grounded syntax check, plus an MX/DNS deliverability lookup when
    settings.SIGNUP_EMAIL_DNS_CHECK is on. Raises DRF's ValidationError (not
    EmailNotValidError) so it surfaces as a normal field error from a
    serializer's validate_email()."""
    try:
        result = _validate_email(value, check_deliverability=settings.SIGNUP_EMAIL_DNS_CHECK)
    except EmailNotValidError as exc:
        raise serializers.ValidationError(str(exc)) from exc
    return result.normalized
