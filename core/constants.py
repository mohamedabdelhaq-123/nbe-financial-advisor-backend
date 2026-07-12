"""Vocabularies shared across the app, defined once so they cannot drift apart."""

# The one category vocabulary. These are exactly the buckets every starter
# template allocates across (core/management/commands/seed_onboarding_templates.py),
# and budget progress matches a transaction to its allocation by EXACT string
# equality on this value (core/views/budgets.py). A category outside this set —
# or one that merely differs in case — therefore contributes to no bucket at all:
# the plan reports 0% used while the money has genuinely been spent. Everything
# that writes a transaction category must draw from here, and normalize_category()
# below is what guarantees it.
BUDGET_CATEGORIES = (
    "housing",
    "food",
    "transport",
    "savings",
    "lifestyle",
    "other",
)

# Historic and third-party spellings mapped onto the vocabulary above. Statement
# OCR, seed data and older clients all emitted their own names; rather than let an
# unrecognised one silently fall out of the budget, fold it into the closest bucket.
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
    "income": "other",
    "salary": "other",
}


def normalize_category(category):
    """Fold any incoming category onto the canonical vocabulary.

    Case is discarded first, so "Utilities" and "utilities" can never diverge —
    that mismatch alone was enough to drop a transaction out of its budget bucket.
    An unrecognised category becomes "other" rather than being stored verbatim: a
    bucket that is wrong is visible and fixable, whereas one that matches nothing
    is invisible and silently understates spending. None is preserved as None —
    "uncategorised" is a real state and is not the same thing as "other".
    """
    if category is None:
        return None
    key = str(category).strip().lower()
    if key in BUDGET_CATEGORIES:
        return key
    return _CATEGORY_ALIASES.get(key, "other")
