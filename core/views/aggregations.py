from datetime import date
from decimal import Decimal

from django.db.models import Sum
from django.db.models.functions import TruncMonth
from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, status
from rest_framework.exceptions import ValidationError
from rest_framework.generics import GenericAPIView, ListAPIView
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from core.exceptions import BusinessRuleError
from core.filters.aggregations import (
    AnomalyFilterSet,
    RecurringChargeFilterSet,
    SpendingInsightFilterSet,
    TransactionFilterSet,
)
from core.models import (
    AnomalyFlag,
    BankAccount,
    RecurringCharge,
    SpendingPatternInsight,
    Transaction,
)
from core.openapi import error_responses
from core.serializers.aggregations import (
    AnomalyFlagSerializer,
    AnomalyResolveSerializer,
    CategoryBreakdownResponseSerializer,
    MonthlySummaryItemSerializer,
    NetWorthResponseSerializer,
    RecurringChargeSerializer,
    SpendingPatternInsightSerializer,
    StabilityScoreResponseSerializer,
    TransactionCreateRequestSerializer,
    TransactionDetailSerializer,
    TransactionListSerializer,
    TransactionPatchSerializer,
    TransactionWriteSerializer,
)

# ============================================================================
# Transactions — the real, fully CRUD-able ledger.
# ============================================================================


class TransactionListCreateView(ListAPIView):
    """
    List the current user's transactions, or record one manually.

    Filtering/sorting/pagination are all handled by query parameters —
    see the parameter list below for the exact set (date range, amount
    range, category, account, free-text merchant search, and `sort`).
    django-filter is the single source of truth for both the filtering
    behavior and this parameter list, so they can't drift apart.

    POST resolves and ownership-checks `account_id` before validating the
    rest of the body — an unowned or nonexistent `account_id` returns 404,
    while every other validation problem returns 422. `source` is always
    set to `"manual"` server-side and can't be overridden by the client.
    A transaction matching an existing one's date, amount, and merchant is
    rejected as a likely duplicate (422, `error.code: "duplicate_transaction"`,
    with the existing row's id in `error.fields.transaction_id`).
    """

    serializer_class = TransactionListSerializer
    pagination_class = LimitOffsetPagination
    filter_backends = [DjangoFilterBackend]
    filterset_class = TransactionFilterSet

    def get_queryset(self):
        # swagger_fake_view: drf-spectacular's DjangoFilterBackend
        # introspection calls get_queryset() against an AnonymousUser to
        # resolve the FilterSet's model — filtering by self.request.user
        # unconditionally raises ("AnonymousUser is not a valid UUID") and
        # silently drops every FilterSet param from the generated schema
        # (confirmed by hand — PLAN.md Checkpoint F). This guard is required
        # for the schema to actually include search/min_amount/etc., not
        # just cosmetic.
        if getattr(self, "swagger_fake_view", False):
            return Transaction.objects.none()
        # Default order when no `sort` param is given — OrderingFilter
        # leaves the queryset's existing order alone otherwise.
        return Transaction.objects.filter(user=self.request.user).order_by("-transaction_date")

    @extend_schema(
        request=TransactionCreateRequestSerializer,
        responses={201: TransactionDetailSerializer, **error_responses(404, 422)},
    )
    def post(self, request, *args, **kwargs):
        account_id = request.data.get("account_id")
        if not account_id:
            raise ValidationError({"account_id": "This field is required."})
        # Resolved/ownership-checked before the serializer runs, so an unowned
        # or nonexistent account_id 404s (API Design Guidelines §10) rather
        # than surfacing as an ordinary field-validation 422.
        account = get_object_or_404(BankAccount, id=account_id, user=request.user)

        serializer = TransactionWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        duplicate = Transaction.objects.filter(
            user=request.user,
            account=account,
            transaction_date=data["transaction_date"],
            amount=data["amount"],
            merchant_raw=data["merchant_raw"],
        ).first()
        if duplicate is not None:
            # Duplicate-prevention guardrail (System_Architecture.md §8) applies
            # to manual entry exactly as it does to statement bulk-insert.
            raise BusinessRuleError(
                "A transaction matching this date, amount, and merchant already exists.",
                code="duplicate_transaction",
                fields={"transaction_id": str(duplicate.id)},
            )

        transaction = Transaction.objects.create(
            user=request.user,
            account=account,
            source="manual",  # never client-supplied — API Design Guidelines' write contract
            currency=data.get("currency") or account.currency,
            transaction_date=data["transaction_date"],
            merchant_raw=data["merchant_raw"],
            category=data.get("category"),
            amount=data["amount"],
            transaction_type=data["transaction_type"],
        )
        return Response(
            TransactionDetailSerializer(transaction).data, status=status.HTTP_201_CREATED
        )


