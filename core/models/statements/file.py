import uuid

from django.db import models


class StatementFile(models.Model):
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
    status = models.CharField(max_length=20, default="pending")
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
        return self.status == "processed"

    @property
    def latest_ocr_run(self):
        """Quick shortcut to grab the latest raw engine extraction metrics."""
        return self.ocr_results.order_by("-processed_at").first()

    @property
    def normalized_payload(self):
        """Fetches the latest structured JSON block without hitting intermediate tables manually."""
        record = self.normalized_records.order_by("-adjusted_at").first()
        return record.normalized_json if record else None
