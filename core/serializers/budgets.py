from decimal import Decimal

from rest_framework import serializers

from core.models import BudgetHistory


class GoalInputSerializer(serializers.Serializer):
    """
    Write-side goal shape (API Design Guidelines §4): name, target_amount,
    target_months. Read-side responses replace target_months with
    months_remaining instead — built separately in core/views/budgets.py
    since it's a computed value, not something a serializer maps 1:1 from a
    model field.
    """

    name = serializers.CharField(max_length=255)
    target_amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    target_months = serializers.IntegerField(min_value=1)


class DashboardGoalRequestSerializer(serializers.Serializer):
    """PATCH /dashboard/goal body — {"goal": {...}}."""

    goal = GoalInputSerializer()


class AllocationInputSerializer(serializers.Serializer):
    category = serializers.CharField(max_length=100)
    allocated_percentage = serializers.DecimalField(max_digits=5, decimal_places=2)


def _validate_allocations_sum_100(allocations):
    total = sum((a["allocated_percentage"] for a in allocations), Decimal("0"))
    if total != Decimal("100"):
        raise serializers.ValidationError(f"Allocations must sum to 100. Sum was {total}.")
    return allocations


class BudgetCreateSerializer(serializers.Serializer):
    """POST /budget body."""

    name = serializers.CharField(max_length=255, required=False, default="My Plan")
    selected_template_key = serializers.CharField(max_length=50, required=False, allow_null=True)
    goal = GoalInputSerializer()
    allocations = AllocationInputSerializer(many=True)

    def validate_allocations(self, allocations):
        return _validate_allocations_sum_100(allocations)


class BudgetUpdateSerializer(serializers.Serializer):
    """
    PATCH /budget body — every field optional (a subset update), but
    `allocations`, if present, replaces the full set rather than merging
    (Data_Shapes_Budgets.md: "it replaces the full set... and must sum to 100").
    """

    name = serializers.CharField(max_length=255, required=False)
    goal = GoalInputSerializer(required=False)
    allocations = AllocationInputSerializer(many=True, required=False)
    changed_via = serializers.ChoiceField(
        choices=["dashboard", "chat_hitl", "onboarding"], required=False, default="dashboard"
    )

    def validate_allocations(self, allocations):
        return _validate_allocations_sum_100(allocations)


class BudgetHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = BudgetHistory
        fields = ["id", "previous_values", "changed_via", "changed_at"]
        read_only_fields = fields


# ---------------------------------------------------------------------------
# Output-only shapes for core/views/budgets.py's computed-dict responses —
# documentation purposes only (drf-spectacular, API Design Guidelines §11),
# mirroring each view's actual _serialize_budget()/_goal_progress()/etc.
# dict shape rather than backing real validation.
# ---------------------------------------------------------------------------


class GoalProgressSerializer(serializers.Serializer):
    """Read-side goal shape: months_remaining instead of target_months (API Design Guidelines §4)."""

    name = serializers.CharField(allow_null=True)
    target_amount = serializers.DecimalField(max_digits=14, decimal_places=2, allow_null=True)
    months_remaining = serializers.IntegerField(allow_null=True)


class DashboardGoalResponseSerializer(GoalProgressSerializer):
    percentage_complete = serializers.FloatField()


class AllocationOutputSerializer(serializers.Serializer):
    category = serializers.CharField()
    allocated_percentage = serializers.DecimalField(max_digits=5, decimal_places=2)
    allocated_amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    currency = serializers.CharField()


class BudgetResponseSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    period_type = serializers.CharField()
    status = serializers.CharField()
    selected_template_key = serializers.CharField(allow_null=True)
    goal = GoalProgressSerializer()
    allocations = AllocationOutputSerializer(many=True)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class BudgetProgressCategorySerializer(serializers.Serializer):
    category = serializers.CharField()
    allocated_amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    actual_amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    percentage_used = serializers.FloatField()
    status = serializers.CharField()


class BudgetProgressResponseSerializer(serializers.Serializer):
    period = serializers.CharField()
    categories = BudgetProgressCategorySerializer(many=True)


class SavingsProgressResponseSerializer(serializers.Serializer):
    goal = GoalProgressSerializer()
    saved_so_far = serializers.DecimalField(max_digits=14, decimal_places=2)
    percentage_complete = serializers.FloatField()
    projected_completion_date = serializers.DateField(allow_null=True)
    on_track = serializers.BooleanField()


class StarterTemplateAllocationSerializer(serializers.Serializer):
    category = serializers.CharField()
    allocated_percentage = serializers.DecimalField(max_digits=5, decimal_places=2)


class StarterTemplateSerializer(serializers.Serializer):
    template_key = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField()
    is_suggested = serializers.BooleanField()
    allocations = StarterTemplateAllocationSerializer(many=True)


class DashboardBudgetSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    status = serializers.CharField()


class DashboardAllocationSummarySerializer(serializers.Serializer):
    category = serializers.CharField()
    allocated_percentage = serializers.DecimalField(max_digits=5, decimal_places=2)
    percentage_used = serializers.FloatField()


class DashboardMetricsSerializer(serializers.Serializer):
    income_stability_score = serializers.FloatField(allow_null=True)
    current_month_spend = serializers.DecimalField(max_digits=14, decimal_places=2)
    current_month_inflow = serializers.DecimalField(max_digits=14, decimal_places=2)


class DashboardNetWorthSerializer(serializers.Serializer):
    total_across_accounts = serializers.DecimalField(max_digits=14, decimal_places=2)
    as_of_date = serializers.DateField()


class DashboardResponseSerializer(serializers.Serializer):
    budget = DashboardBudgetSerializer(allow_null=True)
    goal = DashboardGoalResponseSerializer(allow_null=True)
    allocations_summary = DashboardAllocationSummarySerializer(many=True)
    metrics = DashboardMetricsSerializer()
    net_worth = DashboardNetWorthSerializer()
    has_plan = serializers.BooleanField()
