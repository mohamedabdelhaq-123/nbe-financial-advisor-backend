# Generated for SEC-009 — admin refresh-token revocation.

import uuid

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0016_user_email_verified"),
    ]

    operations = [
        migrations.CreateModel(
            name="AdminBlacklistedToken",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("jti", models.CharField(max_length=255, unique=True)),
                ("blacklisted_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField()),
            ],
            options={
                "db_table": "admin_blacklisted_tokens",
            },
        ),
    ]
