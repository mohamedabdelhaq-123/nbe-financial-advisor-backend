import uuid

from django.db import models


class SavedInvestmentScenario(models.Model):
    """A user-saved, non-executing snapshot of an investment-plan widget.

    The JSON payload starts as the exact plan the user saw. Pending amounts and
    reference prices may be explicitly edited by the user; edited prices are
    labelled user-supplied. It remains a plan, not a portfolio position, and
    never updates itself automatically when market prices change.
    """

    class Status(models.TextChoices):
        SAVED = "saved", "Saved"
        ARCHIVED = "archived", "Archived"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        "User",
        on_delete=models.CASCADE,
        related_name="saved_investment_scenarios",
    )
    source_message = models.OneToOneField(
        "Message",
        on_delete=models.SET_NULL,
        related_name="saved_investment_scenario",
        blank=True,
        null=True,
    )
    title = models.CharField(max_length=120, default="Investment scenario")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SAVED)
    payload_json = models.JSONField()
    saved_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "saved_investment_scenarios"
        ordering = ["-saved_at", "-id"]
        indexes = [
            models.Index(
                fields=["user", "status", "saved_at"],
                name="idx_saved_scenario_user",
            )
        ]

    def __str__(self):
        return f"{self.title} ({self.user_id})"


class SavedInvestmentAllocationPurchase(models.Model):
    """A user's confirmation that one saved-plan allocation was bought.

    The row is an idempotency record and an audit link. The actual aggregate
    position remains InvestmentHolding; saving a scenario alone never creates
    either record.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scenario = models.ForeignKey(
        SavedInvestmentScenario,
        on_delete=models.CASCADE,
        related_name="allocation_purchases",
    )
    instrument = models.ForeignKey(
        "core.InvestmentInstrument",
        on_delete=models.PROTECT,
        related_name="saved_plan_purchases",
    )
    holding = models.ForeignKey(
        "core.InvestmentHolding",
        on_delete=models.SET_NULL,
        related_name="saved_plan_purchases",
        null=True,
        blank=True,
    )
    quantity = models.DecimalField(max_digits=24, decimal_places=8)
    unit_price = models.DecimalField(max_digits=20, decimal_places=4)
    fees = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    purchased_at = models.DateField()
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "saved_investment_allocation_purchases"
        ordering = ["recorded_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["scenario", "instrument"],
                name="saved_scenario_instrument_purchase_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0),
                name="saved_purchase_quantity_gt_zero",
            ),
            models.CheckConstraint(
                condition=models.Q(unit_price__gt=0),
                name="saved_purchase_unit_price_gt_zero",
            ),
            models.CheckConstraint(
                condition=models.Q(fees__gte=0),
                name="saved_purchase_fees_gte_zero",
            ),
        ]

    def __str__(self):
        return f"{self.scenario_id}:{self.instrument_id}"
