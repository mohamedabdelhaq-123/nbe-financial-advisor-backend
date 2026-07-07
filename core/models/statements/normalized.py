import uuid

from django.db import models


class StatementNormalized(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    statement = models.ForeignKey(
        "StatementFile",
        on_delete=models.CASCADE,
        related_name="normalized_records",
    )
    normalized_json = models.JSONField()  # Structured JSON matching ledger intent
    model_used = models.CharField(max_length=100, blank=True, null=True)
    adjusted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "statement_normalized"
