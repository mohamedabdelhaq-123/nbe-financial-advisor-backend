import django_filters as filters

from core.models import StatementFile


class StatementFileFilterSet(filters.FilterSet):
    """GET /statements"""

    # See core/filters/aggregations.py's TransactionFilterSet comment on why
    # this can't use Meta.fields shorthand — StatementFile's FK field is
    # named `account`, not `account_id`.
    account_id = filters.UUIDFilter(field_name="account_id")

    class Meta:
        model = StatementFile
        fields = {"status": ["exact"]}
