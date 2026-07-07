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
checkpoint).
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
            "merchant_raw": f"{merchants[rng.randrange(len(merchants))]} #{statement_file.id.hex[:6]}",
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
