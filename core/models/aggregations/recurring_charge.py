import uuid

from django.db import models


class RecurringCharge(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey("User", on_delete=models.CASCADE, related_name="recurring_charges")
    account = models.ForeignKey(
        "BankAccount",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
        related_name="recurring_charges",
    )
    merchant_normalized = models.CharField(max_length=255)
    frequency = models.CharField(max_length=20)  # weekly, monthly, etc.
    avg_amount = models.DecimalField(max_digits=14, decimal_places=2, blank=True, null=True)
    last_occurrence_date = models.DateField(blank=True, null=True)
    next_expected_date = models.DateField(blank=True, null=True)

    class Meta:
        db_table = "recurring_charges"
