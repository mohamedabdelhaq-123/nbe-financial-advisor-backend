"""
Read-only-enforcement coverage for POST /statements/{id}/transactions/
(StatementTransactionApprovalView) — the gap identified this session: it
had no check at all preventing a statement from being approved onto a
bank-integrated account, letting a synced (read-only) account get
"statement"-sourced transactions written to it just like any manual one.
Fixed by the same assert_account_mutable() every other write path already
goes through, not new logic — so these tests are the first real coverage
of that call, both for the account_id-override path and whatever
normalization already inferred.
"""

import pytest
from rest_framework.test import APIClient

from core.models import BankAccount, StatementFile, Transaction, User


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email="statement-approval-test@example.com", password="x", name="Statement Approval Test"
    )


@pytest.fixture
def client(user):
    api_client = APIClient()
    api_client.force_authenticate(user=user)
    return api_client


@pytest.fixture
def synced_account(user):
    return BankAccount.objects.create(
        user=user,
        bank_name="Mock National Bank",
        masked_account_number="****1234",
        link_type=BankAccount.LINK_TYPE_SYNCED,
    )


@pytest.fixture
def manual_account(user):
    return BankAccount.objects.create(
        user=user,
        bank_name="Manual Bank",
        masked_account_number="****9999",
        link_type=BankAccount.LINK_TYPE_MANUAL,
    )


def _approval_payload():
    return {
        "transactions": [
            {
                "transaction_date": "2026-07-01",
                "merchant_raw": "Carrefour",
                "amount": "150.00",
                "transaction_type": "debit",
            }
        ]
    }


def test_approval_already_resolved_to_synced_account_rejected(client, user, synced_account):
    statement = StatementFile.objects.create(
        user=user,
        seaweed_file_id="raw/abc",
        checksum="a" * 64,
        status=StatementFile.STATUS_NORMALIZED,
        account=synced_account,
    )

    response = client.post(
        f"/statements/{statement.id}/transactions/", _approval_payload(), format="json"
    )

    assert response.status_code == 422
    assert response.data["error"]["code"] == "synced_account_read_only"
    assert Transaction.objects.count() == 0
    statement.refresh_from_db()
    assert statement.status == StatementFile.STATUS_NORMALIZED


def test_approval_account_id_override_to_synced_account_rejected(
    client, user, synced_account, manual_account
):
    statement = StatementFile.objects.create(
        user=user,
        seaweed_file_id="raw/def",
        checksum="b" * 64,
        status=StatementFile.STATUS_NORMALIZED,
        account=manual_account,
    )

    response = client.post(
        f"/statements/{statement.id}/transactions/",
        {**_approval_payload(), "account_id": str(synced_account.id)},
        format="json",
    )

    assert response.status_code == 422
    assert response.data["error"]["code"] == "synced_account_read_only"
    assert Transaction.objects.count() == 0
    statement.refresh_from_db()
    # The override itself is applied before the check runs, same as before —
    # only the transaction write and status advance are blocked.
    assert statement.account_id == synced_account.id
    assert statement.status == StatementFile.STATUS_NORMALIZED


def test_approval_against_manual_account_still_allowed(client, user, manual_account):
    statement = StatementFile.objects.create(
        user=user,
        seaweed_file_id="raw/ghi",
        checksum="c" * 64,
        status=StatementFile.STATUS_NORMALIZED,
        account=manual_account,
    )

    response = client.post(
        f"/statements/{statement.id}/transactions/", _approval_payload(), format="json"
    )

    assert response.status_code == 200
    assert Transaction.objects.filter(account=manual_account).count() == 1
    statement.refresh_from_db()
    assert statement.status == StatementFile.STATUS_APPROVED
