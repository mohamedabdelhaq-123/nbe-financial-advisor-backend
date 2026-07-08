"""
Two JWT authentication classes, one per credential space (API Design
Guidelines §8: "an admin token and a user token are never interchangeable,
and no endpoint accepts either kind of token depending on convenience").

Both core.User and core.AdminUser have a UUID `id`, and simplejwt's
RefreshToken.for_user() only ever reads that field — so nothing stops a
correctly-signed AdminUser token from being looked up against core.User by
the *default* JWTAuthentication (or vice versa), and the two id spaces are
UUIDs, not guaranteed disjoint. Rather than rely on that never happening by
coincidence, admin tokens carry an explicit `is_admin` claim at issuance
(core/views/administration.py's AdminLoginView), and each class below
enforces the boundary in its own direction.
"""

from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed

from core.models import AdminUser


class UserJWTAuthentication(JWTAuthentication):
    """Default authentication for every end-user-facing endpoint (config/settings.py)."""

    def get_user(self, validated_token):
        if validated_token.get("is_admin"):
            raise AuthenticationFailed(
                "Admin tokens cannot be used on user-facing endpoints.", code="wrong_token_type"
            )
        return super().get_user(validated_token)


class AdminJWTAuthentication(JWTAuthentication):
    """Used only by /admin/* views (core/views/administration.py's AdminAuthMixin)."""

    def get_user(self, validated_token):
        if not validated_token.get("is_admin"):
            raise AuthenticationFailed("Not an admin token.", code="wrong_token_type")
        try:
            return AdminUser.objects.get(id=validated_token["user_id"])
        except AdminUser.DoesNotExist as exc:
            raise AuthenticationFailed("Admin user not found.", code="user_not_found") from exc
