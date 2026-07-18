"""
Client for the internal AI Service (FastAPI, docs/System_Architecture.md §2) —
Django is the only caller of it (§3), and this module is the one place that
boundary is crossed. Every public function here is a thin dispatcher that
branches on settings.USE_MOCK_AI_SERVICE (read at call time, not import time,
so override_settings works in tests — same reasoning as core/ask_view.py's
commit 83bbf63) between an in-process mock and a real HTTP call, and both
sides return the *exact* shape the real /internal/... endpoint uses — callers
never know or care which one ran.

The mock exists so the rest of the app (and its tests) can keep working
deterministically without a live ai-service, not just as a bootstrapping
stand-in — flip USE_MOCK_AI_SERVICE off once one is reachable.

Functions cover the three flows that have a real Django caller today:
process_statement()/normalize_statement() (Statements pipeline),
stream_chat() (Conversations), match_recommendations() (Recommendations).

Five more are implemented (mock+real, tested) but deliberately not wired to
any view/task yet — see each function's docstring:
embed_transactions()/create_embeddings() (called from
AdminProductListCreateView for product embeddings; otherwise ready to call
once a trigger point for transaction/analytics embedding is decided),
run_post_ingestion_analysis() (ready to call, doesn't persist
RecurringCharge/AnomalyFlag/MonthlySummary — see its docstring), and
next_plan_question()/generate_plan() (stubs — no Django-side consumer of the
plan questionnaire exists at all yet, see docs/Pipeline.md §5).
"""

import json
import random
from datetime import date, timedelta
from decimal import Decimal

import requests
from django.conf import settings

from core.models import Category

# Each Django VectorField's dimension is fixed at the column level (pgvector
# rejects a mismatched write outright), so these mirror the actual model
# fields exactly rather than sharing one constant.
_TRANSACTION_EMBEDDING_DIMENSIONS = 1536  # core/models/aggregations/transaction.py
_MONTHLY_SUMMARY_EMBEDDING_DIMENSIONS = 1536  # core/models/aggregations/monthly_summary.py
_DEFAULT_GENERIC_EMBEDDING_DIMENSIONS = 768  # AI service's configured embedding model


class AIServiceError(Exception):
    """Raised for any AI-service request/timeout/HTTP-status failure —
    callers catch this one type instead of requests' own exception hierarchy."""


# Module-level singleton, matching services/event_bus.py's/file_storage.py's
# convention — this is what tests monkeypatch in place of a live network call.
_session = requests.Session()

_REQUEST_TIMEOUT_SECONDS = 30


def _auth_headers():
    """Bearer header built from settings at call time (not import time) —
    see the module docstring for why."""
    return {"Authorization": f"Bearer {settings.AI_SERVICE_TOKEN}"}


def _post(path: str, json_body: dict, *, stream: bool = False):
    """Shared real-HTTP-call helper — builds the URL/auth from settings at
    call time, applies a timeout, and normalizes any failure into
    AIServiceError so task/view code has one thing to catch."""
    resp = None
    try:
        resp = _session.post(
            f"{settings.AI_SERVICE_URL}{path}",
            json=json_body,
            headers=_auth_headers(),
            timeout=_REQUEST_TIMEOUT_SECONDS,
            stream=stream,
        )
        resp.raise_for_status()
    except requests.exceptions.RequestException as exc:
        # resp exists (and may hold an open connection, e.g. stream=True)
        # whenever the failure is a bad status rather than a connection-level
        # error, where _session.post() itself raised before assigning it —
        # close it either way rather than leaking the connection.
        if resp is not None:
            resp.close()
        raise AIServiceError(f"AI service call to {path} failed: {_describe(exc)}") from exc
    return resp


def _parse_json(resp, path: str) -> dict:
    """resp.json() wrapped so a malformed or empty successful response
    becomes an AIServiceError too, not a raw requests/json decode error —
    otherwise callers that only catch AIServiceError (e.g. RecommendationsView)
    would still see an unhandled exception on a broken response body."""
    try:
        return resp.json()
    except ValueError as exc:
        raise AIServiceError(f"AI service response from {path} was not valid JSON: {exc}") from exc


def _describe(exc: requests.exceptions.RequestException) -> str:
    """Surfaces the ai-service's own error detail (FastAPI's {"detail": ...}
    error body) when available, instead of just requests' bare status line —
    otherwise the real reason (e.g. "failed to retrieve source document: ...")
    is silently discarded and failure_reason ends up saying only "502 Bad
    Gateway", which isn't actionable."""
    response = getattr(exc, "response", None)
    if response is None:
        return str(exc)
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    return f"{exc} — {detail}" if detail else str(exc)


