import django_filters as filters

from core.models import Category


class CategoryFilterSet(filters.FilterSet):
    """GET /categories/ and GET /admin/categories/ — narrow to one taxonomy side."""

    class Meta:
        model = Category
        fields = {"category_type": ["exact"]}
