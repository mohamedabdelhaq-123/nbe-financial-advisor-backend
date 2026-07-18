import calendar
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Sum
from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.generics import ListAPIView
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.exceptions import ConflictError
from core.filters.budgets import BudgetHistoryFilterSet
from core.models import BankAccount, Budget, BudgetAllocation, BudgetHistory, Goal, Transaction
from core.openapi import error_responses
from core.serializers.budgets import (
    BudgetCreateSerializer,
    BudgetHistorySerializer,
    BudgetProgressResponseSerializer,
    BudgetResponseSerializer,
    BudgetUpdateSerializer,
    DashboardGoalRequestSerializer,
    DashboardGoalResponseSerializer,
    DashboardResponseSerializer,
    GoalInputSerializer,
    GoalUpdateSerializer,
    SavingsProgressResponseSerializer,
    StarterTemplateSerializer,
)
from core.views.aggregations import compute_stability_score
from services import file_storage


def _months_elapsed(start_date, end_date):
    """Whole calendar-month difference — used throughout this file to derive
    `months_remaining` from `Goal.timeline_months` and a reference start date."""
    return (end_date.year - start_date.year) * 12 + (end_date.month - start_date.month)


def _months_remaining(goal):
    if goal is None:
        return None
    # Reference point is Goal.created_at (PLAN.md Checkpoint C) — previously
    # this was budget.created_at, which is set once at plan-creation time and
    # never updates when a goal is later added/changed on top of an existing
    # plan; that mismatch is what made GET /budget/savings-progress always
    # compute ~0% for a plan created well before its goal was set. Goal now
    # being its own entity with its own created_at fixes this directly.
    elapsed = _months_elapsed(goal.created_at.date(), date.today())
    return max(0, goal.timeline_months - elapsed)


def _saved_so_far(user, since_date):
    """
    Net cash flow (inflow - outflow) since `since_date`, clamped at 0 — used
    as an approximation of "progress toward the savings goal" by both
    SavingsProgressView and DashboardView. There's no dedicated mechanism in
    the schema for tagging transactions as "goes toward the savings goal"
    (no separate savings account/envelope concept), so this proxies it with
    the user's overall net cash position instead — a simplification, not a
    precise tracked figure.
    """
    txns = Transaction.objects.filter(user=user, transaction_date__gte=since_date)
    inflow = txns.filter(transaction_type="credit").aggregate(t=Sum("amount"))["t"] or Decimal("0")
    outflow = txns.filter(transaction_type__in=["debit", "fee"]).aggregate(t=Sum("amount"))[
        "t"
    ] or Decimal("0")
    return max(Decimal("0"), inflow - outflow)


def _serialize_budget(budget):
    """GET/POST/PATCH /budget all return this same shape (Data_Shapes_Budgets.md).
    No `goal` key — Goal is its own entity (PLAN.md Checkpoint C), reached
    via GET /goal or GET /dashboard, not nested here."""
    return {
        "id": str(budget.id),
        "name": budget.name,
        "period_type": budget.period_type,
        "status": budget.status,
        "selected_template_key": budget.selected_template_key,
        "allocations": [
            {
                "category": a.category.name,
                "allocated_percentage": a.allocated_percentage,
                "allocated_amount": a.allocated_amount,
                "currency": a.currency,
            }
            for a in budget.allocations.all()
        ],
        "created_at": budget.created_at,
        "updated_at": budget.updated_at,
    }


def _snapshot(budget):
    """
    A budget_history.previous_values payload — captured BEFORE a change is
    applied (Data_Governance_Specs.md §4: "the prior state is versioned/
    snapshotted first"). Decimal -> float conversion is required here (not
    just style): BudgetHistory.previous_values is a plain JSONField with no
    custom encoder, and the stdlib json encoder Django falls back to can't
    serialize Decimal on its own.

    No `goal` key — Goal is its own entity now (PLAN.md Checkpoint C), with
    no history/versioning of its own; budget_history only tracks Budget's
    own fields (name, allocations).
    """
    return {
        "allocations": [
            {
                "category": a.category.name,
                "allocated_percentage": float(a.allocated_percentage),
                "allocated_amount": float(a.allocated_amount),
            }
            for a in budget.allocations.all()
        ],
    }


