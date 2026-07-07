import uuid

from django.db import models


class ReportedIssue(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey("User", on_delete=models.CASCADE, related_name="reported_issues")
    description = models.TextField()
    status = models.CharField(max_length=20, default="open")  # open / resolved
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "reported_issues"

    def __str__(self):
        return f"Issue {self.id[:8]} - Status: {self.status}"