class TransactionDetailView(mixins.RetrieveModelMixin, mixins.DestroyModelMixin, GenericAPIView):
    """
    Retrieve, edit, or delete a single transaction.

    PATCH only accepts a restricted field subset (`category`, `merchant_raw`,
    `amount`, `transaction_date`, `transaction_type`) — `account_id` and
    `source` are deliberately not patchable, since changing either would
    misrepresent where the transaction actually came from. Built from
    Retrieve+Destroy mixins directly (not RetrieveUpdateDestroyAPIView)
    because PATCH's input shape genuinely differs from GET's response
    shape, rather than being a partial version of it.
    """

    serializer_class = TransactionDetailSerializer
    lookup_url_kwarg = "transaction_id"

    def get_queryset(self):
        return Transaction.objects.filter(user=self.request.user)

    @extend_schema(responses={200: TransactionDetailSerializer, **error_responses(404)})
    def get(self, request, *args, **kwargs):
        return self.retrieve(request, *args, **kwargs)

    @extend_schema(
        request=TransactionPatchSerializer,
        responses={200: TransactionDetailSerializer, **error_responses(404, 422)},
    )
    def patch(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = TransactionPatchSerializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(TransactionDetailSerializer(instance).data)

    @extend_schema(responses={204: None, **error_responses(404)})
    def delete(self, request, *args, **kwargs):
        # Editing/deleting a transaction would ideally trigger the same
        # re-aggregation background tasks as any other ledger change, but
        # there's no Celery worker wired up yet — the analytics endpoints
        # already compute live from the ledger on every read instead, so
        # there's nothing stale left to re-trigger in the meantime.
        return self.destroy(request, *args, **kwargs)


# ============================================================================
# Analytics — read-only, backend-computed.
#
# monthly-summaries / category-breakdown / net-worth / stability-score are
# computed live from the real Transaction ledger below (plain ORM
# aggregation — honest, correct arithmetic, nothing to mock).
#
# recurring-charges / anomalies / spending-insights are genuinely AI/
# statistical pattern-detection tasks (System_Architecture.md §5 explicitly
# routes these through the AI service, not Django). Building a real
# detection algorithm is out of scope for a routes/serializers/mocked-
# services checkpoint, so these three endpoints honestly serve only
# whatever rows already exist in their tables rather than faking detection
# results — see each view's docstring.
# ============================================================================


class MonthlySummariesView(APIView):
    """
    GET /analytics/monthly-summaries

    account_id/from/to genuinely filter the underlying Transaction queryset
    before the custom month-bucketing/aggregation below runs — but the
    response itself is a custom-shaped list, not a serialized queryset, so
    this can't become a ListAPIView + FilterSet the way the Category 1/2
    views in this file did (PLAN.md Checkpoint F): drf-spectacular's
    automatic filter-parameter introspection only fires for GenericAPIView.
    Documented manually here instead.
    """

    @extend_schema(
        parameters=[
            OpenApiParameter("account_id", OpenApiTypes.UUID, required=False),
            OpenApiParameter(
                "from", OpenApiTypes.STR, required=False, description="YYYY-MM, inclusive"
            ),
            OpenApiParameter(
                "to", OpenApiTypes.STR, required=False, description="YYYY-MM, inclusive"
            ),
        ],
        responses={200: MonthlySummaryItemSerializer(many=True)},
    )
    def get(self, request):
        qs = Transaction.objects.filter(user=request.user)
        account_id = request.query_params.get("account_id")
        if account_id:
            qs = qs.filter(account_id=account_id)
        if request.query_params.get("from"):
            qs = qs.filter(transaction_date__gte=f"{request.query_params['from']}-01")
        if request.query_params.get("to"):
            qs = qs.filter(transaction_date__lte=f"{request.query_params['to']}-31")

        months = (
            qs.annotate(month=TruncMonth("transaction_date"))
            .values_list("month", flat=True)
            .distinct()
            .order_by("-month")
        )

        results = []
        for month in months:
            month_qs = qs.filter(
                transaction_date__year=month.year, transaction_date__month=month.month
            )
            total_spend = month_qs.filter(transaction_type__in=["debit", "fee"]).aggregate(
                t=Sum("amount")
            )["t"] or Decimal("0")
            total_inflow = month_qs.filter(transaction_type="credit").aggregate(t=Sum("amount"))[
                "t"
            ] or Decimal("0")
            category_breakdown = {
                row["category"]: row["total"]
                for row in month_qs.exclude(category=None)
                .values("category")
                .annotate(total=Sum("amount"))
            }
            top_merchants = [
                {
                    "merchant": row["merchant_normalized"] or row["merchant_raw"],
                    "total": row["total"],
                }
                for row in month_qs.exclude(merchant_raw=None)
                .values("merchant_normalized", "merchant_raw")
                .annotate(total=Sum("amount"))
                .order_by("-total")[:5]
            ]
            results.append(
                {
                    # TruncMonth on a DateField (transaction_date is a DateField,
                    # not DateTimeField) returns a plain date already — no
                    # further .date() call needed (that was the bug: calling
                    # .date() on an object that's already a date raises
                    # AttributeError, since date has no .date() method).
                    "month": month.isoformat(),
                    "account_id": account_id or None,
                    "total_spend": total_spend,
                    "total_inflow": total_inflow,
                    "category_breakdown": category_breakdown,
                    "top_merchants": top_merchants,
                }
            )

        return Response(results)


class CategoryBreakdownView(APIView):
    """GET /analytics/category-breakdown

    `period`/`account_id` genuinely filter the underlying Transaction
    queryset, but the response is a custom aggregated shape, not a
    serialized queryset — same reasoning as MonthlySummariesView above for
    why this stays a manually-documented APIView (PLAN.md Checkpoint F).
    """

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "period",
                OpenApiTypes.STR,
                required=True,
                description="YYYY-MM, e.g. 2026-07",
            ),
            OpenApiParameter("account_id", OpenApiTypes.UUID, required=False),
        ],
        responses={200: CategoryBreakdownResponseSerializer},
    )
    def get(self, request):
        period = request.query_params.get("period")
        if not period:
            raise ValidationError({"period": "This field is required, e.g. 2026-07."})
        try:
            year, month = (int(p) for p in period.split("-"))
        except ValueError as exc:
            raise ValidationError({"period": "Expected format YYYY-MM."}) from exc

        qs = Transaction.objects.filter(
            user=request.user, transaction_date__year=year, transaction_date__month=month
        )
        account_id = request.query_params.get("account_id")
        if account_id:
            qs = qs.filter(account_id=account_id)

        rows = list(
            qs.exclude(category=None)
            .values("category")
            .annotate(total=Sum("amount"))
            .order_by("-total")
        )
        grand_total = sum((row["total"] for row in rows), Decimal("0"))

        breakdown = [
            {
                "category": row["category"],
                "amount": row["total"],
                "percentage_of_total": (
                    round(float(row["total"] / grand_total * 100), 2) if grand_total else 0
                ),
            }
            for row in rows
        ]
        return Response({"period": period, "breakdown": breakdown})


