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

from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed as DRFAuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed

from core.models import AdminUser, User
from services import sse_tickets


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


class SSETicketAuthentication(BaseAuthentication):
    """
    Used only by GET /events/stream (core/views/events.py) — a native
    EventSource can't set an Authorization header, and the access token is
    never cookie-based (module docstring above), so this route authenticates
    via a short-TTL, single-use ticket minted by POST /events/ticket under
    normal UserJWTAuthentication instead (services/sse_tickets.py).
    """

    def authenticate(self, request):
        ticket = request.query_params.get("ticket")
        if not ticket:
            return None
        user_id = sse_tickets.redeem_ticket(ticket)
        if user_id is None:
            raise DRFAuthenticationFailed("Invalid or expired ticket.", code="invalid_ticket")
        try:
            return (User.objects.get(id=user_id), None)
        except User.DoesNotExist as exc:
            raise DRFAuthenticationFailed("User not found.", code="user_not_found") from exc

    def authenticate_header(self, request):
        # Without this, DRF has no WWW-Authenticate challenge to offer and
        # downgrades AuthenticationFailed from 401 to 403 (APIView.handle_exception) —
        # this keeps ticket failures consistent with the 401s
        # UserJWTAuthentication/AdminJWTAuthentication already return elsewhere.
        return "Ticket"
