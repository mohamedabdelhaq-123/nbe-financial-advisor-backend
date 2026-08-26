"""
Unit tests for core/tasks/bank_sync.py's ingest_synced_transactions — called
directly (not via HTTP) since it's the shared landing point for both the
initial backfill (BankConnectionCallbackView) and the ongoing webhook
(BankSyncWebhookView), already exercised indirectly via
tests/test_bank_connections_views.py's callback tests.

fake_redis is required (the task always publishes transaction_synced, and
anomaly_detected when applicable) — same pattern as
tests/test_event_bus.py's stream_user_events()-as-generator assertions.
USE_MOCK_AI_SERVICE stays at its default (True): the mock's anomaly
detection always flags the largest debit/fee transaction in a given
user+account+month as anomalous (services/ai_service.py's
_mock_run_post_ingestion_analysis) — a debit transaction reliably produces
one AnomalyFlag, a credit-only one reliably produces none, which is what the
anomaly-path tests below lean on rather than mocking ai_service directly.
"""

import pytest
from django.core import mail

from core.models import AnomalyFlag, BankAccount, Transaction, User
from core.tasks.bank_sync import ingest_synced_transactions
from services import ai_service, event_bus


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email="bank-sync-task-test@example.com", password="x", name="Bank Sync Task Test"
    )


@pytest.fixture
def account(user):
    return BankAccount.objects.create(
        user=user,
        bank_name="Mock National Bank",
        account_number="1000200030001234",
        link_type=BankAccount.LINK_TYPE_SYNCED,
    )


def _debit_payload(**overrides):
    payload = {
        "external_transaction_id": "txn-1",
        "transaction_date": "2026-07-01",
        "merchant_raw": "Carrefour",
        "amount": "150.00",
        "transaction_type": "debit",
        "currency": "EGP",
        "balance": "4850.00",
    }
    payload.update(overrides)
    return payload


def test_creates_synced_transaction(account):
    ingest_synced_transactions(str(account.id), [_debit_payload()])

    transaction = Transaction.objects.get(account=account)
    assert transaction.source == "synced"
    assert transaction.merchant_raw == "Carrefour"
    assert str(transaction.amount) == "150.00"
    assert transaction.transaction_type == "debit"
    # "Carrefour" matches the "groceries" keyword, which resolve_category()
    # aliases onto the real "food" category — see
    # core/models/categories/merchant_keywords.py.
    assert transaction.category is not None
    assert transaction.category.name == "food"


def test_unrecognised_merchant_lands_on_the_type_fallback_category(account):
    ingest_synced_transactions(
        str(account.id), [_debit_payload(merchant_raw="Some Unknown Merchant Ltd")]
    )

    transaction = Transaction.objects.get(account=account)
    assert transaction.category is not None
    assert transaction.category.name == "other"


def test_unrecognised_merchant_credit_lands_on_the_income_fallback_category(account):
    ingest_synced_transactions(
        str(account.id),
        [_debit_payload(merchant_raw="Some Unknown Merchant Ltd", transaction_type="credit")],
    )

    transaction = Transaction.objects.get(account=account)
    assert transaction.category is not None
    assert transaction.category.name == "other_income"


def test_credit_transaction_from_an_expense_keyword_merchant_never_gets_an_expense_category(
    account,
):
    # "Uber" matches the "transportation" expense keyword, but a payout/refund
    # from Uber is still incoming money — the guess must come from the income
    # keyword list (or its fallback), never bleed an expense category like
    # "transport" onto a credit transaction. See
    # core/models/categories/merchant_keywords.py's direction split.
    ingest_synced_transactions(
        str(account.id), [_debit_payload(merchant_raw="Uber", transaction_type="credit")]
    )

    transaction = Transaction.objects.get(account=account)
    assert transaction.category is not None
    assert transaction.category.category_type == "income"
    assert transaction.category.name == "other_income"


def test_credit_transaction_matching_an_income_keyword_gets_the_income_category(account):
    ingest_synced_transactions(
        str(account.id),
        [_debit_payload(merchant_raw="ACME Corp Payroll", transaction_type="credit")],
    )

    transaction = Transaction.objects.get(account=account)
    assert transaction.category is not None
    assert transaction.category.name == "salary"


def test_missing_merchant_name_leaves_transaction_uncategorised(account):
    ingest_synced_transactions(str(account.id), [_debit_payload(merchant_raw=None)])

    transaction = Transaction.objects.get(account=account)
    assert transaction.category is None


def test_dedupes_against_an_existing_transaction(account, user):
    Transaction.objects.create(
        user=user,
        account=account,
        source="manual",
        transaction_date="2026-07-01",
        merchant_raw="Carrefour",
        amount="150.00",
        transaction_type="debit",
    )

    ingest_synced_transactions(str(account.id), [_debit_payload()])

    assert Transaction.objects.filter(account=account).count() == 1


def test_opposite_direction_is_not_treated_as_a_duplicate(account, user):
    Transaction.objects.create(
        user=user,
        account=account,
        source="manual",
        transaction_date="2026-07-01",
        merchant_raw="Carrefour",
        amount="150.00",
        transaction_type="debit",
    )

    ingest_synced_transactions(str(account.id), [_debit_payload(transaction_type="credit")])

    transactions = Transaction.objects.filter(account=account)
    assert transactions.count() == 2
    assert transactions.filter(transaction_type="debit").count() == 1
    assert transactions.filter(transaction_type="credit").count() == 1


