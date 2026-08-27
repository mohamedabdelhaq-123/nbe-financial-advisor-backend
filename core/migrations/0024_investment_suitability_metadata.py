from django.db import migrations

SUITABILITY_BY_CODE = {
    "gold-24k-gram-egp": {
        "risk_level": "moderate",
        "liquidity": "medium",
        "investment_objectives": ["preserve_value", "balanced_growth"],
        "investment_horizons": ["medium", "long"],
        "investment_aliases": [
            "gold",
            "24k gold",
            "gold 24k",
            "ذهب",
            "الذهب",
            "ذهب عيار 24",
        ],
    },
    "egx30-index-etf": {
        "risk_level": "high",
        "liquidity": "high",
        "investment_objectives": ["balanced_growth"],
        "investment_horizons": ["long"],
        "investment_aliases": [
            "fund",
            "investment fund",
            "etf",
            "egx30",
            "egx30 etf",
            "صندوق",
            "صندوق استثمار",
            "صندوق egx30",
        ],
    },
    "usd-egp-customer-buy": {
        "risk_level": "moderate",
        "liquidity": "high",
        "investment_objectives": ["preserve_value"],
        "investment_horizons": ["short", "medium"],
        "investment_aliases": [
            "usd",
            "dollar",
            "us dollar",
            "currency",
            "دولار",
            "الدولار",
            "دولار أمريكي",
            "عملة",
        ],
    },
}


def add_suitability_metadata(apps, schema_editor):
    investment_instrument = apps.get_model("core", "InvestmentInstrument")
    for instrument in investment_instrument.objects.select_related("product").filter(
        code__in=SUITABILITY_BY_CODE
    ):
        features = dict(instrument.product.features or {})
        features.update(SUITABILITY_BY_CODE[instrument.code])
        instrument.product.features = features
        instrument.product.save(update_fields=["features"])


def remove_suitability_metadata(apps, schema_editor):
    investment_instrument = apps.get_model("core", "InvestmentInstrument")
    managed_keys = {
        "investment_objectives",
        "investment_horizons",
        "investment_aliases",
    }
    for instrument in investment_instrument.objects.select_related("product").filter(
        code__in=SUITABILITY_BY_CODE
    ):
        features = dict(instrument.product.features or {})
        for key in managed_keys:
            features.pop(key, None)
        instrument.product.features = features
        instrument.product.save(update_fields=["features"])


class Migration(migrations.Migration):
    dependencies = [("core", "0023_saved_investment_scenarios")]

    operations = [
        migrations.RunPython(add_suitability_metadata, remove_suitability_metadata),
    ]