def _apply_allocations(budget, allocations, monthly_income):
    """
    The percentage + derived-amount convention (API Design Guidelines §3):
    client sends only allocated_percentage, backend computes and stores
    allocated_amount = monthly_income * percentage — never recomputed live on
    every read, so historical plans stay correct even if monthly_income
    changes later.
    """
    for item in allocations:
        percentage = item["allocated_percentage"]
        amount = (monthly_income * percentage / Decimal("100")).quantize(Decimal("0.01"))
        BudgetAllocation.objects.create(
            budget=budget,
            category=item["category"],
            allocated_percentage=percentage,
            allocated_amount=amount,
        )


class BudgetView(APIView):
    """The user's single active budget plan — one plan per user, no
    parallel/historical rows kept on this table (see GET /budget/history
    for that). There is no `goal` field on this shape; a savings goal is a
    separate entity reached via GET /goal or GET /dashboard."""

    @extend_schema(
        description="Fetch the current budget plan. 404 if the user hasn't created one yet.",
        responses={200: BudgetResponseSerializer, **error_responses(404)},
    )
    def get(self, request):
        budget = Budget.objects.filter(user=request.user).prefetch_related("allocations").first()
        if budget is None:
            raise NotFound("No budget plan exists yet.")
        return Response(_serialize_budget(budget))

    @extend_schema(
        description=(
            "Create the user's one budget plan. Rejected with 409 if one "
            "already exists — PATCH /budget is the only way to change an "
            "existing plan, there's no way to have two. `allocations` is "
            "sent as percentages only; the backend computes and stores "
            "each category's allocated_amount from the user's monthly "
            "income, and the percentages across all categories must sum "
            "to exactly 100 (422 otherwise)."
        ),
        request=BudgetCreateSerializer,
        responses={201: BudgetResponseSerializer, **error_responses(409, 422)},
    )
    def post(self, request):
        if Budget.objects.filter(user=request.user).exists():
            raise ConflictError(
                "A budget plan already exists for this user. Use PATCH /budget to update it."
            )

        serializer = BudgetCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        budget = Budget.objects.create(
            user=request.user,
            name=data.get("name") or "My Plan",
            selected_template_key=data.get("selected_template_key"),
        )
        _apply_allocations(budget, data["allocations"], request.user.monthly_income or Decimal("0"))
        return Response(_serialize_budget(budget), status=201)

    @extend_schema(
        description=(
            "Partially update the existing budget plan (404 if none exists "
            "yet — POST /budget creates it first). If `allocations` is "
            "included, it fully replaces the existing category set rather "
            "than merging with it, and must still sum to 100 (422 "
            "otherwise). Every change is snapshotted into GET /budget/history "
            "before being applied."
        ),
        request=BudgetUpdateSerializer,
        responses={200: BudgetResponseSerializer, **error_responses(404, 422)},
    )
    def patch(self, request):
        budget = get_object_or_404(
            Budget.objects.prefetch_related("allocations"), user=request.user
        )
        serializer = BudgetUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # Snapshot BEFORE applying the change — see _snapshot()'s docstring.
        BudgetHistory.objects.create(
            budget=budget,
            previous_values=_snapshot(budget),
            changed_via=data.get("changed_via", "dashboard"),
        )

        if "name" in data:
            budget.name = data["name"]
        budget.save()

        if "allocations" in data:
            # Full replacement, not a merge (Data_Shapes_Budgets.md).
            budget.allocations.all().delete()
            _apply_allocations(
                budget, data["allocations"], request.user.monthly_income or Decimal("0")
            )

        budget.refresh_from_db()
        return Response(_serialize_budget(budget))


@extend_schema_view(
    get=extend_schema(responses={200: BudgetHistorySerializer, **error_responses(404)})
)
class BudgetHistoryView(ListAPIView):
    """
    List every recorded change to the user's budget plan, newest first —
    each row's `previous_values` is a snapshot of the plan's state
    immediately before that change was applied, and `changed_via` records
    what triggered it (`dashboard`, `chat`, or `onboarding`). 404 if
    the user has no budget plan at all yet (there's nothing to have a
    history of).
    """

    serializer_class = BudgetHistorySerializer
    pagination_class = LimitOffsetPagination
    filter_backends = [DjangoFilterBackend]
    filterset_class = BudgetHistoryFilterSet

    def get_queryset(self):
        # swagger_fake_view: see aggregations.py's TransactionListCreateView.get_queryset().
        if getattr(self, "swagger_fake_view", False):
            return BudgetHistory.objects.none()
        budget = get_object_or_404(Budget, user=self.request.user)
        return BudgetHistory.objects.filter(budget=budget).order_by("-changed_at")


