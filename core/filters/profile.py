import django_filters as filters

from core.models import BankAccount


class BankAccountFilterSet(filters.FilterSet):
    """GET /accounts — PLAN.md Checkpoint A's masked_account_number/bank_name
    filters, swept into a FilterSet in Checkpoint F."""

    class Meta:
        model = BankAccount
        fields = {"masked_account_number": ["exact"], "bank_name": ["exact"]}
