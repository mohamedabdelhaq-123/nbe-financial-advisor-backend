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

import hmac

from django.conf import settings
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


class _SharedSecretAuthentication(BaseAuthentication):
    """
    Base for the machine-to-machine credential space: no User/AdminUser at
    all backs these calls (there's no human on the other end), just a
    static shared secret each caller was configured with out-of-band. Every
    subclass enforces its own boundary via its own env-var-backed secret and
    header name, matching this module's own stated rule — "an admin token
    and a user token are never interchangeable" — extended here to non-JWT
    service callers the same way SSETicketAuthentication already extends it
    to ticket-based ones.

    Unlike SSETicketAuthentication (which returns None — "not applicable" —
    on a missing ticket and relies on its view's default IsAuthenticated
    permission class to still reject the request), this raises on a missing
    header too, not just a wrong one. These views use AllowAny (there's no
    User for IsAuthenticated to check), so authentication itself has to be
    the sole gatekeeper here or a missing header would silently pass through.

    authenticate() returning (None, token) leaves request.user literally
    None, not AnonymousUser — callers on these endpoints never read
    request.user, only the request body (see BankSyncWebhookView), so this
    is never observed.
    """

    header_name = None
    settings_attr = None

    def authenticate(self, request):
        provided = request.META.get(self.header_name)
        expected = getattr(settings, self.settings_attr)
        # constant-time compare: a static shared secret is exactly the kind
        # of value a timing side-channel could otherwise leak byte-by-byte.
        # Also covers "missing" (provided=None) — compare_digest requires
        # matching types/lengths, so it just returns False rather than
        # raising, and either way this must actively reject, not return None
        # (see class docstring on why "not applicable" isn't safe here).
        # Compared as bytes, not str: hmac.compare_digest() raises TypeError
        # on a non-ASCII str, which would otherwise turn a crafted header
        # value into an unhandled 500 instead of a clean 401.
        if not provided or not hmac.compare_digest(provided.encode(), expected.encode()):
            raise DRFAuthenticationFailed(
                "Invalid service credential.", code="invalid_service_token"
            )
        return (None, provided)

    def authenticate_header(self, request):
        return "Shared-Secret"


class BankSyncServiceAuthentication(_SharedSecretAuthentication):
    """Used only by POST /webhooks/bank-sync/ (core/views/webhooks.py) —
    mock-bank-sync (later: a real bank's own sync feed) pushes transaction
    batches here with no end-user JWT to present. Identity of which account
    the payload belongs to is derived entirely from the payload's
    (provider_slug, external_account_id) pair, never trusted from a
    client-supplied user id."""

    header_name = "HTTP_X_WEBHOOK_SECRET"
    settings_attr = "BANK_SYNC_WEBHOOK_SECRET"
