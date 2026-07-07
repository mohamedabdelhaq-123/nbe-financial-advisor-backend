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
