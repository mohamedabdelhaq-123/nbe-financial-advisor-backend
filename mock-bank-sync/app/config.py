"""Environment-driven configuration, read with plain os.environ.

POSTGRES_HOST/POSTGRES_PORT reuse the same env var NAMES the Django `backend`
service already uses for the shared Postgres container (see docker-compose.yml)
— this service just points them at a different logical database
(MOCK_BANK_DB_NAME) via a different least-privilege role
(MOCK_BANK_DB_USER/MOCK_BANK_DB_PASSWORD), provisioned by
deploy/initdb/20-mock-bank-roles.sh.
"""

import os


def _required(name: str) -> str:
    """Reads an env var, raising if it's unset — used for secrets that
    would otherwise fail silently deep inside a request handler."""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} environment variable is required but not set")
    return value


# --- Database (shared Postgres container, own logical database) -----------
POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "postgres")
POSTGRES_PORT = os.environ.get("POSTGRES_PORT", "5432")
MOCK_BANK_DB_NAME = os.environ.get("MOCK_BANK_DB_NAME", "mock_bank_db")
MOCK_BANK_DB_USER = os.environ.get("MOCK_BANK_DB_USER", "mock_bank_user")

# --- Auth secrets ------------------------------------------------------
# Read lazily (function, not module-level constant) so importing this module
# (e.g. for alembic's env.py, or `MOCK_BANK_DB_PASSWORD` below) doesn't blow
# up processes/tests that don't actually need every secret populated.
MOCK_BANK_INTERNAL_SECRET = os.environ.get("MOCK_BANK_INTERNAL_SECRET")
MOCK_BANK_JWT_SECRET = os.environ.get("MOCK_BANK_JWT_SECRET")

# --- Outbound webhook (this service -> Django backend) -----------------
BACKEND_WEBHOOK_URL = os.environ.get(
    "BACKEND_WEBHOOK_URL", "http://backend:8000/webhooks/bank-sync/"
)
BANK_SYNC_WEBHOOK_SECRET = os.environ.get("BANK_SYNC_WEBHOOK_SECRET")


def mock_bank_db_password() -> str:
    """Password for MOCK_BANK_DB_USER, this service's own least-privilege
    Postgres role."""
    return _required("MOCK_BANK_DB_PASSWORD")


def internal_secret() -> str:
    """Shared secret checked on X-Internal-Secret for /internal/customers/lookup."""
    return _required("MOCK_BANK_INTERNAL_SECRET")


def jwt_secret() -> str:
    """HS256 key used to verify Bearer JWTs on /accounts* — must match
    what mock-bank-oauth signs with."""
    return _required("MOCK_BANK_JWT_SECRET")


def webhook_secret() -> str:
    """Sent as X-Webhook-Secret on the outbound push to the Django backend."""
    return _required("BANK_SYNC_WEBHOOK_SECRET")


def database_url() -> str:
    """SQLAlchemy connection URL for this service's own mock_bank_db."""
    return (
        f"postgresql+psycopg2://{MOCK_BANK_DB_USER}:{mock_bank_db_password()}"
        f"@{POSTGRES_HOST}:{POSTGRES_PORT}/{MOCK_BANK_DB_NAME}"
    )
