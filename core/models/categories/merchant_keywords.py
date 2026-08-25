"""Keyword-based category guess for a bare merchant name.

Synced bank transactions (core/tasks/bank_sync.py) arrive with nothing but a
merchant name — no LLM pass runs over them the way it does for statement
transactions (see the ai-service's app/features/ingestion/categories.py).
This is that path's substitute source of a raw category guess: deterministic
substring matching instead of an LLM call. Both paths converge on the same
resolve_category() (core/models/categories/resolution.py) taxonomy snap, so
this is a second *source* of guesses, not a second resolution mechanism —
the labels below are drawn from resolve_category()'s own alias vocabulary on
purpose, so a hit here resolves exactly the way an equivalent LLM guess would.
"""

# (substring to match in the lowercased merchant name, raw category label fed
# to resolve_category()) — checked in order, first match wins. Deliberately
# coarse: a fixed keyword list, not a real classifier.
#
# Split by direction (expense vs income) rather than one shared list:
# resolve_category() only falls back to the transaction's own direction when
# a guess matches *nothing* in the taxonomy — a guess that DOES match (even
# via alias) is returned as-is regardless of transaction_type. So a credit
# transaction from a merchant whose name happens to contain an expense
# keyword (e.g. a refund or payout from "Uber") must never even reach the
# expense list, or it would resolve to an expense category ("transport")
# despite being incoming money. guess_category_from_merchant() picks the
# matching list itself, the same way resolve_category() picks its fallback.
_EXPENSE_KEYWORD_CATEGORIES: list[tuple[str, str]] = [
    ("carrefour", "groceries"),
    ("spinneys", "groceries"),
    ("kazyon", "groceries"),
    ("grocery", "groceries"),
    ("groceries", "groceries"),
    ("supermarket", "groceries"),
    ("talabat", "dining"),
    ("mcdonald", "dining"),
    ("kfc", "dining"),
    ("starbucks", "dining"),
    ("costa", "dining"),
    ("cafe", "dining"),
    ("coffee", "dining"),
    ("restaurant", "dining"),
    ("uber", "transportation"),
    ("careem", "transportation"),
    ("swvl", "transportation"),
    ("metro", "transportation"),
    ("taxi", "transportation"),
    ("fuel", "transportation"),
    ("petrol", "transportation"),
    ("vodafone", "utilities"),
    ("orange eg", "utilities"),
    ("etisalat", "utilities"),
    ("electricity", "utilities"),
    ("water bill", "utilities"),
    ("internet", "utilities"),
    ("rent", "rent"),
    ("amazon", "shopping"),
    ("noon", "shopping"),
    ("jumia", "shopping"),
    ("mall", "shopping"),
    ("netflix", "entertainment"),
    ("spotify", "entertainment"),
    ("cinema", "entertainment"),
    ("gym", "entertainment"),
]

_INCOME_KEYWORD_CATEGORIES: list[tuple[str, str]] = [
    ("salary", "salary"),
    ("payroll", "salary"),
    ("transfer", "transfer"),
    ("refund", "transfer"),
]


def guess_category_from_merchant(
    merchant_raw: str | None, transaction_type: str | None = None
) -> str | None:
    """Return a raw category guess for `merchant_raw`, or None if there's no name at all.

    `transaction_type` picks which keyword list is even eligible to match —
    "credit" only ever matches the income list, anything else only the
    expense list — mirroring resolve_category()'s own credit/expense fallback
    split, so a hit here can never land on the wrong side of the ledger.

    A recognised keyword returns its mapped label. An unrecognised but
    non-empty name is returned unchanged — resolve_category() won't match it
    against the taxonomy either, so it falls through to that direction's
    fallback category, the same outcome an unrecognised LLM guess gets on the
    statement path. Only a genuinely missing name (None/empty) returns None,
    leaving the transaction truly uncategorised rather than guessing blind.
    """
    if not merchant_raw:
        return None

    lowered = merchant_raw.lower()
    is_income = (transaction_type or "").strip().lower() == "credit"
    keywords = _INCOME_KEYWORD_CATEGORIES if is_income else _EXPENSE_KEYWORD_CATEGORIES
    for keyword, category in keywords:
        if keyword in lowered:
            return category

    return merchant_raw
