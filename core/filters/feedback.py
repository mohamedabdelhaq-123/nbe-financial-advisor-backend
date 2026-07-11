import django_filters as filters

from core.models import ReportedIssue


class IssueFilterSet(filters.FilterSet):
    """GET /issues"""

    class Meta:
        model = ReportedIssue
        fields = {"status": ["exact"]}
