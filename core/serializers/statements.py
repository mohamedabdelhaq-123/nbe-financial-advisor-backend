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
            "is_processing",
            "failure_reason",
            "failed_phase",
            "start_transaction_date",
            "last_transaction_date",
            "upload_date",
        ]
        read_only_fields = fields


class StatementDetailSerializer(StatementFileSerializer):
    """Single-resource shape (POST /statements, GET/PATCH /statements/{id})
    — adds the proposed transaction batch, plus the file-level metadata
    normalization produced (bank_name/account_hint/model_used/adjusted_at),
    inline so the frontend doesn't need a second call to see what it's
    approving or where it came from. Deliberately not on the list
    serializer above (GET /statements) — embedding this into every row of
    a paginated list is unbounded payload for no benefit, since a list
    screen doesn't need per-row approval detail."""

    transactions = serializers.SerializerMethodField()
    bank_name = serializers.SerializerMethodField()
    account_hint = serializers.SerializerMethodField()
    model_used = serializers.SerializerMethodField()
    adjusted_at = serializers.SerializerMethodField()

    class Meta(StatementFileSerializer.Meta):
        fields = StatementFileSerializer.Meta.fields + [
            "transactions",
            "bank_name",
            "account_hint",
            "model_used",
            "adjusted_at",
        ]

    def get_transactions(self, obj) -> list | None:
        # Only meaningful while awaiting approval — once processed, the
        # ledger is the source of truth and Statements' job is finished
        # (Data_Governance_Specs.md §2: "not queried again for analytics"),
        # so this deliberately doesn't try to keep mirroring ledger state.
        if obj.status != StatementFile.STATUS_PENDING_APPROVAL:
            return None
        payload = obj.normalized_payload
        return payload.get("transactions", []) if payload else None

    def get_bank_name(self, obj) -> str | None:
        payload = obj.normalized_payload
        return payload.get("bank_name") if payload else None

    def get_account_hint(self, obj) -> str | None:
        payload = obj.normalized_payload
        return payload.get("account_hint") if payload else None

    def get_model_used(self, obj) -> str | None:
        # Unlike transactions, this and adjusted_at are historical facts
        # about the normalization run itself, not the mutable pending
        # batch — they stay populated after processed too, not just at
        # pending_approval.
        record = obj.latest_normalized_record
        return record.model_used if record else None

    def get_adjusted_at(self, obj):
        record = obj.latest_normalized_record
        return record.adjusted_at if record else None


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