class BudgetProgressView(APIView):
    """
    Per-category spend-vs-allocation progress for one calendar month
    (`period`, defaults to the current month) — how much was actually
    spent in each budgeted category against how much was allocated, plus
    an `on_track` / `approaching_limit` / `over_budget` status per
    category (the threshold for "approaching" is 80% of the allocation).

    `period` genuinely filters the underlying transactions per category,
    but the response is a custom aggregated shape rather than a serialized
    queryset, so — same reasoning as the Analytics domain's
    MonthlySummariesView/CategoryBreakdownView — this stays a
    manually-documented view. 404 if the user has no budget plan yet.
    """

    APPROACHING_LIMIT_THRESHOLD = 80

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "period",
                OpenApiTypes.STR,
                required=False,
                description="YYYY-MM, defaults to the current period",
            )
        ],
        responses={200: BudgetProgressResponseSerializer, **error_responses(404)},
    )
    def get(self, request):
        budget = get_object_or_404(
            Budget.objects.prefetch_related("allocations"), user=request.user
        )
        period = request.query_params.get("period") or date.today().strftime("%Y-%m")
        year, month = (int(p) for p in period.split("-"))

        categories = []
        for alloc in budget.allocations.all():
            actual = Transaction.objects.filter(
                user=request.user,
                category=alloc.category,
                transaction_date__year=year,
                transaction_date__month=month,
                transaction_type__in=["debit", "fee"],
            ).aggregate(t=Sum("amount"))["t"] or Decimal("0")

            percentage_used = (
                float(actual / alloc.allocated_amount * 100) if alloc.allocated_amount else 0.0
            )
            if percentage_used >= 100:
                category_status = "over_budget"
            elif percentage_used >= self.APPROACHING_LIMIT_THRESHOLD:
                category_status = "approaching_limit"
            else:
                category_status = "on_track"

            categories.append(
                {
                    "category": alloc.category.name,
                    "allocated_amount": alloc.allocated_amount,
                    "actual_amount": actual,
                    "percentage_used": round(percentage_used, 2),
                    "status": category_status,
                }
            )

        return Response({"period": period, "categories": categories})


class SavingsProgressView(APIView):
    """
    Progress toward the user's savings goal: amount saved so far, percentage
    complete, a projected completion date, and whether that projection is
    on track to land within the goal's own timeline. "Saved so far" is
    approximated as net cash flow (inflow minus outflow) since the goal was
    created — there's no dedicated mechanism for tagging individual
    transactions as "goes toward this goal", so this is an approximation
    of overall net cash position, not a precisely tracked figure.

    Only requires a savings goal to exist — not a budget plan, since the
    two are fully independent entities. 404 if no goal has been set yet.
    """

    @extend_schema(responses={200: SavingsProgressResponseSerializer, **error_responses(404)})
    def get(self, request):
        goal = Goal.objects.filter(user=request.user).first()
        if goal is None:
            raise NotFound("No savings goal set.")

        since = goal.created_at.date()
        saved_so_far = _saved_so_far(request.user, since)
        target = goal.target_amount
        percentage_complete = (
            float(min(Decimal("100"), saved_so_far / target * 100)) if target else 0.0
        )

        months_elapsed = max(1, _months_elapsed(since, date.today()))
        monthly_rate = saved_so_far / months_elapsed
        projected_completion_date = None
        if saved_so_far >= target:
            projected_completion_date = date.today()
        elif monthly_rate > 0:
            months_needed = float((target - saved_so_far) / monthly_rate)
            projected_completion_date = date.today() + timedelta(days=round(months_needed * 30))

        deadline = since + timedelta(days=goal.timeline_months * 30)
        on_track = projected_completion_date is not None and projected_completion_date <= deadline

        return Response(
            {
                "goal": {
                    "name": goal.name,
                    "target_amount": target,
                    "months_remaining": _months_remaining(goal),
                },
                "saved_so_far": saved_so_far,
                "percentage_complete": round(percentage_complete, 2),
                "projected_completion_date": projected_completion_date,
                "on_track": on_track,
            }
        )


