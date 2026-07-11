from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Sum
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.generics import ListAPIView
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.exceptions import ConflictError
from core.models import BankAccount, Budget, BudgetAllocation, BudgetHistory, Goal, Transaction
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
                "category": a.category,
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
                "category": a.category,
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
    """GET/POST/PATCH /budget"""

    @extend_schema(responses={200: BudgetResponseSerializer})
    def get(self, request):
        budget = Budget.objects.filter(user=request.user).prefetch_related("allocations").first()
        if budget is None:
            raise NotFound("No budget plan exists yet.")
        return Response(_serialize_budget(budget))

    @extend_schema(request=BudgetCreateSerializer, responses={201: BudgetResponseSerializer})
    def post(self, request):
        if Budget.objects.filter(user=request.user).exists():
            # One plan per user, no parallel rows (Data_Governance_Specs.md
            # §4) — PATCH is the only way to change an existing plan.
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

    @extend_schema(request=BudgetUpdateSerializer, responses={200: BudgetResponseSerializer})
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


class BudgetHistoryView(ListAPIView):
    """GET /budget/history"""

    serializer_class = BudgetHistorySerializer
    pagination_class = LimitOffsetPagination

    def get_queryset(self):
        budget = get_object_or_404(Budget, user=self.request.user)
        qs = BudgetHistory.objects.filter(budget=budget)
        if self.request.query_params.get("from"):
            qs = qs.filter(changed_at__date__gte=self.request.query_params["from"])
        if self.request.query_params.get("to"):
            qs = qs.filter(changed_at__date__lte=self.request.query_params["to"])
        return qs.order_by("-changed_at")


class BudgetProgressView(APIView):
    """GET /budget/progress"""

    # Simple, clearly-labeled thresholds — not a documented business rule,
    # just a reasonable default for the on_track/approaching_limit/over_budget
    # status field Data_Shapes_Budgets.md requires per category.
    APPROACHING_LIMIT_THRESHOLD = 80

    @extend_schema(responses={200: BudgetProgressResponseSerializer})
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
                    "category": alloc.category,
                    "allocated_amount": alloc.allocated_amount,
                    "actual_amount": actual,
                    "percentage_used": round(percentage_used, 2),
                    "status": category_status,
                }
            )

        return Response({"period": period, "categories": categories})


class SavingsProgressView(APIView):
    """GET /budget/savings-progress

    No longer requires a Budget to exist (PLAN.md Checkpoint C) — only a
    Goal, since the two are now fully independent entities.
    """

    @extend_schema(responses={200: SavingsProgressResponseSerializer})
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
    """GET /budget/starter-templates"""

    # Public: the frontend shows these during onboarding, before the user has
    # an account/token. The reference templates themselves aren't user-scoped
    # (Data Governance Specs §4), so there's nothing to leak — only the
    # is_suggested flag depends on the user, and that gracefully falls back to
    # a sensible default when there's no authenticated user (see below).
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
    """GET/POST/PATCH/DELETE /goal

    The user's single savings goal — its own entity, one-to-one with User
    (PLAN.md Checkpoint C), independent of whether a budget plan exists.
    "Optional" means no row exists at all, not a budget with null-ish goal
    fields.
    """

    @extend_schema(responses={200: DashboardGoalResponseSerializer})
    def get(self, request):
        goal = Goal.objects.filter(user=request.user).first()
        if goal is None:
            raise NotFound("No savings goal set.")
        return Response(_goal_progress(goal))

    @extend_schema(request=GoalInputSerializer, responses={201: DashboardGoalResponseSerializer})
    def post(self, request):
        if Goal.objects.filter(user=request.user).exists():
            # One-to-one with User (DB-level guarantee) — this just turns
            # what would otherwise be an IntegrityError into a clean 409.
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

    @extend_schema(request=GoalUpdateSerializer, responses={200: DashboardGoalResponseSerializer})
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

    @extend_schema(responses={204: None})
    def delete(self, request):
        goal = get_object_or_404(Goal, user=request.user)
        goal.delete()
        return Response(status=204)


