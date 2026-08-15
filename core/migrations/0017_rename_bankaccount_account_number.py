from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0016_user_email_verified"),
    ]

    operations = [
        migrations.RenameField(
            model_name="bankaccount",
            old_name="masked_account_number",
            new_name="account_number",
        ),
    ]
