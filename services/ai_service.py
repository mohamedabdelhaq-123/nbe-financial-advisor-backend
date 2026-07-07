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
PLAN.md) — this file currently only has normalize() (Statements checkpoint).
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
