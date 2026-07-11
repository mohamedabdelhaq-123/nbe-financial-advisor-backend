import django_filters as filters

from core.models import BudgetHistory


class BudgetHistoryFilterSet(filters.FilterSet):
    """GET /budget/history"""

    class Meta:
        model = BudgetHistory
        fields = []


# `from`/`to` are the documented query-param names (Data_Shapes_Budgets.md)
# but `from` is a Python reserved word — see the identical note in
# core/filters/aggregations.py.
BudgetHistoryFilterSet.base_filters["from"] = filters.DateFilter(
    field_name="changed_at", lookup_expr="date__gte"
)
BudgetHistoryFilterSet.base_filters["to"] = filters.DateFilter(
    field_name="changed_at", lookup_expr="date__lte"
)
