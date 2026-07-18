"""
Integration test for the full bank-account-linking flow, run against the
real, already-running mock-bank-oauth and mock-bank-sync containers — no
mocking of either service, unlike tests/test_bank_connectors.py,
tests/test_bank_connections_views.py, or either mock service's own test
suite, which each fake whichever side of a service boundary isn't the
system under test. Not run by default — same `integration` marker/rationale
as tests/integration/test_file_storage_live.py; run with:

    docker compose exec backend pytest -m integration \
        tests/integration/test_bank_integration_live.py -v

The OTP-email step is exercised for real: mock-bank-oauth genuinely calls
this backend's own /internal/notifications/email/, which genuinely fails
against this dev stack's placeholder Gmail credentials. That 502 is
asserted as the correct outcome, not routed around — it's proof the
error-handling path works under a real failure. The OTP itself is then read
via mock-bank-oauth's debug-only GET /debug/challenges/{id}
(MOCK_BANK_OAUTH_ENABLE_DEBUG_ENDPOINTS=1 in this stack) — the one
deliberate bypass in this test; every other step through account linking
(step 6 below) is a real network call between real running containers.

KNOWN GAP, not silently worked around: the reverse direction (step 7 —
mock-bank-sync pushing a webhook back to this backend) is NOT verified
end-to-end here. pytest-django creates its own isolated, ephemeral test
database for this test run (see "Creating test database..." in its output)
— the BankAccount created in step 6 exists only there, never in the real
dev database the already-running `backend` container (the one mock-bank-sync's
webhook actually reaches) is connected to. So a real webhook push against a
real account, landing a real, verifiable Transaction, cannot be proven this
way — that would need the pytest process and the live server to share one
database (e.g. pytest-django's live_server fixture), which is awkward here
since mock-bank-sync's BACKEND_WEBHOOK_URL is a fixed address and reaching a
live_server's own ephemeral port from a *different* container is nontrivial.
Step 7 instead verifies only what's checkable without shared DB state: that
the live server's actual configured shared secret (read from Django settings
here, not overridden) authenticates correctly and reaches real account-
resolution logic, distinguishing "wrong secret" (401) from "right secret,
unknown account" (404). It does not prove a real push creates a real,
persisted transaction on the live server — that remains unverified pending a
live_server-based rewrite, should this gap need closing later.
"""

import re
import uuid

import pytest
import requests
from django.conf import settings
from rest_framework.test import APIClient

from core.models import BankAccount, BankConnection, Transaction, User

pytestmark = pytest.mark.integration

_REQUEST_TIMEOUT_SECONDS = 10


@pytest.fixture
def user(db):
    user = User.objects.create_user(
        email=f"integration-test-{uuid.uuid4()}@example.com",
        password="x",
        name="Integration Test",
    )
    yield user
    # BankConnection/BankAccount/Transaction/AnomalyFlag all CASCADE from
    # User, so this cleans up everything the test creates on the Django side.
    user.delete()


@pytest.fixture
def client(user):
    api_client = APIClient()
    api_client.force_authenticate(user=user)
    return api_client


@pytest.fixture
def mock_customer():
    """Seeds a real mock bank customer (with one pre-existing transaction,
    so the account has real history for the initial-backfill step to pull —
    BankConnectionCallbackView only backfills when fetch_transactions()
    returns something non-empty) via real HTTP calls to the running
    mock-bank-sync container, and deletes it again afterward via
    DELETE /simulate/customer/{id}. Unlike this test's own Django-side data
    (isolated in pytest-django's own ephemeral test database, see this
    module's docstring), mock-bank-sync's ledger is a real, persistent
    database shared with that service's own test suite — leaving data here
    previously broke test_simulate_transaction_no_accounts_at_all_404s in
    mock-bank-sync/tests/test_endpoints.py, which assumes an empty ledger."""
    customer_bank_id = f"integration-test-{uuid.uuid4()}"
    customer_response = requests.post(
        f"{settings.MOCK_BANK_SYNC_SERVICE_URL}/simulate/customer",
        json={
            "customer_bank_id": customer_bank_id,
            "email": "integration-test-customer@example.com",
            "name": "Integration Test Customer",
        },
        timeout=_REQUEST_TIMEOUT_SECONDS,
    )
    customer_response.raise_for_status()
    customer = customer_response.json()

    # No Django BankAccount exists yet at this point, so the webhook this
    # triggers is expected to 404 on the backend side — only the mock
    # ledger's own write (the transaction history itself) matters here.
    transaction_response = requests.post(
        f"{settings.MOCK_BANK_SYNC_SERVICE_URL}/simulate/transaction",
        json={"account_id": customer["accounts"][0]["external_account_id"]},
        timeout=_REQUEST_TIMEOUT_SECONDS,
    )
    transaction_response.raise_for_status()

    yield customer

    requests.delete(
        f"{settings.MOCK_BANK_SYNC_SERVICE_URL}/simulate/customer/{customer_bank_id}",
        timeout=_REQUEST_TIMEOUT_SECONDS,
    ).raise_for_status()