class RecurringChargesView(ListAPIView):
    """
    GET /analytics/recurring-charges

    Reads whatever rows already exist in `recurring_charges`. Populating
    this table is a background, AI-service-driven detection job
    (System_Architecture.md §5) that doesn't exist yet — see this file's
    module docstring for why real detection logic isn't built here.

    Converted from a plain APIView to ListAPIView (PLAN.md Checkpoint F) to
    get automatic FilterSet-based Swagger docs — pagination_class stays
    None to preserve the pre-existing plain-array response (this domain's
    small, bounded per-user lists are intentionally unpaginated, matching
    BankAccountListCreateView's precedent).
    """

    serializer_class = RecurringChargeSerializer
    pagination_class = None
    filter_backends = [DjangoFilterBackend]
    filterset_class = RecurringChargeFilterSet

    def get_queryset(self):
        # swagger_fake_view: see TransactionListCreateView's get_queryset().
        if getattr(self, "swagger_fake_view", False):
            return RecurringCharge.objects.none()
        return RecurringCharge.objects.filter(user=self.request.user)


class AnomaliesView(ListAPIView):
    """GET /analytics/anomalies — same scope boundary as RecurringChargesView,
    same ListAPIView conversion rationale."""

    serializer_class = AnomalyFlagSerializer
    pagination_class = None
    filter_backends = [DjangoFilterBackend]
    filterset_class = AnomalyFilterSet

    def get_queryset(self):
        # swagger_fake_view: see TransactionListCreateView's get_queryset().
        if getattr(self, "swagger_fake_view", False):
            return AnomalyFlag.objects.none()
        return AnomalyFlag.objects.filter(transaction__user=self.request.user)


