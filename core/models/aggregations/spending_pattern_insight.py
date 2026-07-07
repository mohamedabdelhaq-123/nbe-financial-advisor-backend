import uuid

from django.db import models


class SpendingPatternInsight(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey("User", on_delete=models.CASCADE, related_name="pattern_insights")
    insight_type = models.CharField(max_length=50)  # cash_flow, category_volatility, etc.
    period = models.CharField(max_length=20, blank=True, null=True)
    value_json = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "spending_pattern_insights"
        indexes = [
            models.Index(
                fields=["user", "insight_type"],
                name="idx_user_spending_type",
            )
        ]
