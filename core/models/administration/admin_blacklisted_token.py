import uuid

from django.db import models


class AdminBlacklistedToken(models.Model):
    """
    Admin-credential-space equivalent of rest_framework_simplejwt's
    OutstandingToken/BlacklistedToken pair (SEC-009) — those are unusable
    for AdminUser tokens, since OutstandingToken.user is hardcoded to
    AUTH_USER_MODEL (core.User) and rejects an AdminUser instance outright
    (see AdminLoginView's docstring, core/views/administration.py). Only
    the blacklisted half is needed here: nothing in this app needs an
    outstanding-tokens audit list (simplejwt's OutstandingToken mainly
    backs "let an admin panel list and revoke a user's other active
    sessions", a feature this app doesn't have), and a token's own claims
    already carry everything else AdminJWTAuthentication needs.

    Rows are looked up by `jti` alone at refresh/authentication time — a
    JWT's own `exp` claim already makes it unusable once expired, so
    `expires_at` here exists only so a future cleanup job could prune rows
    for tokens that are long expired anyway, not because a query ever needs it.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    jti = models.CharField(max_length=255, unique=True)
    blacklisted_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        db_table = "admin_blacklisted_tokens"

    def __str__(self):
        return f"AdminBlacklistedToken({self.jti})"
