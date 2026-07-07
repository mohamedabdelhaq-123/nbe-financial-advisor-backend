import uuid

from django.db import models


class StatementOcrResult(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    statement = models.ForeignKey(
        "StatementFile",
        on_delete=models.CASCADE,
        related_name="ocr_results",
    )
    seaweed_file_id = models.CharField(max_length=255)
    ocr_engine = models.CharField(max_length=50, default="MinerU")
    confidence_score = models.DecimalField(max_digits=4, decimal_places=3, blank=True, null=True)
    processed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "statement_ocr_results"
