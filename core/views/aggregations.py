from datetime import date
from decimal import Decimal

from django.db.models import Q, Sum
from django.db.models.functions import TruncMonth
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status
from rest_framework.exceptions import ValidationError
from rest_framework.generics import GenericAPIView, ListAPIView
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from core.exceptions import BusinessRuleError
from core.models import (
    AnomalyFlag,
    BankAccount,
    RecurringCharge,
    SpendingPatternInsight,
    Transaction,
)
from core.serializers.aggregations import (
    AnomalyFlagSerializer,
    AnomalyResolveSerializer,
    CategoryBreakdownResponseSerializer,
    MonthlySummaryItemSerializer,
    NetWorthResponseSerializer,
    RecurringChargeSerializer,
    SpendingPatternInsightSerializer,
    StabilityScoreResponseSerializer,
    TransactionDetailSerializer,
    TransactionListSerializer,
    TransactionPatchSerializer,
    TransactionWriteSerializer,
)

# ============================================================================
# Transactions — the real, fully CRUD-able ledger.
# ============================================================================


class TransactionListCreateView(ListAPIView):
    """GET/POST /transactions"""

    serializer_class = TransactionListSerializer
    pagination_class = LimitOffsetPagination
    ALLOWED_SORT_FIELDS = {
        "amount",
        "-amount",
        "transaction_date",
        "-transaction_date",
        "category",
        "-category",
        # "name" (merchant) and "date_added" (created_at, distinct from
        # transaction_date) — PLAN.md Checkpoint B.
        "merchant_normalized",
        "-merchant_normalized",
        "created_at",
        "-created_at",
    }

    def get_queryset(self):
        qs = Transaction.objects.filter(user=self.request.user)
        params = self.request.query_params
        if params.get("account_id"):
            qs = qs.filter(account_id=params["account_id"])
        if params.get("category"):
            qs = qs.filter(category=params["category"])
        if params.get("from"):
            qs = qs.filter(transaction_date__gte=params["from"])
        if params.get("to"):
            qs = qs.filter(transaction_date__lte=params["to"])
        if params.get("source"):
            qs = qs.filter(source=params["source"])
        if "is_recurring" in params:
            qs = qs.filter(is_recurring=params["is_recurring"].lower() == "true")
        if params.get("search"):
            search = params["search"]
            qs = qs.filter(
                Q(merchant_raw__icontains=search) | Q(merchant_normalized__icontains=search)
            )
        if params.get("min_amount"):
            qs = qs.filter(amount__gte=params["min_amount"])
        if params.get("max_amount"):
            qs = qs.filter(amount__lte=params["max_amount"])
        if params.get("transaction_type"):
            qs = qs.filter(transaction_type=params["transaction_type"])
        sort = params.get("sort", "-transaction_date")
        if sort not in self.ALLOWED_SORT_FIELDS:
            sort = "-transaction_date"
        return qs.order_by(sort)

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
    GET/PATCH/DELETE /transactions/{transaction_id}

    Built from Retrieve+Destroy mixins directly (not
    RetrieveUpdateDestroyAPIView) because the PATCH input shape
    (TransactionPatchSerializer, a restricted field subset) differs from the
    GET/response shape (TransactionDetailSerializer) — get_serializer_class()
    can't cleanly serve both through the generic update() flow, so PATCH is
    handled explicitly below instead.
    """

    serializer_class = TransactionDetailSerializer
    lookup_url_kwarg = "transaction_id"

    def get_queryset(self):
        return Transaction.objects.filter(user=self.request.user)

    def get(self, request, *args, **kwargs):
        return self.retrieve(request, *args, **kwargs)

    def patch(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = TransactionPatchSerializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(TransactionDetailSerializer(instance).data)

    def delete(self, request, *args, **kwargs):
        # "Triggers the same re-aggregation background tasks as an edit"
        # (Data_Shapes_Aggregations.md) — no-op here since no Celery worker
        # exists yet (PLAN.md §5); the analytics endpoints below already
        # compute live from the ledger on every read, so there's nothing
        # stale left to re-trigger in this mock's design anyway.
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
    """GET /analytics/monthly-summaries"""

    @extend_schema(responses={200: MonthlySummaryItemSerializer(many=True)})
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
    """GET /analytics/category-breakdown"""

    @extend_schema(responses={200: CategoryBreakdownResponseSerializer})
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


class RecurringChargesView(APIView):
    """
    GET /analytics/recurring-charges

    Reads whatever rows already exist in `recurring_charges`. Populating
    this table is a background, AI-service-driven detection job
    (System_Architecture.md §5) that doesn't exist yet — see this file's
    module docstring for why real detection logic isn't built here.
    """

    @extend_schema(responses={200: RecurringChargeSerializer(many=True)})
    def get(self, request):
        qs = RecurringCharge.objects.filter(user=request.user)
        account_id = request.query_params.get("account_id")
        if account_id:
            qs = qs.filter(account_id=account_id)
        return Response(RecurringChargeSerializer(qs, many=True).data)


class AnomaliesView(APIView):
    """GET /analytics/anomalies — same scope boundary as RecurringChargesView."""

    @extend_schema(responses={200: AnomalyFlagSerializer(many=True)})
    def get(self, request):
        qs = AnomalyFlag.objects.filter(transaction__user=request.user)
        severity = request.query_params.get("severity")
        if severity:
            qs = qs.filter(severity=severity)
        if "resolved" in request.query_params:
            qs = qs.filter(resolved=request.query_params["resolved"].lower() == "true")
        return Response(AnomalyFlagSerializer(qs, many=True).data)


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


class SpendingInsightsView(APIView):
    """GET /analytics/spending-insights — same scope boundary as RecurringChargesView."""

    @extend_schema(responses={200: SpendingPatternInsightSerializer(many=True)})
    def get(self, request):
        qs = SpendingPatternInsight.objects.filter(user=request.user)
        insight_type = request.query_params.get("insight_type")
        if insight_type:
            qs = qs.filter(insight_type=insight_type)
        period = request.query_params.get("period")
        if period:
            qs = qs.filter(period=period)
        return Response(SpendingPatternInsightSerializer(qs, many=True).data)


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
    """

    @extend_schema(responses={200: NetWorthResponseSerializer})
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
    """GET /analytics/stability-score"""

    @extend_schema(responses={200: StabilityScoreResponseSerializer})
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
