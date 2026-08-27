from django.db import migrations, models

FUND_CODE = "egx30-index-etf"


def use_market_price(apps, schema_editor):
    InvestmentInstrument = apps.get_model("core", "InvestmentInstrument")
    instrument = InvestmentInstrument.objects.filter(code=FUND_CODE).first()
    if instrument is None:
        return
    instrument.provider_symbol = "EGX30ETF_MARKET_PRICE"
    instrument.price_type = "market_price"
    instrument.save(update_fields=["provider_symbol", "price_type", "updated_at"])
    product = instrument.product
    product.description = (
        "A curated Egyptian equity index fund quoted by its latest available "
        "delayed EGX market price per unit."
    )
    product.save(update_fields=["description"])


def restore_nav(apps, schema_editor):
    InvestmentInstrument = apps.get_model("core", "InvestmentInstrument")
    instrument = InvestmentInstrument.objects.filter(code=FUND_CODE).first()
    if instrument is None:
        return
    instrument.provider_symbol = "EGX30ETF_NAV"
    instrument.price_type = "nav"
    instrument.save(update_fields=["provider_symbol", "price_type", "updated_at"])
    product = instrument.product
    product.description = (
        "A curated Egyptian equity index fund quoted by its latest published NAV per unit."
    )
    product.save(update_fields=["description"])


class Migration(migrations.Migration):
    dependencies = [("core", "0024_investment_suitability_metadata")]

    operations = [
        migrations.AlterField(
            model_name="investmentinstrument",
            name="price_type",
            field=models.CharField(
                choices=[
                    ("spot", "Spot price"),
                    ("nav", "Net asset value"),
                    ("market_price", "Market price"),
                    ("customer_buy_rate", "Customer buy rate"),
                ],
                max_length=30,
            ),
        ),
        migrations.RunPython(use_market_price, restore_nav),
    ]
