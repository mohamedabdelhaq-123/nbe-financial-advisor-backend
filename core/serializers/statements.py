from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from core.models import StatementFile
from core.serializers.aggregations import TransactionListSerializer


@extend_schema_field(OpenApiTypes.BINARY)
class _BinaryFileField(serializers.FileField):
    """Plain FileField, annotated for schema generation only.

    Has to be a field *class* decorated with @extend_schema_field, not a
    decorated instance — DRF re-instantiates every declared field via
    Field.__deepcopy__(), which reconstructs from the original __init__
    args/kwargs rather than copying instance state (see
    rest_framework.fields.Field.__deepcopy__), so an override set on an
    instance is silently dropped the moment it's bound to a serializer.
    A class-level override survives because it's inherited by the fresh
    instance instead of depending on copied instance state.
    """


class StatementFileSerializer(serializers.ModelSerializer):
    """List/base shape (GET /statements, and inherited by everything below).

    Carries the file-level metadata — file_size/file_type plus what
    normalization resolved (bank_name/account_hint/model_used/adjusted_at) —
    so a document list can show "which bank / what file / when parsed"
    without a per-row detail call. The heavy part (the transaction array
    itself) stays on StatementDetailSerializer: a list screen wants the
    metadata, the detail screen wants the transactions (Data_Shapes_Statements.md)."""

    # Renamed from DRF's default `account` (PrimaryKeyRelatedField) to
    # `account_id` to match the `_id`-suffixed foreign-reference convention
    # used throughout docs/API_GUIDE/Data_Shapes_*.md.
    account_id = serializers.PrimaryKeyRelatedField(source="account", read_only=True)
    bank_name = serializers.SerializerMethodField()
    account_hint = serializers.SerializerMethodField()
    model_used = serializers.SerializerMethodField()
    adjusted_at = serializers.SerializerMethodField()

    class Meta:
        model = StatementFile
        fields = [
            "id",
            "account_id",
            "status",
            "is_processing",
            "failure_reason",
            "failed_phase",
            "file_size",
            "file_type",
            "bank_name",
            "account_hint",
            "model_used",
            "adjusted_at",
            "start_transaction_date",
            "last_transaction_date",
            "upload_date",
        ]
        # Only the model-backed fields — the four SerializerMethodFields above
        # are read-only by nature and must not be listed here (DRF rejects a
        # declared field in read_only_fields).
        read_only_fields = [
            "id",
            "account_id",
            "status",
            "is_processing",
            "failure_reason",
            "failed_phase",
            "file_size",
            "file_type",
            "start_transaction_date",
            "last_transaction_date",
            "upload_date",
        ]

    def get_bank_name(self, obj) -> str | None:
        payload = obj.normalized_payload
        return payload.get("bank_name") if payload else None

    def get_account_hint(self, obj) -> str | None:
        payload = obj.normalized_payload
        return payload.get("account_hint") if payload else None

    def get_model_used(self, obj) -> str | None:
        # These describe the normalization run itself, not the mutable
        # pending batch — so they stay populated after processed too, unlike
        # the proposed-array flavor of `transactions` on the detail shape.
        record = obj.latest_normalized_record
        return record.model_used if record else None

    def get_adjusted_at(self, obj) -> str | None:
        # SerializerMethodField can't infer the type — annotate so the
        # generated OpenAPI schema types it as a (ISO-8601 string) timestamp
        # rather than defaulting to a bare string with a spectacular warning.
        record = obj.latest_normalized_record
        return record.adjusted_at if record else None


class StatementDetailSerializer(StatementFileSerializer):
    """Single-resource shape (POST /statements, GET/PATCH /statements/{id}).

    Everything the list carries, plus the transaction array itself — the
    detail route is where a client goes to review/approve the proposed batch
    or see the committed ledger rows. Kept off the list serializer above so a
    paginated document list isn't dragging a full transaction array per row."""

    transactions = serializers.SerializerMethodField()

    class Meta(StatementFileSerializer.Meta):
        fields = StatementFileSerializer.Meta.fields + ["transactions"]

    def get_transactions(self, obj) -> list | None:
        # Two different sources depending on status, same field name — the
        # frontend always reads `transactions` without needing to know
        # which stage produced it:
        #  - approval: the not-yet-committed proposed array from
        #    normalized_json, for the user to review/correct.
        #  - processed: the real ledger rows this statement produced
        #    (Transaction.statement, related_name="transactions"), once
        #    Statements' job is finished (Data_Governance_Specs.md §2) and
        #    the ledger is the source of truth — not the frozen proposal.
        if obj.status == StatementFile.STATUS_APPROVAL:
            payload = obj.normalized_payload
            return payload.get("transactions", []) if payload else None
        if obj.status == StatementFile.STATUS_PROCESSED:
            ledger_rows = obj.transactions.order_by("-transaction_date")
            return TransactionListSerializer(ledger_rows, many=True).data
        return None


class StatementUploadRequestSerializer(serializers.Serializer):
    """POST /statements — input-only, documents the multipart request body
    for drf-spectacular. Never actually used to validate: the view reads
    request.FILES/request.data directly (create_statement_from_upload()),
    same pattern as StatementOcrResultResponseSerializer's docstring below
    for the output side. Without this, drf-spectacular falls back to
    StatementFileSerializer (a fully read_only response shape with no
    `file` field at all) as the request body, which renders as an empty,
    unusable form in Swagger UI."""

    # Without COMPONENT_SPLIT_REQUEST (a global setting affecting every
    # serializer's schema naming, not just this one), drf-spectacular can't
    # tell this FileField is write-only and defaults to the read-side
    # `use_url` behavior (format: uri — a string, not a file picker). Scoped
    # override instead of flipping that project-wide setting for one field.
    file = _BinaryFileField()
    account_id = serializers.UUIDField(required=False)


class StatementPatchSerializer(serializers.Serializer):
    """PATCH /statements/{id} — validates the requested retry/advance target
    is a real, patchable status. Forward-vs-backward and already-processed
    checks happen in the view (core/views/statements.py), since they need
    the instance's current status, not just the input shape."""

    status = serializers.ChoiceField(
        choices=[
            StatementFile.STATUS_NORMALIZATION,
            StatementFile.STATUS_APPROVAL,
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


class TransactionApprovalItemSerializer(serializers.Serializer):
    """POST /statements/{id}/transactions — one row of the submitted batch.
    Matched to the proposed normalized_json array by position, not by an
    id (PLAN.md: no per-transaction addressing in this design) — the whole
    array is submitted and resolved together, corrections and all."""

    transaction_date = serializers.DateField()
    merchant_raw = serializers.CharField(
        max_length=500, allow_blank=True, allow_null=True, required=False
    )
    category = serializers.CharField(
        max_length=100, allow_blank=True, allow_null=True, required=False
    )
    amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    transaction_type = serializers.CharField(
        max_length=20, allow_blank=True, allow_null=True, required=False
    )


class TransactionApprovalResultSerializer(serializers.Serializer):
    """One resolved row in the response — either inserted (transaction_id
    set) or skipped as a ledger duplicate (duplicate_of set)."""

    transaction_date = serializers.DateField()
    merchant_raw = serializers.CharField(allow_blank=True, allow_null=True)
    amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    transaction_id = serializers.UUIDField(allow_null=True)
    duplicate_of = serializers.UUIDField(allow_null=True)


class TransactionApprovalResponseSerializer(serializers.Serializer):
    statement_status = serializers.CharField()
    resolved = TransactionApprovalResultSerializer(many=True)