def test_unknown_account_is_a_noop(db):
    # Doesn't raise, doesn't create anything, doesn't publish (no account to
    # scope an SSE event to at all).
    ingest_synced_transactions("00000000-0000-0000-0000-000000000000", [_debit_payload()])
    assert Transaction.objects.count() == 0


def test_publishes_transaction_synced_event(account, fake_redis):
    gen = event_bus.stream_user_events(str(account.user_id))
    next(gen)  # consume ": connected"

    ingest_synced_transactions(str(account.id), [_debit_payload()])

    frame = next(gen)
    assert frame.startswith("event: transaction_synced\n")
    assert f'"account_id": "{account.id}"' in frame
    assert '"count": 1' in frame
    gen.close()


def test_publishes_transaction_event_before_optional_analysis(account, monkeypatch):
    order = []

    def record_event(_user_id, event_type, _payload):
        if event_type == "transaction_synced":
            order.append("transaction_synced")

    class AnalysisClient:
        def run_post_ingestion_analysis(self, *_args, **_kwargs):
            order.append("analysis")
            return {"anomalies": []}

    monkeypatch.setattr(event_bus, "publish_user_event", record_event)
    monkeypatch.setattr(ai_service, "get_client", lambda: AnalysisClient())

    ingest_synced_transactions(str(account.id), [_debit_payload()])

    assert order == ["transaction_synced", "analysis"]


def test_no_new_transactions_still_publishes_zero_count_event(account, user, fake_redis):
    Transaction.objects.create(
        user=user,
        account=account,
        source="manual",
        transaction_date="2026-07-01",
        merchant_raw="Carrefour",
        amount="150.00",
        transaction_type="debit",
    )
    gen = event_bus.stream_user_events(str(account.user_id))
    next(gen)

    ingest_synced_transactions(str(account.id), [_debit_payload()])

    frame = next(gen)
    assert '"count": 0' in frame
    gen.close()


def test_debit_transaction_creates_anomaly_flag_and_event(account, fake_redis):
    gen = event_bus.stream_user_events(str(account.user_id))
    next(gen)

    ingest_synced_transactions(str(account.id), [_debit_payload()])

    anomaly = AnomalyFlag.objects.get(account=account)
    assert anomaly.user == account.user
    assert anomaly.transaction is None  # aggregated (category+month), not per-transaction
    assert anomaly.severity == "medium"  # placeholder — ai_service's mock has no severity field
    assert anomaly.month.strftime("%Y-%m") == "2026-07"

    frames = [next(gen), next(gen)]
    assert any(f.startswith("event: transaction_synced\n") for f in frames)
    assert any(f.startswith("event: anomaly_detected\n") for f in frames)
    gen.close()


def test_credit_only_transaction_creates_no_anomaly(account):
    ingest_synced_transactions(str(account.id), [_debit_payload(transaction_type="credit")])
    assert AnomalyFlag.objects.filter(account=account).count() == 0


def test_sends_notification_email(account):
    # This debit payload is also the largest (only) debit/fee transaction in
    # its month, so USE_MOCK_AI_SERVICE's heuristic flags it as an anomaly
    # too (see test_anomaly_also_sends_a_notification_email below) — that
    # fires its own "Unusual activity detected" email alongside this one, so
    # this asserts on the sync-completed email specifically rather than
    # assuming it's the only thing in the outbox.
    ingest_synced_transactions(str(account.id), [_debit_payload()])

    sync_emails = [m for m in mail.outbox if m.subject == "New transactions synced"]
    assert len(sync_emails) == 1
    sent = sync_emails[0]
    assert sent.to == [account.user.email]
    assert "1" in sent.body  # count of new transactions


def test_anomaly_also_sends_a_notification_email(account, fake_redis, monkeypatch):
    # ai_service.run_post_ingestion_analysis mocked directly (rather than
    # relying on USE_MOCK_AI_SERVICE's own "largest debit/fee transaction in
    # the month" heuristic, which is sensitive to what "today" is relative
    # to the fixed 2026-07-01 transaction date above) so this test exercises
    # exactly the AnomalyFlag-creation -> notify() path added in this
    # checkpoint, independent of that heuristic.
    import core.tasks.bank_sync as bank_sync_module

    def _fake_analysis(user_id, account_id, month):
        return {
            "anomalies": [
                {
                    "category": "food",
                    "month": month,
                    "amount": "500.00",
                    "reason": "unusually large charge",
                }
            ]
        }

    class _FakeClient:
        run_post_ingestion_analysis = staticmethod(_fake_analysis)

    monkeypatch.setattr(bank_sync_module.ai_service, "get_client", lambda: _FakeClient())

    ingest_synced_transactions(str(account.id), [_debit_payload()])

    anomaly_emails = [m for m in mail.outbox if m.subject == "Unusual activity detected"]
    assert len(anomaly_emails) == 1
    assert anomaly_emails[0].to == [account.user.email]
