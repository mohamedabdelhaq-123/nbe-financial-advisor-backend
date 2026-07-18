import uuid

from django.db import models


class AnomalyFlag(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Nullable: ai_service.run_post_ingestion_analysis()'s anomalies are
    # aggregated at (user, account, category, month) grain, with no
    # transaction id to pin them to — unlike RecurringCharge/MonthlySummary,
    # which never had a transaction FK to begin with, this one did, so `user`/
    # `account` are added here as the scoping fields a transaction-less
    # anomaly still needs to be meaningful. `transaction` stays populated for
    # anomalies detected some other way (e.g. seed_db's outlier-picking).
    transaction = models.ForeignKey(
        "Transaction",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="anomaly_flags",
    )
    user = models.ForeignKey("User", on_delete=models.CASCADE, related_name="anomaly_flags")
    account = models.ForeignKey(
        "BankAccount",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="anomaly_flags",
    )
    category = models.CharField(max_length=100, blank=True, null=True)
    month = models.DateField(blank=True, null=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2, blank=True, null=True)
    reason = models.TextField()
    severity = models.CharField(max_length=10)  # low, medium, high
    resolved = models.BooleanField(default=False)
    detected_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "anomaly_flags"
        indexes = [models.Index(fields=["severity", "resolved"], name="idx_anomaly_flags_severity")]
