import uuid

from django.db import models


class AnomalyFlag(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    transaction = models.ForeignKey(
        "Transaction",
        on_delete=models.CASCADE,
        related_name="anomaly_flags",
    )
    reason = models.TextField()
    severity = models.CharField(max_length=10)  # low, medium, high
    resolved = models.BooleanField(default=False)
    detected_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "anomaly_flags"
        indexes = [models.Index(fields=["severity", "resolved"], name="idx_anomaly_flags_severity")]
