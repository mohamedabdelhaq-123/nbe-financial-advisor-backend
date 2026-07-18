"""
The statement OCR/normalization pipeline, moved here from core/views/statements.py
so it can run inside a Celery task instead of inline in the request/response
cycle. validate_advance() stays synchronously callable (a double-clicked
retry needs an immediate 422, not a task that fails a few hundred ms later);
everything else — the phase cascade itself — only ever runs from inside
process_statement_pipeline.
"""

from datetime import date
from decimal import Decimal

from celery import shared_task

from core.exceptions import BusinessRuleError
from core.models import (
    BankAccount,
    StatementFile,
    StatementNormalized,
    StatementOcrResult,
)
from services import ai_service, event_bus


def run_extraction_phase(statement: StatementFile) -> None:
    """
    Phase 1/2 of the ingestion pipeline (MinerU/OCR). On failure, status is
    left at uploaded and failure_reason/failed_phase record why —
    PATCH /statements/{id} is then the way to retry this phase (PLAN.md).
    """
    statement.is_processing = True
    statement.save(update_fields=["is_processing"])

    try:
        result = ai_service.process_statement(str(statement.id))
    except Exception as exc:
        statement.failure_reason = str(exc)
        statement.failed_phase = StatementFile.PHASE_EXTRACTION
        statement.is_processing = False
        statement.save(update_fields=["failure_reason", "failed_phase", "is_processing"])
        return

    StatementOcrResult.objects.create(
        statement=statement,
        seaweed_file_id=result["prefix"],
        ocr_engine=result["ocr_engine"],
        confidence_score=Decimal(str(result["confidence_score"])),
    )

    statement.status = StatementFile.STATUS_EXTRACTED
    statement.failure_reason = None
    statement.failed_phase = None
    statement.is_processing = False
    statement.save(update_fields=["status", "failure_reason", "failed_phase", "is_processing"])


def run_normalization_phase(statement: StatementFile) -> None:
    """
    Phase 2/2 (Normalization Agent). Resolves bank properties and writes the
    proposed transaction array to normalized_json for the user to review —
    nothing is written to the ledger here; that only happens via
    POST /statements/{id}/transactions once the user approves the whole
    batch (PLAN.md). `duplicate_of` on each transaction is computed by the AI
    service itself (both mock and real — see services/ai_service.py), not
    re-derived here; it's a preview only, re-checked at approval time rather
    than trusted, since time may have passed since this ran.

    Unlike run_extraction_phase() above, this calls ai_service.normalize_statement()
    against the specific StatementOcrResult that phase just created — the two
    real AI-service endpoints (/internal/ingestion/process,
    .../normalize) are genuinely separate steps threaded by ocr_result_id,
    not two calls sharing one fabricated result.
    """
    statement.is_processing = True
    statement.save(update_fields=["is_processing"])

    ocr_result = statement.latest_ocr_run
    try:
        result = ai_service.normalize_statement(str(ocr_result.id))
    except Exception as exc:
        statement.failure_reason = str(exc)
        statement.failed_phase = StatementFile.PHASE_NORMALIZATION
        statement.is_processing = False
        statement.save(update_fields=["failure_reason", "failed_phase", "is_processing"])
        return

    normalized = result["normalized_json"]

    if statement.account is None:
        # System_Architecture.md §5: "Normalization Agent maps columns...".
        # When the client didn't supply account_id upfront, the Normalization
        # Agent may resolve or create one — keyed on bank_name only, not
        # bank_name + account_hint. account_hint is currently
        # "****" + the *uploaded file's own checksum* (services/ai_service.py,
        # the mock Normalization Agent) — not a real extracted account
        # number — so it's different on every upload and can never match an
        # existing account across two statements from the same real bank
        # account. Keying on it created a brand-new duplicate BankAccount
        # per upload. Once a real OCR/MinerU pipeline extracts a genuine
        # masked account number, matching on it too becomes correct again.
        # Real MinerU may also return null for either field when the PDF
        # doesn't clearly show them — fall back to safe placeholders so the
        # NOT NULL constraints are satisfied and the account is still created.
        bank_name = normalized.get("bank_name") or "Unknown Bank"
        account_hint = normalized.get("account_hint") or "****unknown"
        statement.account, _ = BankAccount.objects.get_or_create(
            user=statement.user,
            bank_name=bank_name,
            defaults={"masked_account_number": account_hint},
        )

    transaction_dates = [
        date.fromisoformat(txn["transaction_date"]) for txn in normalized["transactions"]
    ]

    StatementNormalized.objects.create(
        statement=statement,
        normalized_json=normalized,
        model_used=result["model_used"],
    )

    statement.status = StatementFile.STATUS_NORMALIZED
    statement.failure_reason = None
    statement.failed_phase = None
    statement.is_processing = False
    if transaction_dates:
        statement.start_transaction_date = min(transaction_dates)
        statement.last_transaction_date = max(transaction_dates)
    statement.save(
        update_fields=[
            "account",
            "status",
            "failure_reason",
            "failed_phase",
            "is_processing",
            "start_transaction_date",
            "last_transaction_date",
        ]
    )


