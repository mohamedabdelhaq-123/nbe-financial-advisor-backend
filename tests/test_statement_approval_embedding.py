"""Coverage for the best-effort embed_transactions call added to
StatementTransactionApprovalView.post — transactions.embedding was
populated but never triggered anywhere in the real ingestion pipeline, so
the AI service's find_similar_transactions tool had nothing to search.
This wires the one real statement-approval creation path to call it once
per approved batch, outside the atomic block, and never let a failure
there affect approval itself.
"""

from unittest.mock import MagicMock

import pytest
from rest_framework.test import APIClient

from core.models import BankAccount, StatementFile, Transaction, User
from services import ai_service


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email="statement-embed-test@example.com", password="x", name="Statement Embed Test"
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


def _approval_payload(**overrides):
    row = {
        "transaction_date": "2026-07-01",
        "merchant_raw": "Carrefour",
        "amount": "150.00",
        "transaction_type": "debit",
    }
    row.update(overrides)
    return {"transactions": [row]}


def test_approval_calls_embed_transactions_for_newly_created_rows(
    client, user, manual_account, monkeypatch
):
    statement = StatementFile.objects.create(
        user=user,
        seaweed_file_id="raw/embed-1",
        checksum="d" * 64,
        status=StatementFile.STATUS_NORMALIZED,
        account=manual_account,
    )

    mock_client = MagicMock()
    monkeypatch.setattr(ai_service, "get_client", lambda: mock_client)

    response = client.post(
        f"/statements/{statement.id}/transactions/", _approval_payload(), format="json"
    )

    assert response.status_code == 200
    created = Transaction.objects.get(account=manual_account)
    mock_client.embed_transactions.assert_called_once_with([str(created.id)])


def test_approval_skips_embed_call_when_every_row_is_a_duplicate(
    client, user, manual_account, monkeypatch
):
    Transaction.objects.create(
        user=user,
        account=manual_account,
        transaction_date="2026-07-01",
        merchant_raw="Carrefour",
        amount="150.00",
        transaction_type="debit",
        source="statement",
    )
    statement = StatementFile.objects.create(
        user=user,
        seaweed_file_id="raw/embed-2",
        checksum="e" * 64,
        status=StatementFile.STATUS_NORMALIZED,
        account=manual_account,
    )

    mock_client = MagicMock()
    monkeypatch.setattr(ai_service, "get_client", lambda: mock_client)

    response = client.post(
        f"/statements/{statement.id}/transactions/", _approval_payload(), format="json"
    )

    assert response.status_code == 200
    assert response.data["resolved"][0]["transaction_id"] is None
    mock_client.embed_transactions.assert_not_called()


def test_approval_succeeds_even_if_embedding_call_fails(client, user, manual_account, monkeypatch):
    statement = StatementFile.objects.create(
        user=user,
        seaweed_file_id="raw/embed-3",
        checksum="f" * 64,
        status=StatementFile.STATUS_NORMALIZED,
        account=manual_account,
    )

    mock_client = MagicMock()
    mock_client.embed_transactions.side_effect = ai_service.AIServiceError("ai-service down")
    monkeypatch.setattr(ai_service, "get_client", lambda: mock_client)

    response = client.post(
        f"/statements/{statement.id}/transactions/", _approval_payload(), format="json"
    )

    assert response.status_code == 200
    assert Transaction.objects.filter(account=manual_account).count() == 1
    statement.refresh_from_db()
    assert statement.status == StatementFile.STATUS_APPROVED
