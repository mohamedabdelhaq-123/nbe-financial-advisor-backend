import django_filters as filters
from django.db.models import Q

from core.models import AnomalyFlag, RecurringCharge, SpendingPatternInsight, Transaction


class TransactionFilterSet(filters.FilterSet):
    """GET /transactions — PLAN.md Checkpoints B/F. `sort` replaces the
    former hand-rolled ALLOWED_SORT_FIELDS allowlist; default ordering
    (`-transaction_date`) is set on the view's base queryset instead of
    here, since OrderingFilter leaves the queryset's existing order alone
    when no `sort` param is given."""

    # account_id, not Meta.fields shorthand: the model's FK field is named
    # `account` — `account_id` is just Django ORM's shorthand for its raw
    # value, not a real field name Meta.fields' auto-generation can resolve
    # by introspection, so it has to be declared explicitly like this.
    account_id = filters.UUIDFilter(field_name="account_id")
    # Same reasoning as account_id, for the same structural reason:
    # `category` is a ForeignKey(Category) now (fix/category-table), so
    # Meta.fields' auto-generation would build a ModelChoiceFilter expecting
    # a Category **pk**. The API/frontend both send the category **name**
    # (TransactionListSerializer exposes it via
    # SlugRelatedField(slug_field="name")) — declaring it explicitly here
    # matches by name instead.
    category = filters.CharFilter(field_name="category__name", lookup_expr="iexact")
    # Not a real model field — the frontend's UI concept of "income"/"expense"
    # doesn't map 1:1 onto transaction_type (a single-value exact filter):
    # "expense" covers debit, fee, AND transfer rows, not just debit.
    # Sending transaction_type=debit directly would silently hide real fee
    # rows from an "expenses" view.
    type = filters.ChoiceFilter(
        choices=[("income", "income"), ("expense", "expense")], method="filter_type"
    )
    search = filters.CharFilter(method="filter_search")
    min_amount = filters.NumberFilter(field_name="amount", lookup_expr="gte")
    max_amount = filters.NumberFilter(field_name="amount", lookup_expr="lte")
    sort = filters.OrderingFilter(
        fields=(
            ("amount", "amount"),
            ("transaction_date", "transaction_date"),
            ("category", "category"),
            ("merchant_normalized", "merchant_normalized"),
            ("created_at", "created_at"),
        )
    )

    class Meta:
        model = Transaction
        fields = {
            "source": ["exact"],
            "is_recurring": ["exact"],
            "transaction_type": ["exact"],
        }

    def filter_search(self, queryset, name, value):
        return queryset.filter(
            Q(merchant_raw__icontains=value) | Q(merchant_normalized__icontains=value)
        )

    def filter_type(self, queryset, name, value):
        if value == "income":
            return queryset.filter(transaction_type="credit")
        return queryset.filter(transaction_type__in=["debit", "fee", "transfer"])


# `from`/`to` are the documented query-param names (Data_Shapes_Aggregations.md)
# but `from` is a Python reserved word, so these can't be declared as normal
# class-body attributes — assigning into base_filters (django-filter's
# metaclass-built param-name -> Filter dict) is the standard workaround.
TransactionFilterSet.base_filters["from"] = filters.DateFilter(
    field_name="transaction_date", lookup_expr="gte"
)
TransactionFilterSet.base_filters["to"] = filters.DateFilter(
    field_name="transaction_date", lookup_expr="lte"
)


class RecurringChargeFilterSet(filters.FilterSet):
    """GET /analytics/recurring-charges"""

    # See TransactionFilterSet's comment on why this can't use Meta.fields shorthand.
    account_id = filters.UUIDFilter(field_name="account_id")

    class Meta:
        model = RecurringCharge
        fields = []


class AnomalyFilterSet(filters.FilterSet):
    """GET /analytics/anomalies"""

    # See TransactionFilterSet's comment on why this can't use Meta.fields
    # shorthand. Anomalies detected by the post-ingestion analysis pipeline
    # (no single transaction) are still scoped to an account, so this is the
    # only way to filter those down to one account's anomalies.
    account_id = filters.UUIDFilter(field_name="account_id")

    class Meta:
        model = AnomalyFlag
        fields = {"severity": ["exact"], "resolved": ["exact"]}


class SpendingInsightFilterSet(filters.FilterSet):
    """GET /analytics/spending-insights"""

    class Meta:
        model = SpendingPatternInsight
        fields = {"insight_type": ["exact"], "period": ["exact"]}
