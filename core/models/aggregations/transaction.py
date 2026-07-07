import uuid

from django.db import models
from pgvector.django import HnswIndex, VectorField


class Transaction(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey("User", on_delete=models.CASCADE, related_name="transactions")
    account = models.ForeignKey(
        "BankAccount", on_delete=models.CASCADE, related_name="transactions"
    )
    statement = models.ForeignKey(
        "StatementFile",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="transactions",
    )
    transaction_date = models.DateField()
    merchant_raw = models.CharField(max_length=500, blank=True, null=True)
    merchant_normalized = models.CharField(max_length=255, blank=True, null=True)
    category = models.CharField(max_length=100, blank=True, null=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=10, default="EGP")
    is_recurring = models.BooleanField(default=False)
    confidence_score = models.DecimalField(max_digits=4, decimal_places=3, blank=True, null=True)
    source = models.CharField(max_length=20, default="statement")  # statement / manual
    balance = models.DecimalField(max_digits=14, decimal_places=2, blank=True, null=True)
    transaction_type = models.CharField(max_length=20, blank=True, null=True)
    extra_fields = models.JSONField(blank=True, null=True)
    embedding = VectorField(dimensions=1536, blank=True, null=True)  # RAG target
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "transactions"
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "user",
                    "account",
                    "transaction_date",
                    "amount",
                    "merchant_raw",
                ],
                name="unique_ledger_transaction_match",
            )
        ]
        indexes = [
            models.Index(
                fields=["user", "transaction_date"],
                name="idx_transactions_user_date",
            ),
            models.Index(fields=["user", "category"], name="idx_transactions_user_category"),
            models.Index(fields=["account"], name="idx_transactions_account"),
            HnswIndex(
                name="idx_transactions_embedding",
                fields=["embedding"],
                opclasses=["vector_cosine_ops"],
            ),
        ]

    def __str__(self):
        merchant = self.merchant_normalized or self.merchant_raw
        return f"{self.transaction_date} - {merchant}: {self.amount}"

    @classmethod
    def is_duplicate(cls, user_id, account_id, date, amount, merchant_raw):
        """
        Enforces transaction-level duplicate checking across the engine.
        Can be quickly called by ingestion tasks or dashboard entry views.
        """
        return cls.objects.filter(
            user_id=user_id,
            account_id=account_id,
            transaction_date=date,
            amount=amount,
            merchant_raw=merchant_raw,
        ).exists()
