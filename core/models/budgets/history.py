import uuid

from django.db import models


class BudgetHistory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    budget = models.ForeignKey("Budget", on_delete=models.CASCADE, related_name="history_logs")
    previous_values = models.JSONField()  # Capture snapshot payload of fields and allocations
    changed_via = models.CharField(max_length=20, default="dashboard")  # dashboard / chat
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "budget_history"

    def __str__(self):
        return f"History log for Budget {self.budget_id} modified via {self.changed_via}"
