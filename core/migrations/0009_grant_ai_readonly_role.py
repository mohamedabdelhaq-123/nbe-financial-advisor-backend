import os

from django.db import migrations


def create_ai_readonly_role(apps, schema_editor):
    connection = schema_editor.connection
    db_name = connection.settings_dict["NAME"]
    owner = connection.settings_dict["USER"]
    password = os.environ["AI_READONLY_PASSWORD"]
    quote = connection.ops.quote_name

    with connection.cursor() as cursor:
        cursor.execute(
            """
            DO $$
            BEGIN
              IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ai_readonly') THEN
                CREATE ROLE ai_readonly LOGIN;
              END IF;
            END
            $$;
            """
        )
        cursor.execute("ALTER ROLE ai_readonly WITH LOGIN PASSWORD %s", [password])
        cursor.execute("ALTER ROLE ai_readonly SET default_transaction_read_only = on")
        cursor.execute(f"GRANT CONNECT ON DATABASE {quote(db_name)} TO ai_readonly")
        cursor.execute("GRANT USAGE ON SCHEMA public TO ai_readonly")
        cursor.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO ai_readonly")
        cursor.execute(
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {quote(owner)} IN SCHEMA public "
            "GRANT SELECT ON TABLES TO ai_readonly"
        )
        cursor.execute(f"REVOKE CONNECT ON DATABASE {quote(db_name)} FROM PUBLIC")

        # Narrow, explicit write exceptions on top of the otherwise SELECT-only
        # role: the AI service backfills computed embeddings and owns
        # monthly_summaries end to end.
        cursor.execute("GRANT UPDATE (embedding) ON transactions TO ai_readonly")
        cursor.execute(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON monthly_summaries TO ai_readonly"
        )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0008_remove_budget_goal_target_amount_and_more"),
    ]

    operations = [
        migrations.RunPython(create_ai_readonly_role, migrations.RunPython.noop),
    ]
