from rest_framework import serializers

from core.models import AnomalyFlag, RecurringCharge, SpendingPatternInsight, Transaction


class TransactionListSerializer(serializers.ModelSerializer):
    # Renamed from DRF's default `account`/`statement` (PrimaryKeyRelatedField)
    # to the `_id`-suffixed convention used throughout the Data Shapes docs.
    account_id = serializers.PrimaryKeyRelatedField(source="account", read_only=True)
    statement_id = serializers.PrimaryKeyRelatedField(source="statement", read_only=True)

    class Meta:
        model = Transaction
        fields = [
            "id",
            "account_id",
            "statement_id",
            "transaction_date",
            "merchant_raw",
            "merchant_normalized",
            "category",
            "amount",
            "currency",
            "is_recurring",
            "confidence_score",
            "source",
            "balance",
            "transaction_type",
            "created_at",
        ]
        read_only_fields = fields


class TransactionDetailSerializer(TransactionListSerializer):
    """Same as the list shape plus extra_fields, per Data_Shapes_Aggregations.md's
    GET /transactions/{id} spec ("same shape as one item above, plus extra_fields")."""

    class Meta(TransactionListSerializer.Meta):
        fields = TransactionListSerializer.Meta.fields + ["extra_fields"]
        read_only_fields = fields


class TransactionWriteSerializer(serializers.ModelSerializer):
    """
    POST /transactions body. Deliberately excludes account_id — the view
    resolves and ownership-checks the account before this serializer ever
    runs (via get_object_or_404, so an unowned account_id 404s rather than
    surfacing as a field-validation 422 — API Design Guidelines §10). Also
    excludes source/is_recurring/confidence_score, which are backend-set or
    backend-computed, never client-supplied, per Data_Shapes_Aggregations.md.
    """

    class Meta:
        model = Transaction
        fields = ["transaction_date", "merchant_raw", "category", "amount", "currency", "transaction_type"]
        extra_kwargs = {
            "category": {"required": False},
            "currency": {"required": False},
        }


class TransactionPatchSerializer(serializers.ModelSerializer):
    """PATCH /transactions/{id} — only the subset Data_Shapes_Aggregations.md allows
    (account_id and source are deliberately not patchable, would misrepresent origin)."""

    class Meta:
        model = Transaction
        fields = ["category", "merchant_raw", "amount", "transaction_date", "transaction_type"]


class AnomalyFlagSerializer(serializers.ModelSerializer):
    transaction_id = serializers.PrimaryKeyRelatedField(source="transaction", read_only=True)

    class Meta:
        model = AnomalyFlag
        fields = ["id", "transaction_id", "reason", "severity", "resolved", "detected_at"]
        read_only_fields = ["id", "transaction_id", "reason", "severity", "detected_at"]


class AnomalyResolveSerializer(serializers.ModelSerializer):
    class Meta:
        model = AnomalyFlag
        fields = ["resolved"]
        extra_kwargs = {"resolved": {"required": True}}


class RecurringChargeSerializer(serializers.ModelSerializer):
    class Meta:
        model = RecurringCharge
        fields = [
            "id",
            "merchant_normalized",
            "frequency",
            "avg_amount",
            "last_occurrence_date",
            "next_expected_date",
        ]
        read_only_fields = fields


class SpendingPatternInsightSerializer(serializers.ModelSerializer):
    value = serializers.JSONField(source="value_json", read_only=True)

    class Meta:
        model = SpendingPatternInsight
        fields = ["insight_type", "period", "value", "created_at"]
        read_only_fields = fields


# ---------------------------------------------------------------------------
# Output-only shapes for the "live computed" analytics views in
# core/views/aggregations.py (MonthlySummariesView, CategoryBreakdownView,
# NetWorthView, StabilityScoreView) — these build plain dicts directly
# (no model instance backs a single response 1:1), so these serializers
# exist purely to document the response shape for drf-spectacular
# (API Design Guidelines §11), never for validation.
# ---------------------------------------------------------------------------


class TopMerchantSerializer(serializers.Serializer):
    merchant = serializers.CharField()
    total = serializers.DecimalField(max_digits=14, decimal_places=2)


class MonthlySummaryItemSerializer(serializers.Serializer):
    month = serializers.DateField()
    account_id = serializers.UUIDField(allow_null=True)
    total_spend = serializers.DecimalField(max_digits=14, decimal_places=2)
    total_inflow = serializers.DecimalField(max_digits=14, decimal_places=2)
    category_breakdown = serializers.DictField(child=serializers.DecimalField(max_digits=14, decimal_places=2))
    top_merchants = TopMerchantSerializer(many=True)


class CategoryBreakdownItemSerializer(serializers.Serializer):
    category = serializers.CharField()
    amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    percentage_of_total = serializers.FloatField()


class CategoryBreakdownResponseSerializer(serializers.Serializer):
    period = serializers.CharField()
    breakdown = CategoryBreakdownItemSerializer(many=True)


class NetWorthAccountBreakdownSerializer(serializers.Serializer):
    account_id = serializers.UUIDField()
    bank_name = serializers.CharField()
    balance = serializers.DecimalField(max_digits=14, decimal_places=2)


class NetWorthResponseSerializer(serializers.Serializer):
    as_of_date = serializers.CharField()
    total_across_accounts = serializers.DecimalField(max_digits=14, decimal_places=2)
    per_account_breakdown = NetWorthAccountBreakdownSerializer(many=True)


class StabilityScoreResponseSerializer(serializers.Serializer):
    score = serializers.FloatField(allow_null=True)
    label = serializers.CharField()
    computed_for_period = serializers.CharField()
