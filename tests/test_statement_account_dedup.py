"""
Unit tests for core/tasks/statements.py::run_normalization_phase's
BankAccount resolution — PLAN.md Checkpoint 4.

Calls run_normalization_phase directly (not through the full upload ->
Celery-task flow used by tests/test_statements_tasks.py) with
ai_service.normalize_statement monkeypatched, so these don't depend on
settings.USE_MOCK_AI_SERVICE/a reachable AI service at all — just the
BankAccount get_or_create logic itself.

Previously this was keyed on (user, bank_name, masked_account_number),
where masked_account_number came from the mock AI service's
"****" + statement.checksum[:4] — different for every upload by
construction, so no two statements from the same real bank account could
ever match an existing row. Every no-account_id upload created a brand-new
duplicate BankAccount.
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


def _fake_normalize(bank_name, account_hint):
    def _normalize(ocr_result_id):
        return {
            "normalized_json": {
                "bank_name": bank_name,
                "account_hint": account_hint,
                "transactions": [],
            },
            "model_used": "fake-model",
        }

    return _normalize


def test_two_uploads_same_bank_reuse_the_same_account(monkeypatch, user):
    # Different account_hint per upload (as the checksum-derived mock
    # actually produces) — this is exactly the case that used to duplicate.
    monkeypatch.setattr(
        statements_task_module.ai_service,
        "normalize_statement",
        _fake_normalize("National Bank of Egypt", "****aaaa"),
    )
    stmt1 = _make_statement(user, "checksum-1")
    statements_task_module.run_normalization_phase(stmt1)

    monkeypatch.setattr(
        statements_task_module.ai_service,
        "normalize_statement",
        _fake_normalize("National Bank of Egypt", "****bbbb"),
    )
    stmt2 = _make_statement(user, "checksum-2")
    statements_task_module.run_normalization_phase(stmt2)

    stmt1.refresh_from_db()
    stmt2.refresh_from_db()
    assert stmt1.account_id == stmt2.account_id
    assert (
        BankAccount.objects.filter(user=user, bank_name="National Bank of Egypt").count() == 1
    )


def test_different_bank_name_still_creates_a_distinct_account(monkeypatch, user):
    monkeypatch.setattr(
        statements_task_module.ai_service, "normalize_statement", _fake_normalize("NBE", "****aaaa")
    )
    stmt1 = _make_statement(user, "checksum-3")
    statements_task_module.run_normalization_phase(stmt1)

    monkeypatch.setattr(
        statements_task_module.ai_service, "normalize_statement", _fake_normalize("CIB", "****bbbb")
    )
    stmt2 = _make_statement(user, "checksum-4")
    statements_task_module.run_normalization_phase(stmt2)

    stmt1.refresh_from_db()
    stmt2.refresh_from_db()
    assert stmt1.account_id != stmt2.account_id


def test_statement_with_preselected_account_is_left_untouched(monkeypatch, user):
    account = BankAccount.objects.create(
        user=user, bank_name="Preselected Bank", masked_account_number="0000"
    )
    monkeypatch.setattr(
        statements_task_module.ai_service,
        "normalize_statement",
        _fake_normalize("Some Other Bank", "****zzzz"),
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
        _fake_normalize("National Bank of Egypt", "****aaaa"),
    )
    stmt = _make_statement(user, "checksum-6")

    statements_task_module.run_normalization_phase(stmt)

    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [user.email]
    assert mail.outbox[0].subject == "Your statement is ready to review"
