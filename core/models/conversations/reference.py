import uuid

from django.apps import apps
from django.db import models


class MessageReference(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    message = models.ForeignKey(
        "Message",
        on_delete=models.CASCADE,
        related_name="references",
    )
    target_type = models.CharField(max_length=50)  # e.g., 'statement', 'budget'
    target_id = models.UUIDField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "message_references"
        indexes = [
            models.Index(
                fields=["target_type", "target_id"],
                name="idx_message_references_target",
            )
        ]

    def __str__(self):
        return f"Ref to {self.target_type} ({self.target_id})"

    def resolve_target(self):
        """
        Dynamically resolves and fetches the referenced model instance
        across the project structure without requiring strict hardcoded foreign keys.
        """
        mapping = {
            "statement": ("statements", "StatementFile"),
            "transaction": ("aggregations", "Transaction"),
            "budget": ("budgets", "Budget"),
            "issue": ("feedback", "ReportedIssue"),
        }

        if self.target_type not in mapping:
            return None

        app_label, model_name = mapping[self.target_type]
        try:
            model_class = apps.get_model(app_label, model_name)
            return model_class.objects.filter(id=self.target_id).first()
        except LookupError:
            return None
