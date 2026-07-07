import uuid

from django.db import models


class BudgetAllocation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    budget = models.ForeignKey("Budget", on_delete=models.CASCADE, related_name="allocations")
    category = models.CharField(max_length=100)
    allocated_percentage = models.DecimalField(max_digits=5, decimal_places=2)
    allocated_amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=10, default="EGP")

    class Meta:
        db_table = "budget_allocations"
        constraints = [
            models.UniqueConstraint(fields=["budget", "category"], name="unique_budget_category")
        ]

    def __str__(self):
        return (
                f"{self.category}: {self.allocated_percentage}% "
                f"({self.allocated_amount} {self.currency})"
            )