def test_full_link_flow_against_real_running_services(client, mock_customer):
    # 1. Initiate — real Django view; get_authorize_url() is pure
    # URL-building, no network call yet.
    initiate = client.post("/bank-connections/", {"provider_slug": "mock_bank"})
    assert initiate.status_code == 201
    connection_id = initiate.data["connection_id"]
    connection = BankConnection.objects.get(id=connection_id)
    state = connection.oauth_state

    # 2. Real GET /authorize against the live mock-bank-oauth container.
    authorize_response = requests.get(
        initiate.data["authorize_url"], timeout=_REQUEST_TIMEOUT_SECONDS
    )
    assert authorize_response.status_code == 200
    challenge_id = re.search(r'name="challenge_id" value="([^"]+)"', authorize_response.text).group(
        1
    )

    # 3. Real POST /login/start — mock-bank-oauth makes a real cross-container
    # lookup call to mock-bank-sync, then a real (and, in this dev stack,
    # genuinely failing) attempt to email the OTP via this backend. See
    # module docstring: the 502 here is the expected, correct outcome.
    login_start_response = requests.post(
        f"{settings.MOCK_BANK_OAUTH_SERVICE_URL}/login/start",
        data={
            "challenge_id": challenge_id,
            "customer_bank_id": mock_customer["customer_bank_id"],
        },
        timeout=_REQUEST_TIMEOUT_SECONDS,
    )
    assert login_start_response.status_code == 502

    # 4. Bypass: read the OTP mock-bank-oauth already generated (before the
    # email attempt failed) via the debug-only endpoint.
    debug_response = requests.get(
        f"{settings.MOCK_BANK_OAUTH_SERVICE_URL}/debug/challenges/{challenge_id}",
        timeout=_REQUEST_TIMEOUT_SECONDS,
    )
    assert debug_response.status_code == 200
    otp = debug_response.json()["otp"]
    assert otp is not None

    # 5. Real POST /login/verify — mints a real, single-use authorization code.
    verify_response = requests.post(
        f"{settings.MOCK_BANK_OAUTH_SERVICE_URL}/login/verify",
        data={"challenge_id": challenge_id, "otp": otp},
        timeout=_REQUEST_TIMEOUT_SECONDS,
        allow_redirects=False,
    )
    assert verify_response.status_code == 302
    code = re.search(r"code=([^&]+)", verify_response.headers["Location"]).group(1)

    # 6. Real callback — Django exchanges the code for a token against the
    # live mock-bank-oauth, then pulls accounts/transactions from the live
    # mock-bank-sync.
    callback_response = client.post(
        f"/bank-connections/{connection_id}/callback/", {"code": code, "state": state}
    )
    assert callback_response.status_code == 200
    assert len(callback_response.data) >= 1

    connection.refresh_from_db()
    assert connection.status == BankConnection.STATUS_LINKED
    assert connection.access_token  # real JWT issued by mock-bank-oauth

    account = BankAccount.objects.get(connection=connection)
    assert account.link_type == BankAccount.LINK_TYPE_SYNCED
    assert Transaction.objects.filter(account=account, source="synced").exists()

    # 7. Reverse direction, narrowly scoped — see module docstring's "KNOWN
    # GAP" note for why this can't verify a full push-creates-a-transaction
    # outcome. What IS real here: this hits the actual already-running
    # `backend` container directly (not the in-process `client`/APIClient
    # used above), and settings.BANK_SYNC_WEBHOOK_SECRET is read unmodified
    # from Django settings — i.e. whatever that live server is genuinely
    # configured with, not a test override — so a mismatch between what
    # mock-bank-sync sends and what the live server actually expects would
    # be caught here.
    webhook_url = "http://backend:8000/webhooks/bank-sync/"
    unknown_payload = {
        "provider_slug": "mock_bank",
        "external_account_id": f"integration-test-unknown-{uuid.uuid4()}",
        "transactions": [],
    }
    wrong_secret_response = requests.post(
        webhook_url,
        json=unknown_payload,
        headers={"X-Webhook-Secret": "definitely-wrong-secret"},
        timeout=_REQUEST_TIMEOUT_SECONDS,
    )
    assert wrong_secret_response.status_code == 401

    correct_secret_response = requests.post(
        webhook_url,
        json=unknown_payload,
        headers={"X-Webhook-Secret": settings.BANK_SYNC_WEBHOOK_SECRET},
        timeout=_REQUEST_TIMEOUT_SECONDS,
    )
    assert correct_secret_response.status_code == 404
