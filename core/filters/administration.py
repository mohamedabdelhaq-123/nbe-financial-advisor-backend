import django_filters as filters

from core.models import Product, Reaction, ReportedIssue


class AdminReactionFilterSet(filters.FilterSet):
    """GET /admin/feedback"""

    class Meta:
        model = Reaction
        fields = {"target_type": ["exact"], "rating": ["exact"]}


# `from`/`to` are the documented query-param names (Data_Shapes_Administration.md)
# but `from` is a Python reserved word — see the identical note in
# core/filters/aggregations.py.
AdminReactionFilterSet.base_filters["from"] = filters.DateFilter(
    field_name="created_at", lookup_expr="date__gte"
)
AdminReactionFilterSet.base_filters["to"] = filters.DateFilter(
    field_name="created_at", lookup_expr="date__lte"
)


class AdminIssueFilterSet(filters.FilterSet):
    """GET /admin/issues"""

    class Meta:
        model = ReportedIssue
        fields = {"status": ["exact"]}


class AdminProductFilterSet(filters.FilterSet):
    """GET /admin/products"""

    is_active = filters.BooleanFilter(field_name="is_active")
    category = filters.CharFilter(method="filter_category")

    class Meta:
        model = Product
        fields = []

    def filter_category(self, queryset, name, value):
        return queryset.filter(categories__contains=[value])
