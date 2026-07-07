import uuid

from django.db import models


class Budget(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField("User", on_delete=models.CASCADE, related_name="budget")
    name = models.CharField(max_length=255, default="My Plan")
    period_type = models.CharField(max_length=20, default="monthly")
    status = models.CharField(max_length=20, default="active")
    selected_template_key = models.CharField(max_length=50, blank=True, null=True)
    savings_goal_name = models.CharField(max_length=255, blank=True, null=True)
    goal_target_amount = models.DecimalField(max_digits=14, decimal_places=2, blank=True, null=True)
    goal_timeline_months = models.IntegerField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "budgets"

    def __str__(self):
        return f"Budget Plan for User {self.user_id}"

    @property
    def total_allocated_amount(self):
        """Sum of all category allocations currently configured for this budget."""
        return self.allocations.aggregate(total=models.Sum("allocated_amount"))["total"] or 0.00
