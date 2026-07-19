from django.conf import settings
from email_validator import EmailNotValidError
from email_validator import validate_email as _validate_email
from rest_framework import serializers


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
