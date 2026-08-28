"""
MockAIServiceClient — in-process stand-in for the real AI Service, used
whenever settings.USE_MOCK_AI_SERVICE is True (services/ai_service/__init__.py's
get_client()). Exists so the rest of the app (and its tests) can keep working
deterministically without a live ai-service, not just as a bootstrapping
stand-in — flip USE_MOCK_AI_SERVICE off once one is reachable.

Fabrication logic lives in module-level private functions (not methods — they
don't need self), with thin MockAIServiceClient methods calling them,
mirroring services/ai_service/ai_client.py's module-function-plus-thin-methods
shape.
"""

import random
from datetime import date, timedelta
from decimal import Decimal

from django.utils import timezone

from core.models import Category

from .base import AIServiceError, BaseAIServiceClient

# Each Django VectorField's dimension is fixed at the column level (pgvector
# rejects a mismatched write outright), so these mirror the actual model
# fields exactly rather than sharing one constant.
_TRANSACTION_EMBEDDING_DIMENSIONS = 1536  # core/models/aggregations/transaction.py
_MONTHLY_SUMMARY_EMBEDDING_DIMENSIONS = 1536  # core/models/aggregations/monthly_summary.py
_DEFAULT_GENERIC_EMBEDDING_DIMENSIONS = 768  # AI service's configured embedding model


# ============================================================================
# Statement ingestion — fabricates the same shape the real MinerU/Normalization
# passes would return.
# ============================================================================


def _mock_process_statement(statement_id: str) -> dict:
    """No OCR actually runs — fabricates the confirmation shape the real
    MinerU pass would return."""
    return {
        "prefix": f"pfm-statements-ocr/{statement_id}/",
        "ocr_engine": "MinerU",
        "confidence_score": 0.95,
    }


def _mock_normalize_statement(ocr_result_id: str) -> dict:
    """Fabricates 3 transactions deterministically seeded off the statement's
    own id (so repeated calls for the same statement agree), with
    duplicate_of computed the same way the real ai-service's
    find_duplicate() does."""
    from core.models import StatementOcrResult, Transaction

    ocr_result = StatementOcrResult.objects.select_related("statement__user").get(id=ocr_result_id)
    statement = ocr_result.statement

    seed = int(statement.id.hex[:8], 16)
    rng = random.Random(seed)

    merchants = ["Carrefour", "Uber", "Vodafone", "Talabat", "Fawry"]
    # Must be drawn from the real expense category names: budget progress
    # matches a transaction to its allocation by exact category equality, so a
    # category outside that set lands in no bucket at all — the plan then
    # reports 0% used while the money has genuinely been spent.
    categories = list(
        Category.objects.filter(category_type="expense").values_list("name", flat=True)
    )

    starting_balance = round(rng.uniform(5000, 20000), 2)
    balance = starting_balance
    transactions = []
    for i in range(3):
        transaction_date = date.today() - timedelta(days=rng.randrange(1, 60))
        amount = round(rng.uniform(50, 5000), 2)
        merchant = merchants[rng.randrange(len(merchants))]
        merchant_raw = f"{merchant} #{statement.id.hex[:6]}"
        balance = round(balance - amount, 2)

        # Mirrors the real ai-service's find_duplicate(): user-scoped, exact
        # amount, date within a 2-day window, closest by date — not scoped by
        # account/merchant (the account may not be linked yet).
        window_start = transaction_date - timedelta(days=2)
        window_end = transaction_date + timedelta(days=2)
        candidates = Transaction.objects.filter(
            user=statement.user,
            amount=amount,
            transaction_date__gte=window_start,
            transaction_date__lte=window_end,
        )
        duplicate = min(
            candidates,
            key=lambda t: abs((t.transaction_date - transaction_date).days),
            default=None,
        )

        txn_entry = {
            "transaction_date": transaction_date.isoformat(),
            "merchant_raw": merchant_raw,
            "merchant_normalized": merchant,
            "ai_description": f"Payment to {merchant_raw}.",
            "category": categories[rng.randrange(len(categories))],
            "amount": amount,
            "transaction_type": "debit",
            "balance": balance,
            "duplicate_of": str(duplicate.id) if duplicate is not None else None,
        }
        if i == 0:
            # Exercises the extra_fields round-trip (contract: a plain
            # key-value object, present only when non-empty) without every
            # fabricated transaction needing one.
            txn_entry["extra_fields"] = {"reference_number": f"REF{seed}{i}"}
        transactions.append(txn_entry)

    return {
        "normalized_json": {
            "bank_name": "National Bank of Egypt",
            # Unmasked — matches the real contract's account_number (never a
            # masked hint like the old account_hint field).
            "account_number": statement.checksum[:16],
            "transactions": transactions,
            "extra_fields": {"opening_balance": str(starting_balance)},
        },
        "model_used": "mock-normalizer-v0",
    }


