from django.db import migrations


class Migration(migrations.Migration):
    """
    Merges the two sibling 0017 migrations — 0017_admin_blacklisted_token
    (SEC-009) branched off 0016 instead of chaining after the pre-existing
    0017_rename_bankaccount_account_number, leaving two leaf nodes in the
    graph. No schema changes of its own.
    """

    dependencies = [
        ("core", "0017_admin_blacklisted_token"),
        ("core", "0018_alter_transaction_merchant_raw"),
    ]
