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
"""

import json
import random
from datetime import date, timedelta

import requests
from django.conf import settings

from core.constants import BUDGET_CATEGORIES


class AIServiceError(Exception):
    """Raised for any AI-service request/timeout/HTTP-status failure —
    callers catch this one type instead of requests' own exception hierarchy."""


# Module-level singleton, matching services/event_bus.py's/file_storage.py's
# convention — this is what tests monkeypatch in place of a live network call.
_session = requests.Session()

_REQUEST_TIMEOUT_SECONDS = 30


def _auth_headers():
    return {"Authorization": f"Bearer {settings.AI_SERVICE_TOKEN}"}


def _post(path: str, json_body: dict, *, stream: bool = False):
    """Shared real-HTTP-call helper — builds the URL/auth from settings at
    call time, applies a timeout, and normalizes any failure into
    AIServiceError so task/view code has one thing to catch."""
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
        raise AIServiceError(f"AI service call to {path} failed: {_describe(exc)}") from exc
    return resp


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
    return {
        "prefix": f"pfm-statements-ocr/{statement_id}/",
        "ocr_engine": "MinerU",
        "confidence_score": 0.95,
    }


def _mock_normalize_statement(ocr_result_id: str) -> dict:
    from core.models import StatementOcrResult, Transaction

    ocr_result = StatementOcrResult.objects.select_related("statement__user").get(id=ocr_result_id)
    statement = ocr_result.statement

    seed = int(statement.id.hex[:8], 16)
    rng = random.Random(seed)

    merchants = ["Carrefour", "Uber", "Vodafone", "Talabat", "Fawry"]
    # Must be drawn from BUDGET_CATEGORIES: budget progress matches a transaction
    # to its allocation by exact category equality, so a category outside that set
    # lands in no bucket at all — the plan then reports 0% used while the money
    # has genuinely been spent.
    categories = list(BUDGET_CATEGORIES)

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
    resp = _post("/internal/ingestion/process", {"statement_id": statement_id})
    return resp.json()


def _real_normalize_statement(ocr_result_id: str) -> dict:
    resp = _post("/internal/ingestion/normalize", {"ocr_result_id": ocr_result_id})
    return resp.json()


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
                        "category": allocation.category,
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
    resp = _post(
        "/internal/chat",
        {"conversation_id": conversation_id, "user_id": user_id, "message": message},
        stream=True,
    )
    try:
        for line in resp.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue
            envelope = json.loads(line[len("data: ") :])
            yield envelope
            if envelope.get("event") in ("done", "error"):
                return
    except (requests.exceptions.RequestException, json.JSONDecodeError) as exc:
        raise AIServiceError(f"AI service chat stream failed: {exc}") from exc
    finally:
        resp.close()


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
    resp = _post(
        "/internal/recommendations/match",
        {"user_id": user_id, "query": query, "top_k": top_k},
    )
    return resp.json()