# ============================================================================
# Statement ingestion — POST /internal/ingestion/process, .../normalize
# ============================================================================


def process_statement(statement_id: str) -> dict:
    """
    Phase 1/2 of the ingestion pipeline (MinerU/OCR). Returns
    {"prefix", "ocr_engine", "confidence_score"} either way.
    """
    if settings.USE_MOCK_AI_SERVICE:
        return _mock_process_statement(statement_id)
    return _real_process_statement(statement_id)


def normalize_statement(ocr_result_id: str) -> dict:
    """
    Phase 2/2 (Normalization Agent). Returns {"normalized_json", "model_used"}
    either way — normalized_json = {"bank_name", "account_hint", "transactions": [...]},
    each transaction = {"transaction_date", "merchant_raw", "ai_description",
    "category", "amount", "transaction_type", "duplicate_of"}. duplicate_of is
    computed here (both mock and real), not by the caller — the real AI
    service resolves it itself via its read-only Django DB connection, so the
    mock mirrors that instead of leaving it for run_normalization_phase to
    re-derive.
    """
    if settings.USE_MOCK_AI_SERVICE:
        return _mock_normalize_statement(ocr_result_id)
    return _real_normalize_statement(ocr_result_id)


def _mock_process_statement(statement_id: str) -> dict:
    """Mock for POST /internal/ingestion/process — no OCR actually runs, this
    just fabricates the confirmation shape the real MinerU pass would return."""
    return {
        "prefix": f"pfm-statements-ocr/{statement_id}/",
        "ocr_engine": "MinerU",
        "confidence_score": 0.95,
    }


def _mock_normalize_statement(ocr_result_id: str) -> dict:
    """Mock for POST /internal/ingestion/normalize — fabricates 3 transactions
    deterministically seeded off the statement's own id (so repeated calls
    for the same statement agree), with duplicate_of computed the same way
    the real ai-service's find_duplicate() does."""
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

    transactions = []
    for _ in range(3):
        transaction_date = date.today() - timedelta(days=rng.randrange(1, 60))
        amount = round(rng.uniform(50, 5000), 2)
        merchant_raw = f"{merchants[rng.randrange(len(merchants))]} #{statement.id.hex[:6]}"

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

        transactions.append(
            {
                "transaction_date": transaction_date.isoformat(),
                "merchant_raw": merchant_raw,
                "ai_description": f"Payment to {merchant_raw}.",
                "category": categories[rng.randrange(len(categories))],
                "amount": amount,
                "transaction_type": "debit",
                "duplicate_of": str(duplicate.id) if duplicate is not None else None,
            }
        )

    return {
        "normalized_json": {
            "bank_name": "National Bank of Egypt",
            "account_hint": "****" + statement.checksum[:4],
            "transactions": transactions,
        },
        "model_used": "mock-normalizer-v0",
    }


def _real_process_statement(statement_id: str) -> dict:
    """Real POST /internal/ingestion/process call."""
    resp = _post("/internal/ingestion/process", {"statement_id": statement_id})
    return _parse_json(resp, "/internal/ingestion/process")


def _real_normalize_statement(ocr_result_id: str) -> dict:
    """Real POST /internal/ingestion/normalize call."""
    resp = _post("/internal/ingestion/normalize", {"ocr_result_id": ocr_result_id})
    return _parse_json(resp, "/internal/ingestion/normalize")


# ============================================================================
# Chat — POST /internal/chat (SSE stream)
# ============================================================================


def stream_chat(conversation_id: str, user_id: str, message: str):
    """
    Yields the shared {"event", "data"} envelope: zero or more
    {"event": "token", "data": <str>} chunks, then exactly one terminal
    {"event": "done", "data": {"content", "widget", "references"}} or
    {"event": "error", "data": {"message"}}. Both mock and real implementations
    yield this same shape so callers have one consumption path regardless of
    which one ran.
    """
    if settings.USE_MOCK_AI_SERVICE:
        yield from _mock_stream_chat(conversation_id, user_id, message)
    else:
        yield from _real_stream_chat(conversation_id, user_id, message)


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

    if budget is not None:
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
    else:
        content = (
            "I can help with spending analysis, planning, or product recommendations — "
            "ask me about your budget, transactions, or savings goal."
        )
        widget = {"type": None, "payload": None}
        references = []

    for word in content.split(" "):
        yield {"event": "token", "data": word + " "}

    yield {
        "event": "done",
        "data": {"content": content, "widget": widget, "references": references},
    }


