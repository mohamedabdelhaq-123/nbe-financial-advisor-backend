import uuid

from django.db import models


class RecommendationLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        "User",
        on_delete=models.CASCADE,
        related_name="recommendation_logs",
    )
    product = models.ForeignKey(
        "Product",
        on_delete=models.CASCADE,
        related_name="delivery_logs",
    )
    matched_query = models.TextField(blank=True, null=True)
    similarity_score = models.DecimalField(max_digits=5, decimal_places=4, blank=True, null=True)
    shown_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "recommendation_logs"
        indexes = [models.Index(fields=["user", "shown_at"], name="idx_recommendation_logs_user")]

    def __str__(self):
        return f"Log: User {self.user_id} -> {self.product.title} at {self.shown_at}"
