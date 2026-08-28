"""Coverage for the best-effort embed_transactions call wired into manual
transaction entry (TransactionListCreateView.post) and edits
(TransactionDetailView.patch) — the counterpart to
test_statement_approval_embedding.py, which covers the statement-approval
path. Together the three creation/edit paths all keep
Transaction.embedding in sync so the AI service's find_similar_transactions
tool can find any transaction, not just statement-approved ones.

Only merchant_raw/category/amount/transaction_date feed the embedded
summary text (see core/views/aggregations.py's _EMBEDDING_RELEVANT_FIELDS
comment) — editing only transaction_type or is_recurring must not trigger
a re-embed.
"""

from unittest.mock import MagicMock

import pytest
from rest_framework.test import APIClient

from core.models import BankAccount, Transaction, User
from services import ai_service


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email="txn-embed-test@example.com", password="x", name="Transaction Embed Test"
    )


@pytest.fixture
def client(user):
    api_client = APIClient()
    api_client.force_authenticate(user=user)
    return api_client


@pytest.fixture
def manual_account(user):
    return BankAccount.objects.create(
        user=user,
        bank_name="Manual Bank",
        account_number="1000200030009999",
        link_type=BankAccount.LINK_TYPE_MANUAL,
    )


@pytest.fixture
def manual_transaction(user, manual_account):
    return Transaction.objects.create(
        user=user,
        account=manual_account,
        source="manual",
        transaction_date="2026-07-01",
        merchant_raw="Carrefour",
        amount="150.00",
        transaction_type="debit",
    )


@pytest.fixture
def synced_account(user):
    return BankAccount.objects.create(
        user=user,
        bank_name="Mock National Bank",
        account_number="1000200030001234",
        link_type=BankAccount.LINK_TYPE_SYNCED,
    )


@pytest.fixture
def synced_transaction(user, synced_account):
    return Transaction.objects.create(
        user=user,
        account=synced_account,
        source="synced",
        transaction_date="2026-07-01",
        merchant_raw="Carrefour",
        amount="150.00",
        transaction_type="debit",
    )


@pytest.fixture
def mock_ai_client(monkeypatch):
    mock_client = MagicMock()
    monkeypatch.setattr(ai_service, "get_client", lambda: mock_client)
    return mock_client


# ============================================================================
# TransactionListCreateView.post — manual entry
# ============================================================================


def test_manual_entry_embeds_the_new_transaction(client, manual_account, mock_ai_client):
    response = client.post(
        "/transactions/",
        {
            "account_id": str(manual_account.id),
            "transaction_date": "2026-07-02",
            "merchant_raw": "Talabat",
            "amount": "80.00",
        },
        format="json",
    )

    assert response.status_code == 201
    created = Transaction.objects.get(account=manual_account)
    mock_ai_client.embed_transactions.assert_called_once_with([str(created.id)])


def test_manual_entry_succeeds_even_if_embedding_call_fails(client, manual_account, mock_ai_client):
    mock_ai_client.embed_transactions.side_effect = ai_service.AIServiceError("ai-service down")

    response = client.post(
        "/transactions/",
        {
            "account_id": str(manual_account.id),
            "transaction_date": "2026-07-02",
            "merchant_raw": "Talabat",
            "amount": "80.00",
        },
        format="json",
    )

    assert response.status_code == 201
    assert Transaction.objects.filter(account=manual_account).count() == 1


# ============================================================================
# TransactionDetailView.patch — edits
# ============================================================================


@pytest.mark.parametrize(
    "payload",
    [
        {"merchant_raw": "Spinneys"},
        {"category": "food"},
        {"amount": "175.00"},
        {"transaction_date": "2026-07-05"},
    ],
)
def test_patch_re_embeds_on_summary_relevant_field(
    client, manual_transaction, mock_ai_client, payload
):
    response = client.patch(f"/transactions/{manual_transaction.id}/", payload)

    assert response.status_code == 200
    mock_ai_client.embed_transactions.assert_called_once_with([str(manual_transaction.id)])


@pytest.mark.parametrize(
    "payload",
    [
        {"transaction_type": "fee"},
        {"is_recurring": True},
    ],
)
def test_patch_skips_re_embed_on_summary_irrelevant_field(
    client, manual_transaction, mock_ai_client, payload
):
    response = client.patch(f"/transactions/{manual_transaction.id}/", payload)

    assert response.status_code == 200
    mock_ai_client.embed_transactions.assert_not_called()


def test_patch_succeeds_even_if_embedding_call_fails(client, manual_transaction, mock_ai_client):
    mock_ai_client.embed_transactions.side_effect = ai_service.AIServiceError("ai-service down")

    response = client.patch(f"/transactions/{manual_transaction.id}/", {"amount": "200.00"})

    assert response.status_code == 200
    manual_transaction.refresh_from_db()
    assert str(manual_transaction.amount) == "200.00"


def test_patch_synced_transaction_category_re_embeds(client, synced_transaction, mock_ai_client):
    response = client.patch(f"/transactions/{synced_transaction.id}/", {"category": "food"})

    assert response.status_code == 200
    mock_ai_client.embed_transactions.assert_called_once_with([str(synced_transaction.id)])


def test_patch_synced_transaction_is_recurring_skips_re_embed(
    client, synced_transaction, mock_ai_client
):
    response = client.patch(f"/transactions/{synced_transaction.id}/", {"is_recurring": True})

    assert response.status_code == 200
    mock_ai_client.embed_transactions.assert_not_called()
