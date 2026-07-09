from rest_framework import serializers

from core.models import StatementFile


class StatementFileSerializer(serializers.ModelSerializer):
    # Renamed from DRF's default `account` (PrimaryKeyRelatedField) to
    # `account_id` to match the `_id`-suffixed foreign-reference convention
    # used throughout docs/API_GUIDE/Data_Shapes_*.md.
    account_id = serializers.PrimaryKeyRelatedField(source="account", read_only=True)

    class Meta:
        model = StatementFile
        fields = [
            "id",
            "account_id",
            "status",
            "failure_reason",
            "failed_phase",
            "start_transaction_date",
            "last_transaction_date",
            "upload_date",
        ]
        read_only_fields = fields


class StatementPatchSerializer(serializers.Serializer):
    """PATCH /statements/{id} — validates the requested retry/advance target
    is a real, patchable status. Forward-vs-backward and already-processed
    checks happen in the view (core/views/statements.py), since they need
    the instance's current status, not just the input shape."""

    status = serializers.ChoiceField(
        choices=[
            StatementFile.STATUS_PENDING_NORMALIZATION,
            StatementFile.STATUS_PENDING_APPROVAL,
        ]
    )


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
