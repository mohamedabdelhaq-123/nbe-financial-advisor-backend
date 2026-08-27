import uuid

from django.db import models


class InvestmentInstrument(models.Model):
    """Market-pricing metadata for one curated product.

    The LLM only ever receives ``id``/``code`` from this table. Provider
    symbols and quote semantics remain application-owned catalogue data.
    """

    class AssetClass(models.TextChoices):
        GOLD = "gold", "Gold"
        FUND = "fund", "Investment fund"
        CURRENCY = "currency", "Currency"

    class PriceType(models.TextChoices):
        SPOT = "spot", "Spot price"
        NAV = "nav", "Net asset value"
        MARKET_PRICE = "market_price", "Market price"
        CUSTOMER_BUY_RATE = "customer_buy_rate", "Customer buy rate"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.OneToOneField(
        "core.Product",
        on_delete=models.CASCADE,
        related_name="investment_instrument",
    )
    code = models.SlugField(max_length=100, unique=True)
    asset_class = models.CharField(max_length=20, choices=AssetClass.choices)
    provider_symbol = models.CharField(max_length=120)
    price_type = models.CharField(max_length=30, choices=PriceType.choices)
    price_currency = models.CharField(max_length=3, default="EGP")
    unit = models.CharField(max_length=40)
    minimum_increment = models.DecimalField(max_digits=20, decimal_places=8, default=1)
    fractional_units_supported = models.BooleanField(default=False)
    max_quote_age_seconds = models.PositiveIntegerField(default=3600)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "investment_instruments"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(minimum_increment__gt=0),
                name="investment_instrument_increment_gt_zero",
            ),
            models.CheckConstraint(
                condition=models.Q(max_quote_age_seconds__gt=0),
                name="investment_instrument_quote_age_gt_zero",
            ),
        ]

    def __str__(self):
        return self.code
