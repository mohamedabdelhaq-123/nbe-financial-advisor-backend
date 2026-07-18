"""
Endpoint tests against the real (transaction-wrapped, rolled back per test —
see conftest.py) mock_bank_db. requests.post to the Django backend's webhook
(routes_simulate.py's /simulate/transaction) is monkeypatched so these tests
don't depend on the backend actually running.
"""

import base64
import json
import time
from unittest.mock import patch

from app import config
from authlib.jose import jwt


def _unsigned_none_alg_token(payload: dict) -> str:
    """Hand-built {"alg": "none"} JWT (no signature segment) — authlib's own
    high-level jwt.encode() now refuses to produce one (UnsupportedAlgorithmError),
    so this constructs the raw compact-serialization bytes directly to
    simulate what an attacker would actually send."""

    def b64url(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    header = b64url(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    body = b64url(json.dumps(payload).encode())
    return f"{header}.{body}."


class _FakeWebhookResponse:
    status_code = 202
    ok = True
    text = ""


def _seed_customer(client, customer_bank_id="cust-001", email="customer@example.com"):
    response = client.post(
        "/simulate/customer",
        json={"customer_bank_id": customer_bank_id, "email": email, "name": "Test Customer"},
    )
    assert response.status_code == 201
    return response.json()


def _bearer_token(customer_id: str) -> str:
    # exp is required: app.auth's decode enforces it as an essential claim,
    # so a token shaped like this must match what mock-bank-oauth actually
    # issues (always has exp) rather than the bare-minimum JWT this used to be.
    header = {"alg": "HS256"}
    payload = {"sub": customer_id, "provider": "mock_bank", "exp": int(time.time()) + 3600}
    return jwt.encode(header, payload, config.jwt_secret()).decode("utf-8")


# ============================================================================
# GET /health
# ============================================================================


def test_health_reports_db_reachable(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ============================================================================
# POST /simulate/customer
# ============================================================================


def test_simulate_customer_creates_customer_and_default_account(client):
    body = _seed_customer(client)

    assert body["customer_bank_id"] == "cust-001"
    assert body["email"] == "customer@example.com"
    assert len(body["accounts"]) == 1
    assert body["accounts"][0]["account_type"] == "checking"


def test_simulate_customer_duplicate_bank_id_conflicts(client):
    _seed_customer(client)
    response = client.post(
        "/simulate/customer",
        json={"customer_bank_id": "cust-001", "email": "other@example.com"},
    )
    assert response.status_code == 409


def test_delete_simulated_customer_cascades_to_accounts(client):
    body = _seed_customer(client)
    account_id = body["accounts"][0]["external_account_id"]

    response = client.delete("/simulate/customer/cust-001")

    assert response.status_code == 204
    token = _bearer_token(body["customer_id"])
    # The account no longer belongs to any customer the JWT could resolve —
    # /accounts/{id}/transactions 404s exactly as it would for one that
    # never existed.
    assert (
        client.get(
            f"/accounts/{account_id}/transactions", headers={"Authorization": f"Bearer {token}"}
        ).status_code
        == 404
    )


def test_delete_unknown_customer_404s(client):
    response = client.delete("/simulate/customer/does-not-exist")
    assert response.status_code == 404


# ============================================================================
# GET /internal/customers/lookup
# ============================================================================


def test_internal_lookup_resolves_known_customer(client):
    _seed_customer(client)

    response = client.get(
        "/internal/customers/lookup",
        params={"customer_bank_id": "cust-001"},
        headers={"X-Internal-Secret": config.internal_secret()},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "customer@example.com"
    assert body["customer_id"]


def test_internal_lookup_missing_secret_401s(client):
    _seed_customer(client)
    response = client.get("/internal/customers/lookup", params={"customer_bank_id": "cust-001"})
    assert response.status_code == 401


def test_internal_lookup_wrong_secret_403s(client):
    _seed_customer(client)
    response = client.get(
        "/internal/customers/lookup",
        params={"customer_bank_id": "cust-001"},
        headers={"X-Internal-Secret": "wrong-secret"},
    )
    assert response.status_code == 403


def test_internal_lookup_unknown_customer_404s(client):
    response = client.get(
        "/internal/customers/lookup",
        params={"customer_bank_id": "does-not-exist"},
        headers={"X-Internal-Secret": config.internal_secret()},
    )
    assert response.status_code == 404


# ============================================================================
# GET /accounts, GET /accounts/{id}/transactions
# ============================================================================


def test_list_accounts_scoped_to_bearer_token_customer(client):
    body = _seed_customer(client)
    token = _bearer_token(body["customer_id"])

    response = client.get("/accounts", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    accounts = response.json()
    assert len(accounts) == 1
    assert accounts[0]["external_account_id"] == body["accounts"][0]["external_account_id"]


def test_list_accounts_missing_token_401s(client):
    response = client.get("/accounts")
    assert response.status_code == 401


def test_list_accounts_invalid_token_401s(client):
    response = client.get("/accounts", headers={"Authorization": "Bearer not-a-real-jwt"})
    assert response.status_code == 401


def test_list_accounts_token_missing_exp_401s(client):
    # app.auth requires exp as an essential claim — a token that omits it
    # entirely must not be treated as "never expires".
    header = {"alg": "HS256"}
    payload = {"sub": "any-customer-id", "provider": "mock_bank"}
    token = jwt.encode(header, payload, config.jwt_secret()).decode("utf-8")

    response = client.get("/accounts", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401


def test_list_accounts_alg_none_token_401s(client):
    # app.auth restricts decoding to HS256 explicitly — authlib.jose's own
    # default `jwt` singleton would otherwise accept an unsigned {"alg":
    # "none"} token with an arbitrary sub claim.
    token = _unsigned_none_alg_token(
        {"sub": "any-customer-id", "provider": "mock_bank", "exp": 9999999999}
    )

    response = client.get("/accounts", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401


def test_transactions_for_another_customers_account_404s(client):
    body_a = _seed_customer(client, customer_bank_id="cust-a", email="a@example.com")
    body_b = _seed_customer(client, customer_bank_id="cust-b", email="b@example.com")
    token_b = _bearer_token(body_b["customer_id"])
    account_a_id = body_a["accounts"][0]["external_account_id"]

    response = client.get(
        f"/accounts/{account_a_id}/transactions", headers={"Authorization": f"Bearer {token_b}"}
    )

    assert response.status_code == 404


# ============================================================================
# POST /simulate/transaction
# ============================================================================


def test_simulate_transaction_creates_transaction_and_attempts_webhook(client):
    body = _seed_customer(client)
    account_id = body["accounts"][0]["external_account_id"]

    with patch(
        "app.routes_simulate.requests.post", return_value=_FakeWebhookResponse()
    ) as mock_post:
        response = client.post("/simulate/transaction", json={"account_id": account_id})

    assert response.status_code == 201
    payload = response.json()
    assert payload["webhook_delivery"]["success"] is True
    assert mock_post.call_args.kwargs["json"]["provider_slug"] == "mock_bank"
    assert mock_post.call_args.kwargs["json"]["external_account_id"] == account_id
    assert mock_post.call_args.kwargs["headers"]["X-Webhook-Secret"] == config.webhook_secret()

    # Landed in this service's own ledger regardless of webhook delivery.
    token = _bearer_token(body["customer_id"])
    transactions = client.get(
        f"/accounts/{account_id}/transactions", headers={"Authorization": f"Bearer {token}"}
    ).json()
    assert len(transactions) == 1


def test_simulate_transaction_unknown_account_404s(client):
    response = client.post(
        "/simulate/transaction", json={"account_id": "00000000-0000-0000-0000-000000000000"}
    )
    assert response.status_code == 404


def test_simulate_transaction_no_accounts_at_all_404s(client):
    response = client.post("/simulate/transaction", json={})
    assert response.status_code == 404
