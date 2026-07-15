"""Resolve a raw (user-entered or LLM-produced) category string to a `Category` row.

Replaces the old core/constants.py::normalize_category()/BUDGET_CATEGORIES, which
validated against a hardcoded tuple. The vocabulary now lives in the `categories`
table, seeded by core/migrations (see 0010_category.py).
"""

from core.models.categories.category import Category

# Historic and third-party spellings mapped onto the current vocabulary. Statement
# OCR, seed data and older clients all emitted their own names; rather than let an
# unrecognised one silently fall out of its bucket, fold it into the closest one.
_CATEGORY_ALIASES = {
    "groceries": "food",
    "dining": "food",
    "restaurants": "food",
    "transportation": "transport",
    "utilities": "housing",
    "bills": "housing",
    "rent": "housing",
    "shopping": "lifestyle",
    "entertainment": "lifestyle",
    "health": "other",
    "income": "other_income",
    "transfer": "transfers_in",
}


def resolve_category(raw: str | None, transaction_type: str | None = None) -> Category | None:
    """Resolve `raw` to a `Category` row, or its type's fallback if it doesn't match.

    Case is discarded first, so "Utilities" and "utilities" can never diverge.
    `transaction_type` picks which type's fallback applies when nothing matches:
    "credit" resolves to the income fallback, anything else to the expense one —
    this is what keeps an unresolved category on a credit transaction from
    silently landing under an expense-labeled bucket. `raw=None` returns `None`
    unchanged: "uncategorised" is a real state, not the same thing as a fallback.
    """
    if raw is None:
        return None

    key = str(raw).strip().lower()
    is_income = (transaction_type or "").strip().lower() == "credit"

    match = Category.objects.filter(name=key).first()
    if match is not None:
        return match

    alias = _CATEGORY_ALIASES.get(key)
    if alias is not None:
        match = Category.objects.filter(name=alias).first()
        if match is not None:
            return match

    return Category.objects.filter(
        is_fallback=True, category_type="income" if is_income else "expense"
    ).first()
