from decimal import Decimal

from rest_framework import serializers

from core.models import BudgetHistory, Category


class GoalInputSerializer(serializers.Serializer):
    """
    Write-side goal shape: `name`, `target_amount`, `target_months`.
    Read-side responses replace `target_months` with `months_remaining`
    instead — a value computed at read time, not something stored 1:1 on
    the model. Used for POST /goal (full create) and PATCH /dashboard/goal
    (upsert) — both require every field, unlike PATCH /goal's partial update.
    """

    name = serializers.CharField(max_length=255)
    target_amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    target_months = serializers.IntegerField(min_value=1)


class GoalUpdateSerializer(serializers.Serializer):
    """PATCH /goal body — every field optional (a subset update), unlike
    GoalInputSerializer's all-required create/upsert shape."""

    name = serializers.CharField(max_length=255, required=False)
    target_amount = serializers.DecimalField(max_digits=14, decimal_places=2, required=False)
    target_months = serializers.IntegerField(min_value=1, required=False)


class DashboardGoalRequestSerializer(serializers.Serializer):
    """PATCH /dashboard/goal body — {"goal": {...}}."""

    goal = GoalInputSerializer()


class AllocationInputSerializer(serializers.Serializer):
    # A budget allocates across spending buckets only (income isn't budgeted
    # against), so this only ever resolves to an expense-type Category — an
    # unknown name or an income category name both raise a 400 here rather
    # than silently landing in no bucket.
    category = serializers.SlugRelatedField(
        slug_field="name", queryset=Category.objects.filter(category_type="expense")
    )
    allocated_percentage = serializers.DecimalField(max_digits=5, decimal_places=2)


def _validate_allocations_sum_100(allocations):
    total = sum((a["allocated_percentage"] for a in allocations), Decimal("0"))
    if total != Decimal("100"):
        raise serializers.ValidationError(f"Allocations must sum to 100. Sum was {total}.")
    return allocations


class BudgetCreateSerializer(serializers.Serializer):
    """POST /budget body. No `goal` here — a savings goal is its own entity
    with its own CRUD (POST/PATCH /goal), created independently of a
    budget plan rather than nested inside one."""

    name = serializers.CharField(max_length=255, required=False, default="My Plan")
    selected_template_key = serializers.CharField(max_length=50, required=False, allow_null=True)
    allocations = AllocationInputSerializer(many=True)

    def validate_allocations(self, allocations):
        return _validate_allocations_sum_100(allocations)


class BudgetUpdateSerializer(serializers.Serializer):
    """
    PATCH /budget body — every field optional (a subset update), but
    `allocations`, if present, replaces the full set rather than merging
    with existing categories, and the replacement set must sum to 100.
    No `goal` here — see BudgetCreateSerializer's docstring.
    """

    name = serializers.CharField(max_length=255, required=False)
    allocations = AllocationInputSerializer(many=True, required=False)
    # Lets a user switch which starter template their plan is based on (e.g.
    # "Change plan template" on the dashboard) — same convention as
    # BudgetCreateSerializer's field: the frontend resolves the template's
    # allocations client-side (from GET /budget/starter-templates, same as
    # onboarding) and sends both together, so this is just recorded, not
    # cross-validated against the reference-data store.
    selected_template_key = serializers.CharField(max_length=50, required=False, allow_null=True)
    # "chat", not "chat_hitl" — the chat allocation-confirmation widget
    # (AllocationSliderTool on the frontend) sends changed_via: "chat"; that
    # was previously rejected here since "chat_hitl" (never sent by any real
    # caller) was the only chat-flavored choice accepted.
    changed_via = serializers.ChoiceField(
        choices=["dashboard", "chat", "onboarding"], required=False, default="dashboard"
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
    """Read-side goal shape: `months_remaining` (computed) instead of the
    write-side `target_months`, so a client can't confuse "total plan
    length" with "time left"."""

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
    """No `goal` here — a savings goal is its own entity, reached via
    GET /goal or GET /dashboard, never nested under Budget."""

    id = serializers.UUIDField()
    name = serializers.CharField()
    period_type = serializers.CharField()
    status = serializers.CharField()
    selected_template_key = serializers.CharField(allow_null=True)
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


# ---------------------------------------------------------------------------
# Admin write shapes for /admin/onboarding-templates — see
# core/views/administration.py's AdminOnboardingTemplateListCreateView/
# AdminOnboardingTemplateDetailView. Same category/percentage validation as
# a real budget's allocations (AllocationInputSerializer above), just against
# reference-data JSON objects (services/file_storage.py) instead of a
# BudgetAllocation row.
# ---------------------------------------------------------------------------


class AdminTemplateAllocationInputSerializer(serializers.Serializer):
    category = serializers.SlugRelatedField(
        slug_field="name", queryset=Category.objects.filter(category_type="expense")
    )
    allocated_percentage = serializers.DecimalField(max_digits=5, decimal_places=2)


class AdminTemplateCreateSerializer(serializers.Serializer):
    # SlugField (not CharField) since template_key doubles as the JSON
    # object's filename (services/file_storage.py's
    # _onboarding_template_key()) — restricting it to letters/digits/
    # hyphens/underscores up front means a create can never produce a key
    # that isn't a safe, unambiguous S3 object key.
    template_key = serializers.SlugField(max_length=50)
    name = serializers.CharField(max_length=255)
    description = serializers.CharField(allow_blank=True, required=False, default="")
    allocations = AdminTemplateAllocationInputSerializer(many=True)

    def validate_allocations(self, allocations):
        return _validate_allocations_sum_100(allocations)


class AdminTemplateUpdateSerializer(serializers.Serializer):
    """PATCH body — every field optional; `template_key` is never
    reassignable (it's the object's identity/filename), and `allocations`,
    if present, fully replaces the existing set rather than merging with it,
    same convention as BudgetUpdateSerializer."""

    name = serializers.CharField(max_length=255, required=False)
    description = serializers.CharField(allow_blank=True, required=False)
    allocations = AdminTemplateAllocationInputSerializer(many=True, required=False)

    def validate_allocations(self, allocations):
        return _validate_allocations_sum_100(allocations)


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
    # Month-over-month differential metrics (PLAN.md Checkpoint D).
    previous_month_spend = serializers.DecimalField(max_digits=14, decimal_places=2)
    previous_month_inflow = serializers.DecimalField(max_digits=14, decimal_places=2)
    spend_change_percentage = serializers.FloatField(allow_null=True)
    inflow_change_percentage = serializers.FloatField(allow_null=True)


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
