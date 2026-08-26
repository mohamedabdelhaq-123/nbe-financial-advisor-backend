from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0020_message_suggestions_json"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="transaction",
            name="unique_ledger_transaction_match",
        ),
        migrations.AddConstraint(
            model_name="transaction",
            constraint=models.UniqueConstraint(
                fields=(
                    "user",
                    "account",
                    "transaction_date",
                    "amount",
                    "merchant_raw",
                    "transaction_type",
                ),
                name="unique_ledger_transaction_match",
            ),
        ),
    ]
