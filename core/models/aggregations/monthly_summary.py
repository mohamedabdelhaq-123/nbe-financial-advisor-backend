import uuid

from django.db import models
from pgvector.django import HnswIndex, VectorField


class MonthlySummary(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey("User", on_delete=models.CASCADE, related_name="monthly_summaries")
    account = models.ForeignKey(
        "BankAccount",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="monthly_summaries",
    )
    month = models.DateField()  # Saved as YYYY-MM-01
    total_spend = models.DecimalField(max_digits=14, decimal_places=2, blank=True, null=True)
    total_inflow = models.DecimalField(max_digits=14, decimal_places=2, blank=True, null=True)
    category_breakdown_json = models.JSONField(blank=True, null=True)
    top_merchants_json = models.JSONField(blank=True, null=True)
    embedding = VectorField(dimensions=1536, blank=True, null=True)

    class Meta:
        db_table = "monthly_summaries"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "account", "month"], name="unique_user_month_summary"
            )
        ]
        indexes = [
            HnswIndex(
                name="idx_summaries_embedding",
                fields=["embedding"],
                opclasses=["vector_cosine_ops"],
            )
        ]

    def __str__(self):
        return f"Summary {self.month.strftime('%Y-%m')} for User {self.user_id}"