def _mock_submit_process_job(statement_id: str) -> dict:
    """No real queue — the envelope is fabricated immediately; the actual
    result is computed lazily on the first status poll (see
    _mock_get_job_status), not here."""
    return {
        "job_id": f"mock:process:{statement_id}",
        "step": "process",
        "state": "queued",
        "submitted_at": timezone.now().isoformat(),
    }


def _mock_submit_normalize_job(ocr_result_id: str) -> dict:
    return {
        "job_id": f"mock:normalize:{ocr_result_id}",
        "step": "normalize",
        "state": "queued",
        "submitted_at": timezone.now().isoformat(),
    }


def _mock_get_job_status(job_id: str) -> dict:
    """No persisted queue — every job is immediately 'succeeded' when polled,
    recomputed fresh from job_id (which encodes step+target) each time by
    reusing _mock_process_statement/_mock_normalize_statement, so fabrication
    logic lives in exactly one place. This is stateless by design: submit and
    poll can land in different Celery worker processes, so nothing about the
    job can depend on in-memory state persisted at submit time."""
    _, step, target_id = job_id.split(":", 2)
    if step == "process":
        result, function = _mock_process_statement(target_id), "ingestion.process"
    else:
        result, function = _mock_normalize_statement(target_id), "ingestion.normalize"
    now = timezone.now().isoformat()
    return {
        "job_id": job_id,
        "function": function,
        "state": "succeeded",
        "submitted_at": now,
        "started_at": now,
        "finished_at": now,
        "result": result,
        "error": None,
    }


# ============================================================================
# Chat
# ============================================================================


def _mock_stream_chat(conversation_id: str, user_id: str, message: str):
    """
    Real implementation: Maestro (LangGraph) classifies intent and routes to a
    sub-agent — analysis, planning, or recommendations (System_Architecture.md
    §7) — which may return a structured widget payload alongside prose. This
    mock uses a simple keyword trigger instead of real intent classification:
    mentioning "budget"/"allocation" surfaces the caller's current plan as an
    allocation_slider widget, with a message reference back to the real
    `budget` row it's grounded in. Anything else gets a canned analysis-style
    reply with no widget.
    """
    from core.models import Budget

    lowered = message.lower()
    budget = None
    if "budget" in lowered or "allocation" in lowered:
        budget = Budget.objects.filter(user_id=user_id).prefetch_related("allocations").first()

    tool_call_events: list[dict] = []
    if budget is not None:
        # Mirrors the real analysis node's best-effort tool_call events, so
        # USE_MOCK_AI_SERVICE=1 dev/demo mode can exercise the "thinking"
        # indicator UI without a live model.
        tool_call_events = [
            {
                "event": "tool_call",
                "data": {"call_id": "mock-call-1", "tool": "get_transactions", "status": "started"},
            },
            {
                "event": "tool_call",
                "data": {
                    "call_id": "mock-call-1",
                    "tool": "get_transactions",
                    "status": "completed",
                },
            },
        ]
        content = "Here's your current plan — adjust the sliders and confirm to update it."
        widget = {
            "type": "allocation_slider",
            "payload": {
                "allocations": [
                    {
                        "category": allocation.category.name,
                        "allocated_percentage": float(allocation.allocated_percentage),
                    }
                    for allocation in budget.allocations.all()
                ]
            },
        }
        references = [{"target_type": "budget", "target_id": str(budget.id)}]
        suggestions = [
            "Why this split?",
            "Suggest a more aggressive savings split",
            "Revert to my last confirmed allocation",
        ]
    else:
        content = (
            "I can help with spending analysis, planning, or product recommendations — "
            "ask me about your budget, transactions, or savings goal."
        )
        widget = {"type": None, "payload": None}
        references = []
        suggestions = [
            "Show my spending breakdown",
            "What are my recent transactions?",
            "Help me plan my savings",
        ]

    yield from tool_call_events

    for word in content.split(" "):
        yield {"event": "token", "data": word + " "}

    yield {
        "event": "done",
        "data": {
            "content": content,
            "widget": widget,
            "references": references,
            "suggestions": suggestions,
        },
    }


# ============================================================================
# Recommendations
# ============================================================================


