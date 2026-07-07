import uuid

from django.db import models


class BankStatementTemplate(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    bank_name = models.CharField(max_length=255)
    layout_signature = models.CharField(max_length=255)
    column_mapping_json = models.JSONField()  # Native JSONB
    date_format = models.CharField(max_length=20, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "bank_statement_templates"
        constraints = [
            models.UniqueConstraint(
                fields=["bank_name", "layout_signature"],
                name="unique_bank_layout_signature",
            )
        ]

    def __str__(self):
        return f"Template: {self.bank_name} ({self.layout_signature[:8]})"
