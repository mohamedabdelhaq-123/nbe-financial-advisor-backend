"""
Endpoint-level tests for GET /dashboard (core/views/budgets.py::DashboardView) —
`period`/`account_id` filtering (PLAN.md Checkpoint 1). Transaction dates are
computed relative to date.today() (not hardcoded) so these pass regardless of
what day they're run.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from core.models import BankAccount, Budget, BudgetAllocation, Category, Transaction, User


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email="dashboard-test@example.com", password="x", name="Dashboard Test"
    )


@pytest.fixture
def other_user(db):
    return User.objects.create_user(
        email="dashboard-other@example.com", password="x", name="Someone Else"
    )


@pytest.fixture
def client(user):
    api_client = APIClient()
    api_client.force_authenticate(user=user)
    return api_client


@pytest.fixture
def category(db):
    # "food" is seeded by core/migrations/0011_category.py's data migration —
    # get_or_create so this works whether or not that seed data is present.
    category, _ = Category.objects.get_or_create(
        name="food", defaults={"label": "Food", "category_type": "expense"}
    )
    return category


@pytest.fixture
def account(user):
    return BankAccount.objects.create(user=user, bank_name="NBE", masked_account_number="1234")


@pytest.fixture
def other_account(user):
    return BankAccount.objects.create(user=user, bank_name="CIB", masked_account_number="5678")


def _make_txn(user, account, category, *, txn_date, amount, merchant, txn_type="debit"):
    return Transaction.objects.create(
        user=user,
        account=account,
        category=category,
        transaction_date=txn_date,
        amount=Decimal(str(amount)),
        merchant_raw=merchant,
        transaction_type=txn_type,
    )


@pytest.fixture
def budget(user, category):
    budget = Budget.objects.create(user=user, name="My Plan")
    BudgetAllocation.objects.create(
        budget=budget,
        category=category,
        allocated_percentage=Decimal("100.00"),
        allocated_amount=Decimal("1000.00"),
    )
    return budget


class TestDashboardPeriodWindow:
    def test_default_period_is_this_month(self, client, user, account, category, budget):
        today = date.today()
        month_start = today.replace(day=1)
        # Inside this month's window.
        _make_txn(user, account, category, txn_date=month_start, amount="50.00", merchant="a")
        # Outside it — the day before this month started.
        _make_txn(
            user,
            account,
            category,
            txn_date=month_start - timedelta(days=1),
            amount="999.00",
            merchant="b",
        )

        resp = client.get("/dashboard/")
        assert resp.status_code == 200
        assert Decimal(resp.data["metrics"]["current_month_spend"]) == Decimal("50.00")

    def test_invalid_period_422(self, client, budget):
        resp = client.get("/dashboard/", {"period": "not_a_real_period"})
        assert resp.status_code == 422

    def test_invalid_period_422_even_without_a_budget(self, client):
        # No `budget` fixture used — has_plan=False branch; period is still
        # validated regardless of plan state.
        resp = client.get("/dashboard/", {"period": "bogus"})
        assert resp.status_code == 422

    @pytest.mark.parametrize("period", ["this_month", "last_month", "last_3_months", "this_year"])
    def test_each_period_value_is_accepted(self, client, budget, period):
        resp = client.get("/dashboard/", {"period": period})
        assert resp.status_code == 200

    def test_preceding_window_is_equal_length_to_current_window(
        self, client, user, account, category, budget
    ):
        today = date.today()
        start = today.replace(day=1)
        length_days = (today - start).days + 1
        prev_end = start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=length_days - 1)
        just_before_prev_window = prev_start - timedelta(days=1)

        _make_txn(user, account, category, txn_date=start, amount="10.00", merchant="curr")
        _make_txn(user, account, category, txn_date=prev_start, amount="20.00", merchant="prev")
        _make_txn(
            user,
            account,
            category,
            txn_date=just_before_prev_window,
            amount="999.00",
            merchant="too-old",
        )

        resp = client.get("/dashboard/", {"period": "this_month"})
        assert resp.status_code == 200
        metrics = resp.data["metrics"]
        assert Decimal(metrics["current_month_spend"]) == Decimal("10.00")
        assert Decimal(metrics["previous_month_spend"]) == Decimal("20.00")
        assert metrics["spend_change_percentage"] == pytest.approx(-50.0)


class TestDashboardAccountFilter:
    def test_account_id_restricts_spend_and_inflow(
        self, client, user, account, other_account, category, budget
    ):
        today = date.today()
        _make_txn(user, account, category, txn_date=today, amount="30.00", merchant="a1")
        _make_txn(user, other_account, category, txn_date=today, amount="999.00", merchant="a2")

        resp = client.get("/dashboard/", {"account_id": str(account.id)})
        assert resp.status_code == 200
        assert Decimal(resp.data["metrics"]["current_month_spend"]) == Decimal("30.00")

    def test_account_id_restricts_net_worth_to_that_accounts_balance(
        self, client, user, account, other_account, category, budget
    ):
        today = date.today()
        _make_txn(
            user,
            account,
            category,
            txn_date=today,
            amount="15.00",
            merchant="bal-a",
            txn_type="credit",
        )
        _make_txn(
            user,
            other_account,
            category,
            txn_date=today,
            amount="500.00",
            merchant="bal-b",
            txn_type="credit",
        )
        # current_balance is read from the latest transaction's `balance`
        # field, not `amount` — set it explicitly.
        Transaction.objects.filter(account=account).update(balance=Decimal("123.45"))
        Transaction.objects.filter(account=other_account).update(balance=Decimal("999.99"))

        resp = client.get("/dashboard/", {"account_id": str(account.id)})
        assert resp.status_code == 200
        assert Decimal(resp.data["net_worth"]["total_across_accounts"]) == Decimal("123.45")

    def test_unowned_account_id_404s(self, client, other_user, budget):
        unowned_account = BankAccount.objects.create(
            user=other_user, bank_name="Other Bank", masked_account_number="0000"
        )
        resp = client.get("/dashboard/", {"account_id": str(unowned_account.id)})
        assert resp.status_code == 404

    def test_unowned_account_id_404s_even_without_a_budget(self, client, other_user):
        unowned_account = BankAccount.objects.create(
            user=other_user, bank_name="Other Bank", masked_account_number="0000"
        )
        resp = client.get("/dashboard/", {"account_id": str(unowned_account.id)})
        assert resp.status_code == 404

    def test_allocations_summary_percentage_used_respects_account_filter(
        self, client, user, account, other_account, category, budget
    ):
        today = date.today()
        _make_txn(user, account, category, txn_date=today, amount="100.00", merchant="c1")
        _make_txn(user, other_account, category, txn_date=today, amount="900.00", merchant="c2")

        resp = client.get("/dashboard/", {"account_id": str(account.id)})
        assert resp.status_code == 200
        summary = resp.data["allocations_summary"][0]
        # allocated_amount on the budget fixture is 1000.00 -> 100/1000 = 10%.
        assert summary["percentage_used"] == pytest.approx(10.0)


class TestDashboardNoPlan:
    def test_has_plan_false_still_reports_real_net_worth(self, client, user, account):
        Transaction.objects.create(
            user=user,
            account=account,
            transaction_date=date.today(),
            amount=Decimal("77.00"),
            merchant_raw="no-plan-txn",
            transaction_type="credit",
            balance=Decimal("77.00"),
        )
        resp = client.get("/dashboard/")
        assert resp.status_code == 200
        assert resp.data["has_plan"] is False
        assert Decimal(resp.data["net_worth"]["total_across_accounts"]) == Decimal("77.00")