# Ordered so a target status's index can be compared against the current
# status's index to tell "forward" from "backward/same" (validate_advance()
# below) and to drive the retry cascade.
_STATUS_ORDER = [
    StatementFile.STATUS_UPLOADED,
    StatementFile.STATUS_EXTRACTED,
    StatementFile.STATUS_NORMALIZED,
    StatementFile.STATUS_APPROVED,
]

# Which phase function runs *from* a given status. STATUS_NORMALIZED and
# STATUS_APPROVED have no entry — the former only ever advances via the
# transaction-approval endpoint, never PATCH; the latter is terminal.
_PHASE_RUNNERS = {
    StatementFile.STATUS_UPLOADED: run_extraction_phase,
    StatementFile.STATUS_EXTRACTED: run_normalization_phase,
}

# Maps a status to the phase that would run *from* it — used only by
# process_statement_pipeline's exception handler to fill in failed_phase
# when an error occurs outside a phase runner's own try/except (e.g. saving
# StatementOcrResult/StatementNormalized itself failing).
_PHASE_FOR_STATUS = {
    StatementFile.STATUS_UPLOADED: StatementFile.PHASE_EXTRACTION,
    StatementFile.STATUS_EXTRACTED: StatementFile.PHASE_NORMALIZATION,
}


def validate_advance(statement: StatementFile, target_status: str) -> int:
    """
    Guard checks only — split out of the old advance_statement_to() so they
    can still run synchronously in the view, before the task is enqueued.
    Raises BusinessRuleError if the statement is already approved
    (`already_approved`), a phase is already running on it
    (`already_processing` — guards against two overlapping callers, e.g. a
    double-clicked retry, re-running the same phase concurrently), or
    `target_status` isn't strictly ahead of the statement's current status
    (`invalid_status_transition`). Returns target_status's rank in
    _STATUS_ORDER for process_statement_pipeline to use.
    """
    if statement.status == StatementFile.STATUS_APPROVED:
        raise BusinessRuleError(
            "This statement has already been approved and cannot be retried.",
            code="already_approved",
        )
    if statement.is_processing:
        raise BusinessRuleError(
            "This statement is currently being processed; try again shortly.",
            code="already_processing",
        )
    target_rank = _STATUS_ORDER.index(target_status)
    if target_rank <= _STATUS_ORDER.index(statement.status):
        raise BusinessRuleError(
            "Target status must be ahead of the statement's current status.",
            code="invalid_status_transition",
        )
    return target_rank


@shared_task
def process_statement_pipeline(statement_id: str, target_status: str) -> None:
    """
    Runs in the Celery worker — re-fetches the row rather than accepting a
    model instance (task args must be JSON-serializable, and a stale
    in-memory instance across the process boundary would be wrong anyway).

    validate_advance() has already run synchronously in the view before this
    was enqueued, and the view already set is_processing=True — this resumes
    one phase at a time, stopping once target_status is reached or a phase
    fails (leaving failure_reason/failed_phase set by that phase's runner —
    a mid-cascade failure is a valid outcome, not something this re-raises).

    The outer try/except hardens a pre-existing gap: run_extraction_phase()/
    run_normalization_phase() only ever catch ai_service's own call
    exceptions — anything else raised in the loop (e.g. a DB error saving
    StatementOcrResult) used to 500 the request and leave is_processing
    stuck True forever, with the request/response cycle there to at least
    surface the 500. That's no longer tolerable once nothing is watching
    this synchronously, so any other exception also clears is_processing and
    records a failure_reason here before re-raising (Celery still logs/
    retries per its own configuration).

    Publishes a statement_status event either way (finally) — the single
    multiplexed SSE connection (core/views/events.py) is how the client
    learns the pipeline finished without polling.
    """
    try:
        statement = StatementFile.objects.select_related("user").get(id=statement_id)
    except StatementFile.DoesNotExist:
        return

    try:
        target_rank = _STATUS_ORDER.index(target_status)
        while _STATUS_ORDER.index(statement.status) < target_rank:
            runner = _PHASE_RUNNERS.get(statement.status)
            if runner is None:
                break
            status_before = statement.status
            runner(statement)
            if statement.status == status_before:
                # The phase attempted and failed — its runner already
                # recorded failure_reason/failed_phase and left status where
                # it was.
                break
    except Exception as exc:
        statement.refresh_from_db()
        statement.is_processing = False
        statement.failure_reason = str(exc)
        statement.failed_phase = statement.failed_phase or _PHASE_FOR_STATUS.get(statement.status)
        statement.save(update_fields=["is_processing", "failure_reason", "failed_phase"])
        raise
    finally:
        event_bus.publish_user_event(
            statement.user_id,
            "statement_status",
            {
                "statement_id": str(statement.id),
                "status": statement.status,
                "is_processing": statement.is_processing,
                "failure_reason": statement.failure_reason,
                "failed_phase": statement.failed_phase,
            },
        )
