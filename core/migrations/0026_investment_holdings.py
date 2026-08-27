# Generated for manually tracked investment positions.

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0025_fund_market_price"),
    ]

    operations = [
        migrations.CreateModel(
            name="InvestmentHolding",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("quantity", models.DecimalField(decimal_places=8, max_digits=24)),
                (
                    "average_purchase_price",
                    models.DecimalField(decimal_places=4, max_digits=20),
                ),
                ("fees", models.DecimalField(decimal_places=2, default=0, max_digits=20)),
                ("purchased_at", models.DateField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "instrument",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="holdings",
                        to="core.investmentinstrument",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="investment_holdings",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "investment_holdings",
                "constraints": [
                    models.UniqueConstraint(
                        fields=("user", "instrument"),
                        name="investment_holding_user_instrument_unique",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("quantity__gt", 0)),
                        name="investment_holding_quantity_gt_zero",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("average_purchase_price__gt", 0)),
                        name="investment_holding_purchase_price_gt_zero",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("fees__gte", 0)),
                        name="investment_holding_fees_gte_zero",
                    ),
                ],
            },
        )
    ]
