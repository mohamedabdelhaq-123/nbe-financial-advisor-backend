from rest_framework.permissions import BasePermission

from core.authentication import AdminJWTAuthentication
from core.models import AdminUser


class IsAdminUser(BasePermission):
    """
    Baseline for every /admin/* route (any role — reviewer or super_admin).
    Not DRF's built-in IsAuthenticated: that checks `request.user.
    is_authenticated`, an attribute AdminUser deliberately doesn't have
    (it's a plain model, not shaped like Django's auth user — see
    core/authentication.py's module docstring on why the two credential
    spaces are kept structurally separate). isinstance() here also handles
    the unauthenticated case for free: request.user is AnonymousUser when
    no/invalid token was supplied, and AnonymousUser is never an AdminUser.
    """

    def has_permission(self, request, view):
        return isinstance(request.user, AdminUser)


class IsSuperAdmin(BasePermission):
    """super_admin only — product catalog writes (Data_Shapes_Administration.md's role split)."""

    def has_permission(self, request, view):
        return isinstance(request.user, AdminUser) and request.user.is_super_admin


class HasDataProcessingConsent(BasePermission):
    """
    Gates any endpoint that runs OCR on a new document or sends the user's
    financial data to the AI service — both are "processing" in the
    data_processing consent's sense (see ConsentRecord / MeConsentView),
    so both stop once that consent is revoked. Only guards the
    *new-processing* entry points (statement upload, sending a chat
    message) — reading data already processed before the revoke, or
    starting an empty chat session, stays available same as the rest of
    the account.
    """

    message = "Data processing consent is required for this action."

    def has_permission(self, request, view):
        return request.user.has_active_consent("data_processing")


class AdminAuthMixin:
    """
    Applied to every /admin/* view. Swaps the project-wide default
    authentication (UserJWTAuthentication, which explicitly rejects admin
    tokens) for AdminJWTAuthentication, and gates on IsAdminUser rather than
    IsAuthenticated for the reason in that class's docstring. Views needing
    the stricter super_admin-only gate override permission_classes (or
    get_permissions() for a per-method split) with IsSuperAdmin instead.
    """

    authentication_classes = [AdminJWTAuthentication]
    permission_classes = [IsAdminUser]
    # Global DEFAULT_THROTTLE_CLASSES assumes request.user.is_authenticated,
    # which AdminUser deliberately lacks (see IsAdminUser above) — crashes
    # otherwise. Already gated by IsAdminUser, so no throttling gap in practice.
    throttle_classes = []
