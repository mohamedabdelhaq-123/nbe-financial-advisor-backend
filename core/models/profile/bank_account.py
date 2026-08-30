import uuid
from decimal import Decimal

from django.db import models
from django.db.models import Q, Sum

from core.constants import BANK_ACCOUNT_LINK_TYPES


class BankAccount(models.Model):
    LINK_TYPE_MANUAL = "manual"
    LINK_TYPE_SYNCED = "synced"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey("User", on_delete=models.CASCADE, related_name="bank_accounts")
    bank_name = models.CharField(max_length=255)
    account_type = models.CharField(max_length=50, blank=True, null=True)
    account_number = models.CharField(max_length=50)
    currency = models.CharField(max_length=10, default="EGP")
    is_active = models.BooleanField(default=True)
    link_type = models.CharField(
        max_length=20,
        choices=[(t, t) for t in BANK_ACCOUNT_LINK_TYPES],
        default=LINK_TYPE_MANUAL,
    )
    # SET_NULL (not CASCADE): revoking/losing the BankConnection shouldn't
    # delete transaction history — the account just stops syncing and stays
    # read-only via link_type regardless of whether `connection` still exists.
    connection = models.ForeignKey(
        "BankConnection",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="bank_accounts",
    )
    # The provider's own identifier for this account — how an inbound sync
    # webhook (no end-user JWT) is routed to the right row (see
    # core/views/webhooks.py's BankSyncWebhookView).
    external_account_id = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "bank_accounts"
        constraints = [
            # Scoped to `connection` (one per user+provider) rather than
            # provider_slug directly: BankSyncWebhookView's lookup
            # (connection__provider_slug, external_account_id) can only ever
            # match one row once this holds, since a connection pins down
            # both the user and the provider.
            models.UniqueConstraint(
                fields=["connection", "external_account_id"],
                condition=models.Q(connection__isnull=False),
                name="unique_external_account_per_connection",
            )
        ]

    def __str__(self):
        return f"{self.bank_name} - {self.account_number}"

    @property
    def is_synced(self):
        """
        Whether this account is bank-integrated and therefore read-only to
        the end user. A plain predicate, not a raise — same layering as
        Transaction.is_duplicate() (also a plain check): whether to turn
        this into a BusinessRuleError is a view-layer decision (DRF/HTTP
        concerns don't belong on a model), see
        core.views.profile.assert_account_mutable(), the one shared call
        every write path that touches a BankAccount or its transactions
        goes through (BankAccountDetailView, TransactionListCreateView,
        TransactionDetailView).
        """
        return self.link_type == self.LINK_TYPE_SYNCED

    @property
    def current_balance(self):
        """
        Return the live ledger balance for this account.

        A statement transaction can carry the bank's running balance, while
        transactions created later by manual entry or bank sync may only carry
        an amount and direction.  Treat the newest stated balance as an anchor,
        then apply every newer movement so a NULL balance on the latest row
        cannot hide real credits/debits.  Accounts with no stated balance are
        derived from their full ledger starting at zero.
        """
        transactions = self.transactions.all()
        anchor = (
            transactions.filter(balance__isnull=False)
            .order_by("-transaction_date", "-created_at")
            .only("balance", "transaction_date", "created_at")
            .first()
        )

        if anchor is None:
            balance = Decimal("0")
            movements = transactions
        else:
            balance = anchor.balance
            movements = transactions.filter(
                Q(transaction_date__gt=anchor.transaction_date)
                | Q(
                    transaction_date=anchor.transaction_date,
                    created_at__gt=anchor.created_at,
                )
            )

        totals = movements.aggregate(
            credits=Sum("amount", filter=Q(transaction_type="credit")),
            outflows=Sum(
                "amount",
                filter=Q(transaction_type__isnull=True) | ~Q(transaction_type="credit"),
            ),
        )
        return balance + (totals["credits"] or Decimal("0")) - (totals["outflows"] or Decimal("0"))
