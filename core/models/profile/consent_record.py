import uuid

from django.db import models


class ConsentRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey("User", on_delete=models.CASCADE, related_name="consent_records")
    consent_type = models.CharField(max_length=50)
    policy_version = models.CharField(max_length=20)
    granted_at = models.DateTimeField(blank=True, null=True)
    revoked_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "consent_records"

    @property
    def is_currently_valid(self):
        return self.granted_at is not None and self.revoked_at is None