def _mock_match_recommendations(user_id: str, query: str, top_k: int) -> dict:
    """
    Real implementation: an offline embedding model computes the query's
    embedding, and pgvector's HNSW index over `problem_statements` finds the
    closest matches by cosine similarity. No local embedding model is wired
    up, so this mock ranks products by a simple case-insensitive
    keyword-overlap score against title/description/tags/categories instead
    of a real vector search — same "soft suggestion, never a guarantee"
    spirit, not a real RAG pipeline.
    """
    from core.models import Product

    active_products = list(Product.objects.filter(is_active=True))

    if not query:
        # No ranking signal to fake here — a real implementation would rank
        # by the user's profile/goal signals instead.
        matches = [
            {"product_id": str(product.id), "product_name": product.title, "similarity": 0.5}
            for product in active_products[:top_k]
        ]
        return {"matches": matches}

    query_terms = query.lower().split()
    scored = []
    for product in active_products:
        haystack = " ".join(
            [
                product.title.lower(),
                (product.description or "").lower(),
                " ".join(product.tags or []).lower(),
                " ".join(product.categories or []).lower(),
            ]
        )
        match_count = sum(1 for term in query_terms if term in haystack)
        if match_count:
            similarity = min(0.99, 0.5 + 0.1 * match_count)
            scored.append((similarity, product))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    matches = [
        {"product_id": str(product.id), "product_name": product.title, "similarity": similarity}
        for similarity, product in scored[:top_k]
    ]
    return {"matches": matches}


# ============================================================================
# Transaction embedding
# ============================================================================


def _mock_embed_transactions(transaction_ids: list[str]) -> dict:
    """Mirrors the real endpoint's two notable behaviors rather than being a
    no-op: it writes a (random) vector directly to each Transaction.embedding
    column — the real service does this via its own DB connection, bypassing
    Django entirely — and it's all-or-nothing, matching the real endpoint's
    documented contract ("if any transaction ID doesn't exist... nothing is
    written for the whole request")."""
    from core.models import Transaction

    transactions = list(Transaction.objects.filter(id__in=transaction_ids))
    if len(transactions) != len(set(transaction_ids)):
        raise AIServiceError("embed_transactions: one or more transaction_ids not found")

    for transaction in transactions:
        transaction.embedding = [random.random() for _ in range(_TRANSACTION_EMBEDDING_DIMENSIONS)]
    Transaction.objects.bulk_update(transactions, ["embedding"])

    return {"results": [{"transaction_id": str(t.id), "status": "embedded"} for t in transactions]}


# ============================================================================
# Generic embeddings
# ============================================================================


def _mock_create_embeddings(texts: list[str], dimensions: int | None) -> dict:
    """Random vectors of the requested (or default) dimension, one per input
    text, same index-ordering the real endpoint documents."""
    size = dimensions or _DEFAULT_GENERIC_EMBEDDING_DIMENSIONS
    data = [
        {"object": "embedding", "embedding": [random.random() for _ in range(size)], "index": i}
        for i in range(len(texts))
    ]
    return {
        "object": "list",
        "data": data,
        "model": "mock-embedder-v0",
    }


# ============================================================================
# Analytics
# ============================================================================


def _mock_run_post_ingestion_analysis(user_id: str, account_id: str, month: str) -> dict:
    """`summary` is computed live from real Transaction data (same
    aggregation MonthlySummariesView uses — core/views/aggregations.py), null
    if there's nothing for that month, matching the real endpoint's
    documented "or null if no summary could be computed".
    `recurring_charges`/`anomalies` are simple synthesized examples, not real
    statistical detection — same "plausible, not rigorous" bar as this
    module's other mocks."""
    from django.db.models import Count, Sum

    from core.models import Transaction

    year, month_num = (int(part) for part in month.split("-"))
    month_txns = Transaction.objects.filter(
        user_id=user_id,
        account_id=account_id,
        transaction_date__year=year,
        transaction_date__month=month_num,
    )

    summary = None
    if month_txns.exists():
        total_income = month_txns.filter(transaction_type="credit").aggregate(t=Sum("amount"))[
            "t"
        ] or Decimal("0")
        total_expense = month_txns.filter(transaction_type__in=["debit", "fee"]).aggregate(
            t=Sum("amount")
        )["t"] or Decimal("0")
        by_category = {
            row["category__name"]: float(row["total"])
            for row in month_txns.exclude(category=None)
            .values("category__name")
            .annotate(total=Sum("amount"))
        }
        summary = {
            "user_id": user_id,
            "account_id": account_id,
            "month": month,
            "total_income": float(total_income),
            "total_expense": float(total_expense),
            "net": float(total_income - total_expense),
            "by_category": by_category,
            "embedding": [random.random() for _ in range(_MONTHLY_SUMMARY_EMBEDDING_DIMENSIONS)],
        }

    recurring_charges = []
    top_merchant = (
        month_txns.exclude(merchant_raw=None)
        .values("merchant_raw")
        .annotate(total=Sum("amount"), occurrences=Count("id"))
        .filter(occurrences__gte=2)
        .order_by("-occurrences")
        .first()
    )
    if top_merchant is not None:
        recurring_charges.append(
            {
                "user_id": user_id,
                "account_id": account_id,
                "merchant": top_merchant["merchant_raw"],
                "amount": float(top_merchant["total"] / top_merchant["occurrences"]),
                "cadence_days": 30,
            }
        )

    anomalies = []
    largest_debit = (
        month_txns.filter(transaction_type__in=["debit", "fee"]).order_by("-amount").first()
    )
    if largest_debit is not None:
        anomalies.append(
            {
                "user_id": user_id,
                "account_id": account_id,
                "category": largest_debit.category.name if largest_debit.category else "other",
                "month": month,
                "amount": float(largest_debit.amount),
                "reason": "Amount is outside the IQR-based expected range for this category.",
            }
        )

    return {"summary": summary, "recurring_charges": recurring_charges, "anomalies": anomalies}