class StarterTemplatesView(APIView):
    """
    List the reference budget-allocation templates (e.g. "balanced",
    "aggressive_savings") shown during onboarding, each with its
    category/percentage breakdown. Public — no authentication required —
    since onboarding renders these before the user has an account or
    token, and the templates themselves aren't user-specific data.

    Exactly one template has `is_suggested: true`. For a signed-in user
    with steady income and no dependents, that's `"aggressive_savings"`
    (more room to safely save); every other case — including an
    unauthenticated onboarding visitor, who has no income/dependents
    signal to go on yet — falls back to `"balanced"`.
    """

    permission_classes = [AllowAny]

    @extend_schema(responses={200: StarterTemplateSerializer(many=True)})
    def get(self, request):
        templates = file_storage.get_onboarding_templates()
        # Simple heuristic for which template gets is_suggested=true — a real
        # implementation would ground this via the AI service's planning
        # signals (System_Architecture.md §7's reference-template grounding).
        # Picks "aggressive_savings" only for a signed-in steady-income user
        # with no dependents (more room to safely save); "balanced" otherwise,
        # which is also the default an anonymous onboarding visitor sees since
        # no income/dependents signals exist for them yet.
        suggested_key = "balanced"
        user = request.user
        if (
            user.is_authenticated
            and user.income_steadiness == "steady"
            and user.dependents_count == 0
        ):
            suggested_key = "aggressive_savings"
        for template in templates:
            template["is_suggested"] = template["template_key"] == suggested_key
        return Response(templates)


def _goal_progress(goal):
    """Shared by GoalView, DashboardView, and DashboardGoalView — all return
    this exact shape (Data_Shapes_Budgets.md: PATCH /dashboard/goal's
    response is "same goal shape as GET /dashboard"). Returns None outright
    when there's no Goal row at all (PLAN.md Checkpoint C: "optional" means
    no row exists, full stop — not a dict of null-ish fields)."""
    if goal is None:
        return None
    percentage_complete = 0.0
    if goal.target_amount:
        saved = _saved_so_far(goal.user, goal.created_at.date())
        percentage_complete = float(min(Decimal("100"), saved / goal.target_amount * 100))
    return {
        "name": goal.name,
        "target_amount": goal.target_amount,
        "months_remaining": _months_remaining(goal),
        "percentage_complete": round(percentage_complete, 2),
    }


class GoalView(APIView):
    """
    The user's single savings goal — its own entity, one-to-one with the
    user, independent of whether a budget plan exists (a user can have a
    goal with no budget, or vice versa). "No goal set" means no row exists
    at all, never a dict of null-ish fields, so every operation here 404s
    cleanly when there isn't one (except POST, which is how you create it).
    """

    @extend_schema(
        description="Fetch the current savings goal and its progress. 404 if none is set.",
        responses={200: DashboardGoalResponseSerializer, **error_responses(404)},
    )
    def get(self, request):
        goal = Goal.objects.filter(user=request.user).first()
        if goal is None:
            raise NotFound("No savings goal set.")
        return Response(_goal_progress(goal))

    @extend_schema(
        description=(
            "Create the user's one savings goal. Rejected with 409 if one "
            "already exists — PATCH /goal is the only way to change an "
            "existing goal."
        ),
        request=GoalInputSerializer,
        responses={201: DashboardGoalResponseSerializer, **error_responses(409, 422)},
    )
    def post(self, request):
        if Goal.objects.filter(user=request.user).exists():
            raise ConflictError(
                "A savings goal already exists for this user. Use PATCH /goal to update it."
            )
        serializer = GoalInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        goal = Goal.objects.create(
            user=request.user,
            name=data["name"],
            target_amount=data["target_amount"],
            timeline_months=data["target_months"],
        )
        return Response(_goal_progress(goal), status=201)

    @extend_schema(
        description="Partially update the existing goal. 404 if none exists yet.",
        request=GoalUpdateSerializer,
        responses={200: DashboardGoalResponseSerializer, **error_responses(404, 422)},
    )
    def patch(self, request):
        goal = get_object_or_404(Goal, user=request.user)
        serializer = GoalUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if "name" in data:
            goal.name = data["name"]
        if "target_amount" in data:
            goal.target_amount = data["target_amount"]
        if "target_months" in data:
            goal.timeline_months = data["target_months"]
        goal.save()
        return Response(_goal_progress(goal))

    @extend_schema(
        description=(
            "Delete the savings goal entirely. Does not affect any budget "
            "plan or transaction history — only the goal itself and its "
            "progress tracking disappear. 404 if none exists."
        ),
        responses={204: None, **error_responses(404)},
    )
    def delete(self, request):
        goal = get_object_or_404(Goal, user=request.user)
        goal.delete()
        return Response(status=204)


