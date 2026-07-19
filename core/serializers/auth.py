from django.contrib.auth import authenticate
from rest_framework import serializers

from core.models import User
from core.validators import validate_signup_email


class SignupSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = User
        fields = ["id", "name", "email", "password", "phone"]
        read_only_fields = ["id"]
        # DRF auto-generates a UniqueValidator for `email` from the model's
        # unique=True constraint, so a duplicate signup surfaces as a normal
        # 422 validation error (via core/exceptions.py) rather than an
        # unhandled IntegrityError.

    def validate_email(self, value):
        # Syntax (RFC-grounded, stricter than Django's built-in EmailField
        # regex) + MX/DNS deliverability check — see core/validators.py.
        return validate_signup_email(value)

    def create(self, validated_data):
        # UserManager.create_user() calls set_password() internally — never
        # assign validated_data["password"] directly onto the instance, that
        # would store it in plaintext.
        return User.objects.create_user(**validated_data)


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate(self, attrs):
        user = authenticate(
            self.context["request"], username=attrs["email"], password=attrs["password"]
        )
        if user is None:
            # Deliberately generic: doesn't distinguish "no such email" from
            # "wrong password", so a login attempt can't be used to enumerate
            # which emails are registered.
            raise serializers.ValidationError("Invalid email or password.")
        attrs["user"] = user
        return attrs


class TokenPairResponseSerializer(serializers.Serializer):
    """Response shape for signup and login: a short-lived JWT access token
    plus the id of the user it belongs to. No `refresh_token` field here —
    it's set as an httpOnly cookie instead, so it's never readable by
    client-side JavaScript, even via a successful XSS attack."""

    access_token = serializers.CharField()
    user_id = serializers.UUIDField()


class RefreshResponseSerializer(serializers.Serializer):
    """Response shape for POST /auth/refresh: just a new access token. No
    `refresh_token` field (see TokenPairResponseSerializer) and no matching
    request serializer, since the endpoint takes no request body at all —
    the refresh token it rotates comes from the httpOnly cookie."""

    access_token = serializers.CharField()


class BankLoginInitiateSerializer(serializers.Serializer):
    """POST /auth/bank-login/initiate/ request body."""

    provider_slug = serializers.CharField(max_length=50)


class BankLoginInitiateResponseSerializer(serializers.Serializer):
    """POST /auth/bank-login/initiate/ response body — the frontend
    redirects the user's browser to authorize_url to continue the bank's
    OAuth+OTP flow, then submits state (unchanged) alongside the code the
    provider's redirect hands back to POST /auth/bank-login/callback/."""

    state = serializers.CharField()
    authorize_url = serializers.URLField()


class BankLoginCallbackSerializer(serializers.Serializer):
    """POST /auth/bank-login/callback/ request body — the `code`/`state`
    the frontend read off the provider's OAuth redirect."""

    code = serializers.CharField()
    state = serializers.CharField()


class PasswordResetRequestSerializer(serializers.Serializer):
    """POST /auth/password-reset/request body. Always answered with a
    generic 202 regardless of whether `email` matches a real account (same
    enumeration-avoidance reasoning as LoginSerializer's generic error)."""

    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    """POST /auth/password-reset/confirm body — `t` is the opaque link
    ticket from the query string of the link emailed by the request step
    above (services/link_tickets.py), redeemed server-side to the real
    (user_id, token) pair the URL used to carry directly."""

    t = serializers.CharField()
    new_password = serializers.CharField(write_only=True, min_length=8)


class EmailVerificationConfirmSerializer(serializers.Serializer):
    """POST /auth/verify-email/confirm body — `t` is the opaque link ticket
    from the query string of the link emailed on signup (or re-sent via
    POST /auth/verify-email/request), same redemption as
    PasswordResetConfirmSerializer above."""

    t = serializers.CharField()
