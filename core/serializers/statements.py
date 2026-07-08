from rest_framework import serializers

from core.models import StatementFile


class StatementFileSerializer(serializers.ModelSerializer):
    # Renamed from DRF's default `account` (PrimaryKeyRelatedField) to
    # `account_id` to match the `_id`-suffixed foreign-reference convention
    # used throughout docs/API_GUIDE/Data_Shapes_*.md.
    account_id = serializers.PrimaryKeyRelatedField(source="account", read_only=True)
    failure_reason = serializers.SerializerMethodField()

    class Meta:
        model = StatementFile
        fields = [
            "id",
            "account_id",
            "status",
            "start_transaction_date",
            "last_transaction_date",
            "upload_date",
            "failure_reason",
        ]
        read_only_fields = fields

    def get_failure_reason(self, obj) -> str | None:
        # No `failure_reason` column exists on StatementFile — DB_Schema.md
        # doesn't define one, and the mock pipeline in services/ai_service.py
        # never actually fails. Kept as a field (always null for now) rather
        # than omitted, so this response shape already matches
        # Data_Shapes_Statements.md ahead of a real, fallible pipeline landing.
        return None


class StatementOcrResultResponseSerializer(serializers.Serializer):
    """GET /statements/{id}/ocr-result — output-only, see StatementFileSerializer's
    docstring pattern (documents core/views/statements.py's dict response)."""

    statement_id = serializers.UUIDField()
    ocr_engine = serializers.CharField()
    confidence_score = serializers.DecimalField(max_digits=4, decimal_places=3, allow_null=True)
    processed_at = serializers.DateTimeField()
    artifact_url = serializers.CharField()


class StatementNormalizedResponseSerializer(serializers.Serializer):
    """GET /statements/{id}/normalized — output-only, same pattern."""

    statement_id = serializers.UUIDField()
    model_used = serializers.CharField(allow_null=True)
    adjusted_at = serializers.DateTimeField()
    transaction_count = serializers.IntegerField()
    normalized_json = serializers.JSONField()