def _real_stream_chat(conversation_id: str, user_id: str, message: str):
    """Real POST /internal/chat call — parses the raw SSE wire format (each
    frame is one `data: {json}\\n\\n` line) and yields the same envelope
    shape _mock_stream_chat produces."""
    resp = _post(
        "/internal/chat",
        {"conversation_id": conversation_id, "user_id": user_id, "message": message},
        stream=True,
    )
    terminal_event_seen = False
    try:
        for line in resp.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue
            envelope = json.loads(line[len("data: ") :])
            yield envelope
            if envelope.get("event") in ("done", "error"):
                terminal_event_seen = True
                return
    except (requests.exceptions.RequestException, json.JSONDecodeError) as exc:
        raise AIServiceError(f"AI service chat stream failed: {exc}") from exc
    finally:
        resp.close()
    if not terminal_event_seen:
        # The stream closed cleanly but never sent a done/error frame (e.g.
        # the ai-service crashed mid-stream) — without this, the caller's
        # loop just ends with no result, per generate_chat_reply's own
        # matching fix for the "no terminal event" case.
        raise AIServiceError("AI service chat stream ended without a terminal event")


# ============================================================================
# Recommendations — POST /internal/recommendations/match
# ============================================================================


def match_recommendations(user_id: str, query: str, top_k: int = 3) -> dict:
    """Returns {"matches": [{"product_id", "product_name", "similarity"}]}."""
    if settings.USE_MOCK_AI_SERVICE:
        return _mock_match_recommendations(user_id, query, top_k)
    return _real_match_recommendations(user_id, query, top_k)


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


def _real_match_recommendations(user_id: str, query: str, top_k: int) -> dict:
    """Real POST /internal/recommendations/match call."""
    resp = _post(
        "/internal/recommendations/match",
        {"user_id": user_id, "query": query, "top_k": top_k},
    )
    return _parse_json(resp, "/internal/recommendations/match")


# ============================================================================
# Transaction embedding — POST /internal/transactions/embed
#
# Not wired to any call site yet — ready to call once a trigger point
# (statement approval? manual entry too?) is decided. See the module
# docstring.
# ============================================================================


def embed_transactions(transaction_ids: list[str]) -> dict:
    """Returns {"results": [{"transaction_id", "status"}]}."""
    if settings.USE_MOCK_AI_SERVICE:
        return _mock_embed_transactions(transaction_ids)
    return _real_embed_transactions(transaction_ids)


def _mock_embed_transactions(transaction_ids: list[str]) -> dict:
    """Mock for POST /internal/transactions/embed. Mirrors the real
    endpoint's two notable behaviors rather than being a no-op: it writes a
    (random) vector directly to each Transaction.embedding column — the real
    service does this via its own DB connection, bypassing Django entirely —
    and it's all-or-nothing, matching the real endpoint's documented
    contract ("if any transaction ID doesn't exist... nothing is written for
    the whole request")."""
    from core.models import Transaction

    transactions = list(Transaction.objects.filter(id__in=transaction_ids))
    if len(transactions) != len(set(transaction_ids)):
        raise AIServiceError("embed_transactions: one or more transaction_ids not found")

    for transaction in transactions:
        transaction.embedding = [random.random() for _ in range(_TRANSACTION_EMBEDDING_DIMENSIONS)]
    Transaction.objects.bulk_update(transactions, ["embedding"])

    return {"results": [{"transaction_id": str(t.id), "status": "embedded"} for t in transactions]}


def _real_embed_transactions(transaction_ids: list[str]) -> dict:
    """Real POST /internal/transactions/embed call."""
    resp = _post("/internal/transactions/embed", {"transaction_ids": transaction_ids})
    return _parse_json(resp, "/internal/transactions/embed")


# ============================================================================
# Generic embeddings — POST /internal/embeddings
#
# Wired into AdminProductListCreateView for product/problem-statement
# embeddings (core/views/administration.py).
# ============================================================================


def create_embeddings(texts: list[str], dimensions: int | None = None) -> dict:
    """Returns {"data": [{"embedding", "index"}], "model", "usage"}."""
    if settings.USE_MOCK_AI_SERVICE:
        return _mock_create_embeddings(texts, dimensions)
    return _real_create_embeddings(texts, dimensions)


def _mock_create_embeddings(texts: list[str], dimensions: int | None) -> dict:
    """Mock for POST /internal/embeddings — random vectors of the requested
    (or default) dimension, one per input text, same index-ordering the real
    endpoint documents."""
    size = dimensions or _DEFAULT_GENERIC_EMBEDDING_DIMENSIONS
    data = [
        {"object": "embedding", "embedding": [random.random() for _ in range(size)], "index": i}
        for i in range(len(texts))
    ]
    prompt_tokens = sum(len(text.split()) for text in texts)
    return {
        "object": "list",
        "data": data,
        "model": "mock-embedder-v0",
        "usage": {"prompt_tokens": prompt_tokens, "total_tokens": prompt_tokens},
    }


