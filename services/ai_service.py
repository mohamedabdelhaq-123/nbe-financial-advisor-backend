"""
Mock stand-in for the internal AI Service (FastAPI, docs/System_Architecture.md
§2) — Django is the only caller of it (§3), and this module is the one place
that boundary is crossed. Every function here returns data in the exact JSON
shape the real /internal/... endpoint would (docs/API_GUIDE/API_Endpoints_1.md
§13) — plain JSON-safe primitives (strings/floats/ints), not Decimal/date
objects — matching what an actual HTTP call would hand back, so the calling
view's job of converting those into proper model field types doesn't change
when the mock body is swapped for a real request.

Functions are added incrementally, one per checkpoint that needs them (see
PLAN.md) — normalize() (Statements checkpoint), chat() (Conversations
checkpoint), match_recommendations() (Recommendation checkpoint).
"""

import random
from datetime import date, timedelta


def normalize(statement_file) -> dict:
    """
    Mock for POST /internal/normalize. Real implementation: Django hands the
    statement's raw file (via its SeaweedFS key) to the AI service, which
    runs MinerU OCR then an LLM normalization pass and returns structured
    transactions (System_Architecture.md §5). This mock fabricates a small
    set of transactions instead — seeded off the statement's own id, so
    repeated calls for the *same* statement are reproducible, while different
    statements produce different-enough data that the ledger's duplicate-
    check guardrail (System_Architecture.md §8) has something real to
    exercise instead of always colliding on identical fake rows.
    """
    seed = int(statement_file.id.hex[:8], 16)
    rng = random.Random(seed)

    merchants = ["Carrefour", "Uber", "Vodafone", "Talabat", "Fawry"]
    categories = ["groceries", "transport", "utilities", "food", "bills"]

    transactions = [
        {
            "transaction_date": (date.today() - timedelta(days=rng.randrange(1, 60))).isoformat(),
            "merchant_raw": (
                f"{merchants[rng.randrange(len(merchants))]} " f"#{statement_file.id.hex[:6]}"
            ),
            "category": categories[rng.randrange(len(categories))],
            "amount": round(rng.uniform(50, 5000), 2),
            "transaction_type": "debit",
        }
        for _ in range(3)
    ]

    return {
        "ocr": {
            "engine": "MinerU",
            "confidence_score": 0.95,
        },
        # model_used lives as a sibling of `normalized`, not inside it — it
        # maps to StatementNormalized.model_used, a separate column from
        # normalized_json (DB_Schema.md), and Data_Shapes_Statements.md's
        # GET .../normalized response shape puts it at the top level too.
        "model_used": "mock-normalizer-v0",
        "normalized": {
            "bank_name": "National Bank of Egypt",
            "account_hint": "****" + statement_file.checksum[:4],
            "transactions": transactions,
        },
    }


def chat(content: str, budget=None) -> dict:
    """
    Mock for POST /internal/chat, proxied by Django as an SSE stream
    (API Design Guidelines §9). Real implementation: Maestro (LangGraph)
    classifies intent and routes to a sub-agent — analysis, planning, or
    recommendations (System_Architecture.md §7) — which may return a
    structured widget payload alongside prose. This mock uses a simple
    keyword trigger instead of real intent classification: mentioning
    "budget"/"allocation" surfaces the caller's current plan as an
    allocation_slider widget (Architectural_Guidelines.md §7 — the same
    widget component the dashboard's "Customize" action would use), with a
    message reference back to the real `budget` row it's grounded in
    (System_Architecture.md §8's numeric-traceability guardrail). Anything
    else gets a canned analysis-style reply with no widget.

    `budget` is passed in as an already-fetched model instance (not looked
    up here) — this module never queries the DB itself, matching the AI
    service's real stateless-request-handling boundary (System_Architecture.md
    §2); the Django view on the other side of this mock is the one Django
    business logic is allowed to touch the database from.
    """
    lowered = content.lower()
    if ("budget" in lowered or "allocation" in lowered) and budget is not None:
        return {
            "content": "Here's your current plan — adjust the sliders and confirm to update it.",
            "widget": {
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
            },
            "references": [{"target_type": "budget", "target_id": str(budget.id)}],
        }

    return {
        "content": (
            "I can help with spending analysis, planning, or product recommendations — "
            "ask me about your budget, transactions, or savings goal."
        ),
        "widget": {"type": None, "payload": None},
        "references": [],
    }


def match_recommendations(query: str, active_products, top_k: int = 5) -> list[dict]:
    """
    Mock for POST /internal/recommendations/match. Real implementation: an
    offline embedding model computes the query's embedding, and pgvector's
    HNSW index over `problem_statements` finds the closest matches by cosine
    similarity (Data_Governance_Specs.md §6, DB_Schema.md's problem_statements
    table — itself AI-service/Alembic-owned, not written to from here). No
    local embedding model is wired up, so this mock ranks products by a
    simple case-insensitive keyword-overlap score against
    title/description/tags/categories instead of a real vector search — same
    "soft suggestion, never a guarantee" spirit (§6's rule), not a real RAG
    pipeline. `active_products` is passed in pre-filtered (is_active=True) —
    this module doesn't query the DB itself, matching the real AI service's
    stateless-request-handling boundary (System_Architecture.md §2).

    Returns a list of {"product": <the Product instance>, "similarity_score": float},
    already sorted best-match-first and capped at `top_k`.
    """
    if not query:
        # Profile/goal-driven fallback with no query text: there's no real
        # ranking signal to fake here, so this just returns the first
        # `top_k` active products with a flat, clearly-synthetic
        # similarity_score — a real implementation would rank by the user's
        # profile/goal signals instead (Data_Governance_Specs.md §6: "Reads
        # contextual signals from Profile and Budgets when matching is
        # profile-driven rather than query-driven").
        return [
            {"product": product, "similarity_score": 0.5} for product in active_products[:top_k]
        ]

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
            similarity_score = min(0.99, 0.5 + 0.1 * match_count)
            scored.append((similarity_score, product))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [{"product": product, "similarity_score": score} for score, product in scored[:top_k]]