_DASHBOARD_PERIODS = ("this_month", "last_month", "last_3_months", "this_year")


def _resolve_window(period):
    """(start, end, prev_start, prev_end) for a GET /dashboard `period` value.

    `end` is always today (these are all "up to now" windows, not fixed
    historical ranges). `prev_start`/`prev_end` is the immediately preceding
    span of the *same length in days* as (start, end) — this is what lets
    `_percentage_change()` below generalize from "this month vs last month"
    to any window length without changing its own logic at all.
    """
    today = date.today()
    if period == "last_month":
        if today.month == 1:
            year, month = today.year - 1, 12
        else:
            year, month = today.year, today.month - 1
        start = date(year, month, 1)
        end = date(year, month, calendar.monthrange(year, month)[1])
    elif period == "last_3_months":
        month = today.month - 3
        year = today.year
        while month <= 0:
            month += 12
            year -= 1
        start = date(year, month, 1)
        end = today
    elif period == "this_year":
        start = date(today.year, 1, 1)
        end = today
    else:  # "this_month" — also the default when no period is given
        start = date(today.year, today.month, 1)
        end = today

    length_days = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=length_days - 1)
    return start, end, prev_start, prev_end


def _window_totals(user, start, end, account=None):
    """(spend, inflow) for a user's transactions in [start, end], optionally
    restricted to a single BankAccount — shared by DashboardView's current-
    and preceding-window metrics. Computed live from Transaction, same as
    the pre-existing month-based figures this replaces."""
    txns = Transaction.objects.filter(user=user, transaction_date__range=(start, end))
    if account is not None:
        txns = txns.filter(account=account)
    spend = txns.filter(transaction_type__in=["debit", "fee"]).aggregate(t=Sum("amount"))[
        "t"
    ] or Decimal("0")
    inflow = txns.filter(transaction_type="credit").aggregate(t=Sum("amount"))["t"] or Decimal("0")
    return spend, inflow


def _percentage_change(current, previous):
    """(current - previous) / abs(previous) * 100 — explicit None (not a
    crash or a misleading infinity) when there's no previous-window figure to
    compare against."""
    if not previous:
        return None
    return round(float((current - previous) / abs(previous) * 100), 2)


