import uuid

from django.db import models


class NetWorthSnapshot(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey("User", on_delete=models.CASCADE, related_name="net_worth_snapshots")
    as_of_date = models.DateField()
    total_across_accounts = models.DecimalField(max_digits=14, decimal_places=2)
    per_account_breakdown_json = models.JSONField(blank=True, null=True)

    class Meta:
        db_table = "net_worth_snapshots"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "as_of_date"], name="unique_user_net_worth_date"
            )
        ]
