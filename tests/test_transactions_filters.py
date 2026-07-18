"""
Endpoint-level tests for GET /transactions filtering
(core/filters/aggregations.py::TransactionFilterSet) — PLAN.md Checkpoint 2.

Covers the two confirmed regressions (category matching a Category FK's pk
instead of its name; no `type` param at all) plus a regression test for
account_id, which was already correct.
"""

from datetime import date
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from core.models import BankAccount, Category, Transaction, User


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email="txn-filters-test@example.com", password="x", name="Txn Filters Test"
    )


@pytest.fixture
def client(user):
    api_client = APIClient()
    api_client.force_authenticate(user=user)
    return api_client


@pytest.fixture
def account(user):
    return BankAccount.objects.create(user=user, bank_name="NBE", masked_account_number="1234")


@pytest.fixture
def other_account(user):
    return BankAccount.objects.create(user=user, bank_name="CIB", masked_account_number="5678")


@pytest.fixture
def food(db):
    category, _ = Category.objects.get_or_create(
        name="food", defaults={"label": "Food", "category_type": "expense"}
    )
    return category


@pytest.fixture
def transport(db):
    category, _ = Category.objects.get_or_create(
        name="transport", defaults={"label": "Transport", "category_type": "expense"}
    )
    return category


def _make_txn(user, account, *, merchant, amount="10.00", txn_type="debit", category=None):
    return Transaction.objects.create(
        user=user,
        account=account,
        category=category,
        transaction_date=date.today(),
        amount=Decimal(amount),
        merchant_raw=merchant,
        transaction_type=txn_type,
    )


class TestCategoryFilter:
    def test_matches_by_category_name(self, client, user, account, food, transport):
        _make_txn(user, account, merchant="grocery", category=food)
        _make_txn(user, account, merchant="uber", category=transport)

        resp = client.get("/transactions/", {"category": "food"})
        assert resp.status_code == 200
        merchants = {row["merchant_raw"] for row in resp.data["results"]}
        assert merchants == {"grocery"}

    def test_matches_case_insensitively(self, client, user, account, food):
        _make_txn(user, account, merchant="grocery", category=food)

        resp = client.get("/transactions/", {"category": "FOOD"})
        assert resp.status_code == 200
        assert len(resp.data["results"]) == 1

    def test_unknown_category_name_returns_empty_not_error(self, client, user, account, food):
        _make_txn(user, account, merchant="grocery", category=food)

        resp = client.get("/transactions/", {"category": "not-a-real-category"})
        assert resp.status_code == 200
        assert resp.data["results"] == []


class TestTypeFilter:
    def test_income_returns_only_credit_rows(self, client, user, account):
        _make_txn(user, account, merchant="salary", txn_type="credit")
        _make_txn(user, account, merchant="groceries", txn_type="debit")
        _make_txn(user, account, merchant="bank-fee", txn_type="fee")
        _make_txn(user, account, merchant="wire", txn_type="transfer")

        resp = client.get("/transactions/", {"type": "income"})
        assert resp.status_code == 200
        merchants = {row["merchant_raw"] for row in resp.data["results"]}
        assert merchants == {"salary"}

    def test_expense_returns_debit_fee_and_transfer_not_credit(self, client, user, account):
        _make_txn(user, account, merchant="salary", txn_type="credit")
        _make_txn(user, account, merchant="groceries", txn_type="debit")
        _make_txn(user, account, merchant="bank-fee", txn_type="fee")
        _make_txn(user, account, merchant="wire", txn_type="transfer")

        resp = client.get("/transactions/", {"type": "expense"})
        assert resp.status_code == 200
        merchants = {row["merchant_raw"] for row in resp.data["results"]}
        assert merchants == {"groceries", "bank-fee", "wire"}

    def test_invalid_type_value_is_rejected_not_silently_ignored(self, client, user, account):
        _make_txn(user, account, merchant="groceries", txn_type="debit")

        resp = client.get("/transactions/", {"type": "bogus"})
        assert resp.status_code == 422


class TestAccountIdFilter:
    """Regression only — account_id was already correctly implemented."""

    def test_account_id_scopes_to_one_account(self, client, user, account, other_account):
        _make_txn(user, account, merchant="a1")
        _make_txn(user, other_account, merchant="a2")

        resp = client.get("/transactions/", {"account_id": str(account.id)})
        assert resp.status_code == 200
        merchants = {row["merchant_raw"] for row in resp.data["results"]}
        assert merchants == {"a1"}