class DashboardView(APIView):
    """
    Single combined read for the main dashboard screen: budget plan
    summary, goal progress, per-category allocation usage, spend/inflow
    metrics (vs. the preceding equal-length window), and net worth — one
    call instead of the frontend stitching together five separate ones.

    `period` (`this_month` | `last_month` | `last_3_months` | `this_year`,
    default `this_month`) selects the window every metric below is computed
    over. `account_id`, when given, restricts every metric to that one
    account's transactions/balance instead of all of the user's accounts
    combined — 404 if it doesn't belong to the current user.

    Always returns 200, even for a brand new user with no budget plan yet:
    `has_plan` is `false` and every plan-dependent field falls back to a
    zeroed/empty shape rather than 404ing, so the frontend can render its
    real empty state directly from this same response instead of branching
    on an error. Goal and budget are fetched independently of each other —
    a user can have one without the other. `period`/`account_id` are still
    validated in this branch (422/404 apply regardless of plan state) since
    net worth is real data either way.
    """

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "period",
                OpenApiTypes.STR,
                required=False,
                enum=list(_DASHBOARD_PERIODS),
                description="Window every metric is computed over. Defaults to this_month.",
            ),
            OpenApiParameter(
                "account_id",
                OpenApiTypes.UUID,
                required=False,
                description="Restrict every metric to this one account.",
            ),
        ],
        responses={200: DashboardResponseSerializer, **error_responses(404, 422)},
    )
    def get(self, request):
        period = request.query_params.get("period", "this_month")
        if period not in _DASHBOARD_PERIODS:
            raise ValidationError({"period": f"Must be one of {list(_DASHBOARD_PERIODS)}."})

        account = None
        account_id = request.query_params.get("account_id")
        if account_id:
            account = get_object_or_404(BankAccount, id=account_id, user=request.user)

        start, end, prev_start, prev_end = _resolve_window(period)

        if account is not None:
            total_net_worth = account.current_balance or Decimal("0")
        else:
            accounts = BankAccount.objects.filter(user=request.user, is_active=True)
            total_net_worth = sum(
                (a.current_balance or Decimal("0") for a in accounts), Decimal("0")
            )

        goal = Goal.objects.filter(user=request.user).first()
        budget = Budget.objects.filter(user=request.user).prefetch_related("allocations").first()
        if budget is None:
            # has_plan=false triggers the frontend's real, designed empty
            # state (Design.md §3) — not an error, so this is still a 200.
            return Response(
                {
                    "budget": None,
                    "goal": _goal_progress(goal),
                    "allocations_summary": [],
                    "metrics": {
                        "income_stability_score": compute_stability_score(request.user),
                        "current_month_spend": Decimal("0"),
                        "current_month_inflow": Decimal("0"),
                        "previous_month_spend": Decimal("0"),
                        "previous_month_inflow": Decimal("0"),
                        "spend_change_percentage": None,
                        "inflow_change_percentage": None,
                    },
                    "net_worth": {
                        "total_across_accounts": total_net_worth,
                        "as_of_date": date.today(),
                    },
                    "has_plan": False,
                }
            )

        current_spend, current_inflow = _window_totals(request.user, start, end, account)
        previous_spend, previous_inflow = _window_totals(
            request.user, prev_start, prev_end, account
        )

        window_txns = Transaction.objects.filter(user=request.user, transaction_date__range=(start, end))
        if account is not None:
            window_txns = window_txns.filter(account=account)

        allocations_summary = []
        for alloc in budget.allocations.all():
            actual = window_txns.filter(
                category=alloc.category, transaction_type__in=["debit", "fee"]
            ).aggregate(t=Sum("amount"))["t"] or Decimal("0")
            percentage_used = (
                float(actual / alloc.allocated_amount * 100) if alloc.allocated_amount else 0.0
            )
            allocations_summary.append(
                {
                    "category": alloc.category.name,
                    "allocated_percentage": alloc.allocated_percentage,
                    "percentage_used": round(percentage_used, 2),
                }
            )

        return Response(
            {
                "budget": {"id": str(budget.id), "name": budget.name, "status": budget.status},
                "goal": _goal_progress(goal),
                "allocations_summary": allocations_summary,
                "metrics": {
                    "income_stability_score": compute_stability_score(request.user),
                    "current_month_spend": current_spend,
                    "current_month_inflow": current_inflow,
                    "previous_month_spend": previous_spend,
                    "previous_month_inflow": previous_inflow,
                    "spend_change_percentage": _percentage_change(current_spend, previous_spend),
                    "inflow_change_percentage": _percentage_change(
                        current_inflow, previous_inflow
                    ),
                },
                "net_worth": {"total_across_accounts": total_net_worth, "as_of_date": end},
                "has_plan": True,
            }
        )


class DashboardGoalView(APIView):
    """
    Convenience upsert for the user's savings goal, callable directly from
    the dashboard screen: creates the goal if it doesn't exist yet, updates
    it if it does — a single call instead of the frontend having to check
    whether a goal already exists before deciding whether to call
    `POST /goal` or `PATCH /goal` itself. Same underlying goal entity,
    same response shape as `GET /goal`. The request body is wrapped in a
    top-level `goal` key: `{"goal": {"name", "target_amount", "target_months"}}`.
    """

    @extend_schema(
        request=DashboardGoalRequestSerializer,
        responses={200: DashboardGoalResponseSerializer, **error_responses(422)},
    )
    def patch(self, request):
        goal_data = request.data.get("goal")
        if not goal_data:
            raise ValidationError({"goal": "This field is required."})

        serializer = GoalInputSerializer(data=goal_data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        goal, _created = Goal.objects.update_or_create(
            user=request.user,
            defaults={
                "name": data["name"],
                "target_amount": data["target_amount"],
                "timeline_months": data["target_months"],
            },
        )

        return Response(_goal_progress(goal))