# ============================================================================
# Plan questionnaire
# ============================================================================

_MOCK_PLAN_QUESTIONS = [
    {"id": "housing_cost", "text": "What is your average monthly housing cost?"},
    {"id": "transportation_cost", "text": "What is your average monthly transportation cost?"},
    {"id": "savings_goal", "text": "Do you have a specific monthly savings goal?"},
]

# Mirrors the "balanced" onboarding template (seed_onboarding_templates.py) —
# reference-grounded numbers, not invented ones, matching the real service's
# own "grounded in reference budget-limit templates, never invented figures"
# design principle (docs/System_Architecture.md §7).
_MOCK_PLAN_ALLOCATIONS = [
    {"category": "housing", "percentage": "30.0"},
    {"category": "food", "percentage": "15.0"},
    {"category": "transport", "percentage": "10.0"},
    {"category": "savings", "percentage": "20.0"},
    {"category": "lifestyle", "percentage": "15.0"},
    {"category": "other", "percentage": "10.0"},
]


def _mock_next_plan_question(user_context: dict, answers: dict, questions_asked: int) -> dict:
    """A fixed short question sequence, exhausted (question: null) after 3
    questions regardless of user_context/answers content."""
    if questions_asked >= len(_MOCK_PLAN_QUESTIONS):
        return {"question": None}
    return {"question": _MOCK_PLAN_QUESTIONS[questions_asked]}


def _mock_generate_plan(user_context: dict, answers: dict) -> dict:
    """Always returns the same reference-grounded ("balanced" template)
    allocation regardless of user_context/answers content."""
    return {"allocations": list(_MOCK_PLAN_ALLOCATIONS)}


class MockAIServiceClient(BaseAIServiceClient):
    def process_statement(self, statement_id: str) -> dict:
        return _mock_process_statement(statement_id)

    def normalize_statement(self, ocr_result_id: str) -> dict:
        return _mock_normalize_statement(ocr_result_id)

    def submit_process_job(self, statement_id: str) -> dict:
        return _mock_submit_process_job(statement_id)

    def submit_normalize_job(self, ocr_result_id: str) -> dict:
        return _mock_submit_normalize_job(ocr_result_id)

    def get_job_status(self, job_id: str) -> dict:
        return _mock_get_job_status(job_id)

    def stream_chat(self, conversation_id: str, user_id: str, message: str):
        yield from _mock_stream_chat(conversation_id, user_id, message)

    def match_recommendations(self, user_id: str, query: str, top_k: int = 3) -> dict:
        return _mock_match_recommendations(user_id, query, top_k)

    def embed_transactions(self, transaction_ids: list[str]) -> dict:
        return _mock_embed_transactions(transaction_ids)

    def create_embeddings(self, texts: list[str], dimensions: int | None = None) -> dict:
        return _mock_create_embeddings(texts, dimensions)

    def run_post_ingestion_analysis(self, user_id: str, account_id: str, month: str) -> dict:
        return _mock_run_post_ingestion_analysis(user_id, account_id, month)

    def next_plan_question(self, user_context: dict, answers: dict, questions_asked: int) -> dict:
        return _mock_next_plan_question(user_context, answers, questions_asked)

    def generate_plan(self, user_context: dict, answers: dict) -> dict:
        return _mock_generate_plan(user_context, answers)