def _month_totals(user, year, month):
    """(spend, inflow) for a user's transactions in a given calendar month —
    shared by DashboardView's current- and previous-month metrics (PLAN.md
    Checkpoint D). Computed live from Transaction, same as the pre-existing
    current-month figures — not MonthlySummary, which DashboardView doesn't
    read from today."""
    txns = Transaction.objects.filter(
        user=user, transaction_date__year=year, transaction_date__month=month
    )
    spend = txns.filter(transaction_type__in=["debit", "fee"]).aggregate(t=Sum("amount"))[
        "t"
    ] or Decimal("0")
    inflow = txns.filter(transaction_type="credit").aggregate(t=Sum("amount"))["t"] or Decimal("0")
    return spend, inflow


def _percentage_change(current, previous):
    """(current - previous) / abs(previous) * 100 — explicit None (not a
    crash or a misleading infinity) when there's no previous-month figure to
    compare against."""
    if not previous:
        return None
    return round(float((current - previous) / abs(previous) * 100), 2)


class DashboardView(APIView):
    """GET /dashboard — aggregate endpoint (API Design Guidelines §7)."""

    @extend_schema(responses={200: DashboardResponseSerializer})
    def get(self, request):
        # Fetched independently of budget (PLAN.md Checkpoint C) — a user can
        # have a Goal with no Budget, or vice versa; the two are no longer
        # coupled the way embedded goal fields on Budget used to be.
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
                        "total_across_accounts": Decimal("0"),
                        "as_of_date": date.today(),
                    },
                    "has_plan": False,
                }
            )

        today = date.today()
        month_txns = Transaction.objects.filter(
            user=request.user,
            transaction_date__year=today.year,
            transaction_date__month=today.month,
        )
        current_month_spend = month_txns.filter(transaction_type__in=["debit", "fee"]).aggregate(
            t=Sum("amount")
        )["t"] or Decimal("0")
        current_month_inflow = month_txns.filter(transaction_type="credit").aggregate(
            t=Sum("amount")
        )["t"] or Decimal("0")

        # Month-over-month differential metrics (PLAN.md Checkpoint D).
        if today.month == 1:
            prev_year, prev_month = today.year - 1, 12
        else:
            prev_year, prev_month = today.year, today.month - 1
        previous_month_spend, previous_month_inflow = _month_totals(
            request.user, prev_year, prev_month
        )

        allocations_summary = []
        for alloc in budget.allocations.all():
            actual = month_txns.filter(
                category=alloc.category, transaction_type__in=["debit", "fee"]
            ).aggregate(t=Sum("amount"))["t"] or Decimal("0")
            percentage_used = (
                float(actual / alloc.allocated_amount * 100) if alloc.allocated_amount else 0.0
            )
            allocations_summary.append(
                {
                    "category": alloc.category,
                    "allocated_percentage": alloc.allocated_percentage,
                    "percentage_used": round(percentage_used, 2),
                }
            )

        accounts = BankAccount.objects.filter(user=request.user, is_active=True)
        total_net_worth = sum((a.current_balance or Decimal("0") for a in accounts), Decimal("0"))

        return Response(
            {
                "budget": {"id": str(budget.id), "name": budget.name, "status": budget.status},
                "goal": _goal_progress(goal),
                "allocations_summary": allocations_summary,
                "metrics": {
                    "income_stability_score": compute_stability_score(request.user),
                    "current_month_spend": current_month_spend,
                    "current_month_inflow": current_month_inflow,
                    "previous_month_spend": previous_month_spend,
                    "previous_month_inflow": previous_month_inflow,
                    "spend_change_percentage": _percentage_change(
                        current_month_spend, previous_month_spend
                    ),
                    "inflow_change_percentage": _percentage_change(
                        current_month_inflow, previous_month_inflow
                    ),
                },
                "net_worth": {"total_across_accounts": total_net_worth, "as_of_date": today},
                "has_plan": True,
            }
        )


class DashboardGoalView(APIView):
    """
    PATCH /dashboard/goal

    Convenience upsert alias for the user's Goal (creates it if it doesn't
    exist yet, updates it if it does) — operates on the standalone Goal
    entity (PLAN.md Checkpoint C), not nested Budget fields anymore. Doesn't
    hold any goal-update logic of its own beyond the upsert; GoalView's
    POST/PATCH are the "own entity, own CRUD" surface this delegates to
    conceptually (same fields, same _goal_progress() response shape).
    """

    @extend_schema(
        request=DashboardGoalRequestSerializer, responses={200: DashboardGoalResponseSerializer}
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
