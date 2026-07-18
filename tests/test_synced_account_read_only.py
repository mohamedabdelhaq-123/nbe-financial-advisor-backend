"""
Endpoint-level tests for assert_account_mutable() (core/views/profile.py) —
the shared read-only-enforcement check every write path that touches a
BankAccount or its transactions goes through. Covers all three call sites:
BankAccountDetailView.patch/delete, TransactionListCreateView.post,
TransactionDetailView.patch/delete.

A manual-account counterpart is asserted for each, to confirm this doesn't
regress the pre-existing manual-account write flow.
"""

import pytest
from rest_framework.test import APIClient

from core.models import BankAccount, Transaction, User


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email="read-only-test@example.com", password="x", name="Read Only Test"
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


def _assert_read_only_422(response):
    assert response.status_code == 422
    assert response.data["error"]["code"] == "synced_account_read_only"


# ============================================================================
# BankAccountDetailView
# ============================================================================


def test_patch_synced_account_rejected(client, synced_account):
    response = client.patch(f"/accounts/{synced_account.id}/", {"bank_name": "Renamed"})
    _assert_read_only_422(response)
    synced_account.refresh_from_db()
    assert synced_account.bank_name == "Mock National Bank"


def test_delete_synced_account_rejected(client, synced_account):
    response = client.delete(f"/accounts/{synced_account.id}/")
    _assert_read_only_422(response)
    assert BankAccount.objects.filter(id=synced_account.id).exists()


def test_patch_manual_account_still_allowed(client, manual_account):
    response = client.patch(f"/accounts/{manual_account.id}/", {"bank_name": "Renamed"})
    assert response.status_code == 200
    manual_account.refresh_from_db()
    assert manual_account.bank_name == "Renamed"


def test_delete_manual_account_still_allowed(client, manual_account):
    response = client.delete(f"/accounts/{manual_account.id}/")
    assert response.status_code == 204
    assert not BankAccount.objects.filter(id=manual_account.id).exists()


# ============================================================================
# TransactionListCreateView.post
# ============================================================================


def test_manual_entry_against_synced_account_rejected(client, synced_account):
    response = client.post(
        "/transactions/",
        {
            "account_id": str(synced_account.id),
            "transaction_date": "2026-07-02",
            "merchant_raw": "Talabat",
            "amount": "80.00",
        },
        format="json",
    )
    _assert_read_only_422(response)
    assert Transaction.objects.filter(account=synced_account).count() == 0


def test_manual_entry_against_manual_account_still_allowed(client, manual_account):
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
# TransactionDetailView.patch/delete
# ============================================================================


def test_patch_transaction_on_synced_account_rejected(client, synced_transaction):
    response = client.patch(f"/transactions/{synced_transaction.id}/", {"category": "food"})
    _assert_read_only_422(response)
    synced_transaction.refresh_from_db()
    assert synced_transaction.category is None


def test_delete_transaction_on_synced_account_rejected(client, synced_transaction):
    response = client.delete(f"/transactions/{synced_transaction.id}/")
    _assert_read_only_422(response)
    assert Transaction.objects.filter(id=synced_transaction.id).exists()


def test_patch_transaction_on_manual_account_still_allowed(client, manual_transaction):
    response = client.patch(f"/transactions/{manual_transaction.id}/", {"category": "food"})
    assert response.status_code == 200
    manual_transaction.refresh_from_db()
    assert manual_transaction.category is not None


def test_delete_transaction_on_manual_account_still_allowed(client, manual_transaction):
    response = client.delete(f"/transactions/{manual_transaction.id}/")
    assert response.status_code == 204
    assert not Transaction.objects.filter(id=manual_transaction.id).exists()
