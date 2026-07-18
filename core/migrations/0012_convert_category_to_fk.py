"""Convert Transaction.category / BudgetAllocation.category from a free-text
string to a real ForeignKey(Category).

Django can't express "cast this varchar column to a FK" as a single AlterField
on a populated table (there's no automatic string->uuid cast), so this does it
in safe steps: rename the old string column out of the way, add the new FK
column alongside it, backfill by name (same alias/fallback resolution as
core/models/categories/resolution.py), then drop the old string column.
"""

import django.db.models.deletion
from django.db import migrations, models

# Same alias table as core/models/categories/resolution.py, duplicated here
# (migrations must not import application code that can change shape later).
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


def _resolve(by_name, raw, fallback):
    if raw is None:
        return fallback
    key = str(raw).strip().lower()
    match = by_name.get(key)
    if match is not None:
        return match
    alias = _CATEGORY_ALIASES.get(key)
    if alias is not None:
        match = by_name.get(alias)
        if match is not None:
            return match
    return fallback


def backfill_categories(apps, schema_editor):
    Category = apps.get_model("core", "Category")
    Transaction = apps.get_model("core", "Transaction")
    BudgetAllocation = apps.get_model("core", "BudgetAllocation")

    by_name = {c.name: c for c in Category.objects.all()}
    expense_fallback = Category.objects.filter(category_type="expense", is_fallback=True).first()
    income_fallback = Category.objects.filter(category_type="income", is_fallback=True).first()

    for txn in Transaction.objects.all().iterator():
        if txn.category_str is None:
            continue
        is_income = (txn.transaction_type or "").strip().lower() == "credit"
        fallback = income_fallback if is_income else expense_fallback
        resolved = _resolve(by_name, txn.category_str, fallback)
        Transaction.objects.filter(pk=txn.pk).update(category=resolved)

    for alloc in BudgetAllocation.objects.all().iterator():
        resolved = _resolve(by_name, alloc.category_str, expense_fallback)
        BudgetAllocation.objects.filter(pk=alloc.pk).update(category=resolved)


def restore_category_strings(apps, schema_editor):
    Transaction = apps.get_model("core", "Transaction")
    BudgetAllocation = apps.get_model("core", "BudgetAllocation")

    for txn in Transaction.objects.select_related("category").all().iterator():
        Transaction.objects.filter(pk=txn.pk).update(
            category_str=txn.category.name if txn.category else None
        )
    for alloc in BudgetAllocation.objects.select_related("category").all().iterator():
        BudgetAllocation.objects.filter(pk=alloc.pk).update(
            category_str=alloc.category.name if alloc.category else None
        )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0011_category"),
    ]

    operations = [
        migrations.RenameField(
            model_name="transaction", old_name="category", new_name="category_str"
        ),
        migrations.RenameField(
            model_name="budgetallocation", old_name="category", new_name="category_str"
        ),
        migrations.AddField(
            model_name="transaction",
            name="category",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="transactions",
                to="core.category",
            ),
        ),
        migrations.AddField(
            model_name="budgetallocation",
            name="category",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="allocations",
                to="core.category",
            ),
        ),
        migrations.RunPython(backfill_categories, restore_category_strings),
        migrations.RemoveField(model_name="transaction", name="category_str"),
        migrations.RemoveField(model_name="budgetallocation", name="category_str"),
        migrations.AlterField(
            model_name="budgetallocation",
            name="category",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="allocations",
                to="core.category",
            ),
        ),
    ]
