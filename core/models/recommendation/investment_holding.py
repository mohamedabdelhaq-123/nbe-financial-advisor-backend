import uuid

from django.conf import settings
from django.db import models


class InvestmentHolding(models.Model):
    """A manually recorded aggregate position; it never executes a trade."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="investment_holdings",
    )
    instrument = models.ForeignKey(
        "core.InvestmentInstrument",
        on_delete=models.PROTECT,
        related_name="holdings",
    )
    quantity = models.DecimalField(max_digits=24, decimal_places=8)
    average_purchase_price = models.DecimalField(max_digits=20, decimal_places=4)
    fees = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    purchased_at = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "investment_holdings"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "instrument"],
                name="investment_holding_user_instrument_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0),
                name="investment_holding_quantity_gt_zero",
            ),
            models.CheckConstraint(
                condition=models.Q(average_purchase_price__gt=0),
                name="investment_holding_purchase_price_gt_zero",
            ),
            models.CheckConstraint(
                condition=models.Q(fees__gte=0),
                name="investment_holding_fees_gte_zero",
            ),
        ]

    def __str__(self):
        return f"{self.user_id}:{self.instrument.code}"