def _real_create_embeddings(texts: list[str], dimensions: int | None) -> dict:
    """Real POST /internal/embeddings call."""
    body = {"input": texts}
    if dimensions is not None:
        body["dimensions"] = dimensions
    resp = _post("/internal/embeddings", body)
    return _parse_json(resp, "/internal/embeddings")


# ============================================================================
# Analytics — POST /internal/analyze/post-ingestion
#
# Not wired to any call site yet — ready to call once a trigger point is
# decided. Deliberately a pure pass-through: this does NOT persist
# RecurringCharge/AnomalyFlag/MonthlySummary rows from the response — the
# real AnomalyFlagResult has no transaction reference, which doesn't fit
# AnomalyFlag's required transaction FK (core/models/aggregations/anomaly_flag.py),
# an open design question left for whoever wires this up. See the module
# docstring.
# ============================================================================


def run_post_ingestion_analysis(user_id: str, account_id: str, month: str) -> dict:
    """Returns {"summary", "recurring_charges", "anomalies"} (matches the
    real PostIngestionResult exactly)."""
    if settings.USE_MOCK_AI_SERVICE:
        return _mock_run_post_ingestion_analysis(user_id, account_id, month)
    return _real_run_post_ingestion_analysis(user_id, account_id, month)


def _mock_run_post_ingestion_analysis(user_id: str, account_id: str, month: str) -> dict:
    """Mock for POST /internal/analyze/post-ingestion. `summary` is computed
    live from real Transaction data (same aggregation MonthlySummariesView
    uses — core/views/aggregations.py), null if there's nothing for that
    month, matching the real endpoint's documented "or null if no summary
    could be computed". `recurring_charges`/`anomalies` are simple
    synthesized examples, not real statistical detection — same "plausible,
    not rigorous" bar as this module's other mocks."""
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


def _real_run_post_ingestion_analysis(user_id: str, account_id: str, month: str) -> dict:
    """Real POST /internal/analyze/post-ingestion call."""
    resp = _post(
        "/internal/analyze/post-ingestion",
        {"user_id": user_id, "account_id": account_id, "month": month},
    )
    return _parse_json(resp, "/internal/analyze/post-ingestion")


# ============================================================================
# Plan questionnaire — POST /internal/plan/question, .../generate
#
# Stub only: no Django-side consumer of this exists yet, and it's genuinely
# undocumented whether Django is ever meant to call these two directly, or
# whether the AI service handles the whole questionnaire loop internally
# behind one /internal/chat call (docs/Pipeline.md §5's "Planner Agent").
# Kept in the same mock/real dispatch shape as everything else so a future
# caller doesn't need to learn a new pattern once that's resolved.
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


def next_plan_question(user_context: dict, answers: dict, questions_asked: int) -> dict:
    """Returns {"question": {"id", "text"} | None}."""
    if settings.USE_MOCK_AI_SERVICE:
        return _mock_next_plan_question(user_context, answers, questions_asked)
    return _real_next_plan_question(user_context, answers, questions_asked)


def _mock_next_plan_question(user_context: dict, answers: dict, questions_asked: int) -> dict:
    """Mock for POST /internal/plan/question — a fixed short question
    sequence, exhausted (question: null) after 3 questions regardless of
    user_context/answers content."""
    if questions_asked >= len(_MOCK_PLAN_QUESTIONS):
        return {"question": None}
    return {"question": _MOCK_PLAN_QUESTIONS[questions_asked]}


def _real_next_plan_question(user_context: dict, answers: dict, questions_asked: int) -> dict:
    """Real POST /internal/plan/question call."""
    resp = _post(
        "/internal/plan/question",
        {"user_context": user_context, "answers": answers, "questions_asked": questions_asked},
    )
    return _parse_json(resp, "/internal/plan/question")


def generate_plan(user_context: dict, answers: dict) -> dict:
    """Returns {"allocations": [{"category", "percentage"}]}."""
    if settings.USE_MOCK_AI_SERVICE:
        return _mock_generate_plan(user_context, answers)
    return _real_generate_plan(user_context, answers)


def _mock_generate_plan(user_context: dict, answers: dict) -> dict:
    """Mock for POST /internal/plan/generate — always returns the same
    reference-grounded ("balanced" template) allocation regardless of
    user_context/answers content."""
    return {"allocations": list(_MOCK_PLAN_ALLOCATIONS)}


def _real_generate_plan(user_context: dict, answers: dict) -> dict:
    """Real POST /internal/plan/generate call."""
    resp = _post("/internal/plan/generate", {"user_context": user_context, "answers": answers})
    return _parse_json(resp, "/internal/plan/generate")
