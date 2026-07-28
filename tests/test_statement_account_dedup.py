"""
Unit tests for core/tasks/statements.py::run_normalization_phase's
BankAccount resolution — PLAN.md Checkpoint 4.

Calls run_normalization_phase directly (not through the full upload ->
Celery-task flow used by tests/test_statements_tasks.py) with
ai_service.normalize_statement monkeypatched, so these don't depend on
settings.USE_MOCK_AI_SERVICE/a reachable AI service at all — just the
BankAccount get_or_create logic itself.

Previously this was keyed on (user, bank_name) only, deliberately ignoring
the AI service's account_hint field — account_hint was a masked hint
derived from "****" + statement.checksum[:4], different for every upload by
construction, so it couldn't be trusted to match two statements from the
same real bank account.

Now that the AI service returns account_number — the real, unmasked account
number as printed in the source (spec 016-normalizer-pipeline-rework) —
matching is keyed on (user, bank_name, account_number) instead: two uploads
sharing the same real account number reuse one BankAccount, and two uploads
with genuinely different account numbers now correctly create separate
BankAccount rows (previously untestable/impossible to express under the old
hint-based design, since the hint was never reliable enough to assert either
way).
"""

import pytest
from django.core import mail

import core.tasks.statements as statements_task_module
from core.models import BankAccount, StatementFile, StatementOcrResult, User


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email="statement-dedup-test@example.com", password="x", name="Dedup Test"
    )


def _make_statement(user, checksum):
    statement = StatementFile.objects.create(
        user=user, seaweed_file_id="fake/prefix", checksum=checksum
    )
    StatementOcrResult.objects.create(statement=statement, seaweed_file_id="fake/prefix")
    return statement


def _fake_normalize(bank_name, account_number):
    def _normalize(ocr_result_id):
        return {
            "normalized_json": {
                "bank_name": bank_name,
                "account_number": account_number,
                "transactions": [],
            },
            "model_used": "fake-model",
        }

    return _normalize


def test_two_uploads_same_bank_same_account_number_reuse_the_same_account(monkeypatch, user):
    # Same real account_number both times (the AI service extracts it
    # unmasked and deterministically from the source document) — should
    # land on the same BankAccount.
    monkeypatch.setattr(
        statements_task_module.ai_service,
        "normalize_statement",
        _fake_normalize("National Bank of Egypt", "4213010248203200016"),
    )
    stmt1 = _make_statement(user, "checksum-1")
    statements_task_module.run_normalization_phase(stmt1)

    monkeypatch.setattr(
        statements_task_module.ai_service,
        "normalize_statement",
        _fake_normalize("National Bank of Egypt", "4213010248203200016"),
    )
    stmt2 = _make_statement(user, "checksum-2")
    statements_task_module.run_normalization_phase(stmt2)

    stmt1.refresh_from_db()
    stmt2.refresh_from_db()
    assert stmt1.account_id == stmt2.account_id
    assert BankAccount.objects.filter(user=user, bank_name="National Bank of Egypt").count() == 1


def test_two_uploads_same_bank_different_account_number_creates_distinct_accounts(
    monkeypatch, user
):
    # Genuinely different real account numbers for the same bank — this is
    # the case the old hint-based matching could never distinguish (the
    # hint was never trustworthy enough to key on). Now that account_number
    # is real, two different numbers must land on two different accounts.
    monkeypatch.setattr(
        statements_task_module.ai_service,
        "normalize_statement",
        _fake_normalize("National Bank of Egypt", "4213010248203200016"),
    )
    stmt1 = _make_statement(user, "checksum-1")
    statements_task_module.run_normalization_phase(stmt1)

    monkeypatch.setattr(
        statements_task_module.ai_service,
        "normalize_statement",
        _fake_normalize("National Bank of Egypt", "9988776655443322110"),
    )
    stmt2 = _make_statement(user, "checksum-2")
    statements_task_module.run_normalization_phase(stmt2)

    stmt1.refresh_from_db()
    stmt2.refresh_from_db()
    assert stmt1.account_id != stmt2.account_id
    assert BankAccount.objects.filter(user=user, bank_name="National Bank of Egypt").count() == 2


def test_different_bank_name_still_creates_a_distinct_account(monkeypatch, user):
    monkeypatch.setattr(
        statements_task_module.ai_service,
        "normalize_statement",
        _fake_normalize("NBE", "4213010248203200016"),
    )
    stmt1 = _make_statement(user, "checksum-3")
    statements_task_module.run_normalization_phase(stmt1)

    monkeypatch.setattr(
        statements_task_module.ai_service,
        "normalize_statement",
        _fake_normalize("CIB", "9988776655443322110"),
    )
    stmt2 = _make_statement(user, "checksum-4")
    statements_task_module.run_normalization_phase(stmt2)

    stmt1.refresh_from_db()
    stmt2.refresh_from_db()
    assert stmt1.account_id != stmt2.account_id


def test_statement_resolves_onto_an_existing_synced_account(monkeypatch, user):
    """A statement uploaded for an account the user already linked by bank
    sync must land on that account, not shadow it with a manual duplicate.

    This only holds because the connector contract hands back the real
    account number (services/bank_connectors/base.py) rather than a masked
    one — while sync stored "****3200" and the statement reported the full
    number, the two could never compare equal and every such upload silently
    forked a second account. assert_bank_not_already_synced doesn't cover
    this path: get_or_create here never goes through the view layer.
    """
    synced = BankAccount.objects.create(
        user=user,
        bank_name="National Bank of Egypt",
        account_number="4213010248203200016",
        link_type=BankAccount.LINK_TYPE_SYNCED,
    )
    monkeypatch.setattr(
        statements_task_module.ai_service,
        "normalize_statement",
        _fake_normalize("National Bank of Egypt", "4213010248203200016"),
    )
    stmt = _make_statement(user, "checksum-synced")

    statements_task_module.run_normalization_phase(stmt)

    stmt.refresh_from_db()
    assert stmt.account_id == synced.id
    assert BankAccount.objects.filter(user=user, bank_name="National Bank of Egypt").count() == 1
    # Still the synced row — resolved onto, not downgraded to manual.
    synced.refresh_from_db()
    assert synced.link_type == BankAccount.LINK_TYPE_SYNCED


def test_statement_with_preselected_account_is_left_untouched(monkeypatch, user):
    account = BankAccount.objects.create(
        user=user, bank_name="Preselected Bank", account_number="0000"
    )
    monkeypatch.setattr(
        statements_task_module.ai_service,
        "normalize_statement",
        _fake_normalize("Some Other Bank", "5566778899001122334"),
    )
    stmt = _make_statement(user, "checksum-5")
    stmt.account = account
    stmt.save(update_fields=["account"])

    statements_task_module.run_normalization_phase(stmt)

    stmt.refresh_from_db()
    assert stmt.account_id == account.id
    assert not BankAccount.objects.filter(bank_name="Some Other Bank").exists()


def test_normalization_emails_the_user_that_the_statement_is_ready(monkeypatch, user):
    """PLAN.md Checkpoint 6 — a finished statement upload now also emails
    the user, not just SSE (this call has no fake_redis fixture, so it
    incidentally also proves run_normalization_phase itself never touches
    event_bus directly — only the outer process_statement_pipeline task
    does)."""
    monkeypatch.setattr(
        statements_task_module.ai_service,
        "normalize_statement",
        _fake_normalize("National Bank of Egypt", "4213010248203200016"),
    )
    stmt = _make_statement(user, "checksum-6")

    statements_task_module.run_normalization_phase(stmt)

    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [user.email]
    assert mail.outbox[0].subject == "Your statement is ready to review"
