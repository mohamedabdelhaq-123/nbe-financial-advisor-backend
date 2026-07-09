import uuid

from django.db import models


class StatementFile(models.Model):
    # A row only ever exists once its file is stored (see
    # core/views/statements.py::create_statement_from_upload) — there is no
    # "record_created"/"stored" status here, a storage failure never
    # persists a row at all. Status names the phase the statement is
    # currently at/working toward — no "pending_" prefix, since
    # `is_processing` below already says whether that phase is actively
    # running; baking "pending" into the name too would just be saying the
    # same thing twice. `failure_reason`/`failed_phase` carry retry context
    # for whichever phase hasn't advanced yet, instead of a separate
    # `failed` status per phase.
    STATUS_EXTRACTION = "extraction"
    STATUS_NORMALIZATION = "normalization"
    STATUS_APPROVAL = "approval"
    STATUS_PROCESSED = "processed"
    STATUS_CHOICES = [
        (STATUS_EXTRACTION, "Extraction"),
        (STATUS_NORMALIZATION, "Normalization"),
        (STATUS_APPROVAL, "Approval"),
        (STATUS_PROCESSED, "Processed"),
    ]

    PHASE_EXTRACTION = "extraction"
    PHASE_NORMALIZATION = "normalization"
    FAILED_PHASE_CHOICES = [
        (PHASE_EXTRACTION, "Extraction"),
        (PHASE_NORMALIZATION, "Normalization"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey("User", on_delete=models.CASCADE, related_name="statement_files")
    account = models.ForeignKey(
        "BankAccount",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="statement_files",
    )
    template = models.ForeignKey(
        "BankStatementTemplate",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="statement_files",
    )
    seaweed_file_id = models.CharField(max_length=255)
    checksum = models.CharField(max_length=64)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default=STATUS_EXTRACTION)
    failure_reason = models.TextField(blank=True, null=True)
    failed_phase = models.CharField(
        max_length=20, choices=FAILED_PHASE_CHOICES, blank=True, null=True
    )
    # True only while a phase runner is actively executing (set/cleared by
    # _run_extraction/_run_normalization in core/views/statements.py) —
    # without this, "status=extraction, failure_reason=null" is ambiguous
    # between "never attempted yet" and "a background worker is running
    # this right now" once the pipeline stops being fully synchronous.
    # Always false in any response today (nothing runs across requests
    # yet), but also doubles as a guard against two overlapping PATCH
    # retries firing on the same statement.
    is_processing = models.BooleanField(default=False)
    start_transaction_date = models.DateField(blank=True, null=True)
    last_transaction_date = models.DateField(blank=True, null=True)
    upload_date = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "statement_files"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "checksum"], name="unique_user_statement_checksum"
            )
        ]
        indexes = [models.Index(fields=["user", "status"], name="idx_statement_user_status")]

    def __str__(self):
        return f"Statement {self.id} ({self.status})"

    @property
    def is_fully_processed(self):
        """Returns True if the document successfully crossed the extraction finish line."""
        return self.status == self.STATUS_PROCESSED

    @property
    def latest_ocr_run(self):
        """Quick shortcut to grab the latest raw engine extraction metrics."""
        return self.ocr_results.order_by("-processed_at").first()

    @property
    def latest_normalized_record(self):
        """The latest StatementNormalized row itself (not just its JSON payload) — lets
        callers also reach model_used/adjusted_at, not only the data normalized_payload exposes."""
        return self.normalized_records.order_by("-adjusted_at").first()

    @property
    def normalized_payload(self):
        """Fetches the latest structured JSON block without hitting intermediate tables manually."""
        record = self.latest_normalized_record
        return record.normalized_json if record else None
