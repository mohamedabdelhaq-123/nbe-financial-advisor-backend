"""
app/config.py reads its required env vars at import time (module-level
_require() calls), so these must be set before anything imports app.main —
before pytest even collects test modules that do `from app.main import app`.
conftest.py is guaranteed to run first.

Plain assignment, not os.environ.setdefault(): running via `docker compose
exec mock-bank-oauth pytest`, the container's real environment already has
these set (docker-compose.yml, for the service's normal operation) —
setdefault() would be a no-op against an already-present real value, and
these tests need known values regardless of what's actually configured.
"""

import os

os.environ["MOCK_BANK_OAUTH_CLIENT_ID"] = "test-client"
os.environ["MOCK_BANK_OAUTH_CLIENT_SECRET"] = "test-client-secret"
os.environ["MOCK_BANK_INTERNAL_SECRET"] = "test-internal-secret"
os.environ["MOCK_BANK_JWT_SECRET"] = "test-jwt-secret"
os.environ["MOCK_BANK_OAUTH_GMAIL_ADDRESS"] = "test-mock-bank@example.com"
os.environ["MOCK_BANK_OAUTH_GMAIL_APP_PASSWORD"] = "test-app-password"
os.environ["MOCK_BANK_SYNC_SERVICE_URL"] = "http://fake-mock-bank-sync:8003"
os.environ["MOCK_BANK_OAUTH_ENABLE_DEBUG_ENDPOINTS"] = "1"

import pytest
from app.main import app
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_store():
    """Each test starts with an empty in-memory challenge/code/token store —
    app/store.py's dicts are module-level singletons, shared across the
    whole test session otherwise."""
    from app import store

    store._challenges.clear()
    store._auth_codes.clear()
    store._refresh_tokens.clear()
    yield
