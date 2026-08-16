import uuid

from django.db import models


class AdminBlacklistedToken(models.Model):
    """Admin equivalent of simplejwt's OutstandingToken/BlacklistedToken —
    those hardcode `user` to AUTH_USER_MODEL and reject AdminUser. Only the
    blacklist half is needed; expires_at is for a future cleanup job, not
    queried directly (exp is already enforced by the JWT itself)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    jti = models.CharField(max_length=255, unique=True)
    blacklisted_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        db_table = "admin_blacklisted_tokens"

    def __str__(self):
        return f"AdminBlacklistedToken({self.jti})"
