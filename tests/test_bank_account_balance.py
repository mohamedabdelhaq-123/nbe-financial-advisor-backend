from datetime import date
from decimal import Decimal

import pytest

from core.models import BankAccount, Transaction, User


@pytest.fixture
def balance_account(db):
    user = User.objects.create_user(
        email="balance-regression@example.com",
        password="test-pass",
        name="Balance Regression",
    )
    return BankAccount.objects.create(
        user=user,
        bank_name="Test Bank",
        account_number="1001",
        currency="EGP",
    )


def _transaction(account, *, txn_date, amount, txn_type, balance=None):
    return Transaction.objects.create(
        user=account.user,
        account=account,
        transaction_date=txn_date,
        amount=Decimal(amount),
        transaction_type=txn_type,
        balance=Decimal(balance) if balance is not None else None,
    )


@pytest.mark.django_db
def test_current_balance_applies_amount_only_movements_after_latest_stated_balance(
    balance_account,
):
    # This older movement is already represented by the statement anchor and
    # must not be counted a second time.
    _transaction(
        balance_account,
        txn_date=date(2024, 10, 22),
        amount="5000.00",
        txn_type="credit",
    )
    _transaction(
        balance_account,
        txn_date=date(2024, 10, 23),
        amount="2000.00",
        txn_type="debit",
        balance="15118.51",
    )
    _transaction(
        balance_account,
        txn_date=date(2026, 8, 27),
        amount="1100.00",
        txn_type="debit",
    )
    _transaction(
        balance_account,
        txn_date=date(2026, 8, 29),
        amount="150000.00",
        txn_type="credit",
    )
    _transaction(
        balance_account,
        txn_date=date(2026, 8, 30),
        amount="1000000000.00",
        txn_type="credit",
    )

    assert balance_account.current_balance == Decimal("1000164018.51")


@pytest.mark.django_db
def test_current_balance_derives_full_ledger_when_no_stated_balance(balance_account):
    _transaction(
        balance_account,
        txn_date=date(2026, 8, 29),
        amount="500.00",
        txn_type="credit",
    )
    _transaction(
        balance_account,
        txn_date=date(2026, 8, 30),
        amount="125.50",
        txn_type=None,
    )

    assert balance_account.current_balance == Decimal("374.50")
