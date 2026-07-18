"""Alembic environment.

Points at app.models' declarative metadata (via app.db.Base) so
`alembic revision --autogenerate` works for future schema changes, and
builds sqlalchemy.url at runtime from app.config.database_url() rather than
from alembic.ini, so no DB credentials need to live in that file.
"""

from logging.config import fileConfig

from alembic import context
from app import config as app_config

# Import models so they're registered on Base.metadata before autogenerate
# inspects it.
from app import models  # noqa: F401
from app.db import Base
from sqlalchemy import engine_from_config, pool

# this is the Alembic Config object, which provides access to values within
# the .ini file in use.
config = context.config

# Interpret the config file for Python logging (skipped if no config passed).
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", app_config.database_url())

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emits SQL, no live DB connection)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (against a live DB connection)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
