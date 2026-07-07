import uuid

from django.db import models


class BankAccount(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey("User", on_delete=models.CASCADE, related_name="bank_accounts")
    bank_name = models.CharField(max_length=255)
    account_type = models.CharField(max_length=50, blank=True, null=True)
    masked_account_number = models.CharField(max_length=50)
    currency = models.CharField(max_length=10, default="EGP")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "bank_accounts"

    def __str__(self):
        return f"{self.bank_name} - {self.masked_account_number}"

    @property
    def current_balance(self):
        """
        Grabs the latest transaction balance or falls back to 0.00.
        Leverages the single source of truth ledger in Aggregations.
        """
        # Kept dynamic to reflect real-time ledger updates
        latest_transaction = (
            self.transactions.order_by("-transaction_date", "-created_at").only("balance").first()
        )
        return latest_transaction.balance if latest_transaction else 0.00