class AnomalyResolveView(APIView):
    """PATCH /analytics/anomalies/{anomaly_id}"""

    @extend_schema(request=AnomalyResolveSerializer, responses={200: AnomalyFlagSerializer})
    def patch(self, request, anomaly_id):
        # Scoped via the underlying transaction's ownership, not a direct
        # user FK on AnomalyFlag itself — matches Data_Shapes_Aggregations.md
        # ("Scoping: implicit self via the underlying transaction's ownership").
        anomaly = get_object_or_404(AnomalyFlag, id=anomaly_id, transaction__user=request.user)
        serializer = AnomalyResolveSerializer(anomaly, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(AnomalyFlagSerializer(anomaly).data)


class SpendingInsightsView(ListAPIView):
    """GET /analytics/spending-insights — same scope boundary and ListAPIView
    conversion rationale as RecurringChargesView."""

    serializer_class = SpendingPatternInsightSerializer
    pagination_class = None
    filter_backends = [DjangoFilterBackend]
    filterset_class = SpendingInsightFilterSet

    def get_queryset(self):
        # swagger_fake_view: see TransactionListCreateView's get_queryset().
        if getattr(self, "swagger_fake_view", False):
            return SpendingPatternInsight.objects.none()
        return SpendingPatternInsight.objects.filter(user=self.request.user)


class NetWorthView(APIView):
    """
    GET /analytics/net-worth

    Always reflects live current balances (BankAccount.current_balance,
    derived from the latest transaction per account) regardless of the
    `as_of` query param's value — true point-in-time historical snapshots
    would read from `net_worth_snapshots`, but nothing populates that table
    yet (same background-job gap as recurring-charges/anomalies above).
    `as_of_date` in the response echoes the requested date (or today) for
    shape-compatibility, without claiming to reconstruct a past balance.

    `as_of` is NOT a filter (PLAN.md Checkpoint F) — it never touches the
    account query above, purely echoed back — so no FilterSet applies here
    by definition; documented manually instead.
    """

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "as_of",
                OpenApiTypes.DATE,
                required=False,
                description="Echoed back in as_of_date only — see docstring",
            )
        ],
        responses={200: NetWorthResponseSerializer},
    )
    def get(self, request):
        as_of = request.query_params.get("as_of") or date.today().isoformat()
        accounts = BankAccount.objects.filter(user=request.user, is_active=True)
        per_account = [
            {
                "account_id": str(acct.id),
                "bank_name": acct.bank_name,
                # BankAccount.current_balance can be None even when a latest
                # transaction exists — its fallback to 0.00 only applies when
                # there's no transaction at all, not when a real transaction's
                # own `balance` field was left unset (true for every mock
                # transaction from the Statements pipeline, which never sets
                # it). Coalesced to 0 here since the documented response shape
                # for `balance` is always "number", never null.
                "balance": acct.current_balance or Decimal("0"),
            }
            for acct in accounts
        ]
        total = sum((a["balance"] for a in per_account), Decimal("0"))
        return Response(
            {
                "as_of_date": as_of,
                "total_across_accounts": total,
                "per_account_breakdown": per_account,
            }
        )


def compute_stability_score(user):
    """
    Shared by StabilityScoreView below and the Budgets domain's Dashboard
    aggregate (core/views/budgets.py) — factored out here rather than
    duplicated, per System_Architecture.md §4's "if a number can be computed
    from transactions, it is computed, not duplicated" rule. Returns None
    when there isn't enough data (fewer than 2 months of inflow), letting
    each caller decide how to represent that in its own response shape.

    A simple, real (not AI-mocked) heuristic over actual inflow data: the
    coefficient of variation of the last 6 months' total credit inflow,
    inverted into a 0-100 score. Intentionally simple arithmetic, not a
    statistical/ML model — good enough to exercise real data end-to-end; a
    more sophisticated model can replace just this function's body later
    without changing either caller's response shape.
    """
    monthly_inflows = list(
        Transaction.objects.filter(user=user, transaction_type="credit")
        .annotate(month=TruncMonth("transaction_date"))
        .values("month")
        .annotate(total=Sum("amount"))
        .order_by("-month")[:6]
        .values_list("total", flat=True)
    )
    if len(monthly_inflows) < 2:
        return None

    amounts = [float(a) for a in monthly_inflows]
    mean = sum(amounts) / len(amounts)
    variance = sum((a - mean) ** 2 for a in amounts) / len(amounts)
    coefficient_of_variation = (variance**0.5 / mean) if mean else 1.0
    return max(0.0, min(100.0, round((1 - coefficient_of_variation) * 100, 1)))


class StabilityScoreView(APIView):
    """GET /analytics/stability-score

    `period` is NOT a filter (PLAN.md Checkpoint F) — confirmed by reading
    compute_stability_score() below, which takes no period argument at all;
    it's purely echoed back in computed_for_period. No FilterSet applies
    here by definition; documented manually instead.
    """

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "period",
                OpenApiTypes.STR,
                required=False,
                description="Echoed back in computed_for_period only — see docstring",
            )
        ],
        responses={200: StabilityScoreResponseSerializer},
    )
    def get(self, request):
        period = request.query_params.get("period")
        score = compute_stability_score(request.user)
        if score is None:
            return Response(
                {
                    "score": None,
                    "label": "insufficient_data",
                    "computed_for_period": period or "all",
                }
            )
        label = "stable" if score >= 70 else "variable" if score >= 40 else "unstable"
        return Response(
            {"score": score, "label": label, "computed_for_period": period or "last_6_months"}
        )
