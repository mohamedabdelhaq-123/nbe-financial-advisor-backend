import uuid

from django.db import models


class BankConnection(models.Model):
    """
    One row per (user, provider) bank link — never a single record per user —
    so a user can hold several simultaneous bank connections, each its own
    independent OAuth+OTP round trip. BankAccount.connection points back to
    whichever BankConnection produced it (see core/models/profile/bank_account.py).

    provider_slug is the key into services/bank_connectors's registry
    (get_connector(provider_slug)) — the one place any call site needs to
    know which bank/adapter it's talking to.
    """

    STATUS_PENDING_OTP = "pending_otp"
    STATUS_LINKED = "linked"
    STATUS_REVOKED = "revoked"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_PENDING_OTP, STATUS_PENDING_OTP),
        (STATUS_LINKED, STATUS_LINKED),
        (STATUS_REVOKED, STATUS_REVOKED),
        (STATUS_FAILED, STATUS_FAILED),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey("User", on_delete=models.CASCADE, related_name="bank_connections")
    provider_slug = models.CharField(max_length=50)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING_OTP)
    external_customer_id = models.CharField(max_length=255, blank=True, null=True)
    # Opaque to Django — access_token is whatever the connector's provider
    # issued (for mock_bank, a signed JWT mock-bank-sync verifies on its own;
    # never decoded here, just stored and forwarded as a Bearer token). Plain
    # TextField for this first pass — flagged as needing encryption-at-rest
    # before this ever points at a real bank's token.
    access_token = models.TextField(blank=True, null=True)
    refresh_token = models.TextField(blank=True, null=True)
    token_expires_at = models.DateTimeField(blank=True, null=True)
    # CSRF-style value threaded through the authorize -> callback round trip
    # (BankConnectionInitiateView sets it, BankConnectionCallbackView checks
    # it against the caller-supplied `state` and clears it once redeemed).
    oauth_state = models.CharField(max_length=255, blank=True, null=True)
    error_reason = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    linked_at = models.DateTimeField(blank=True, null=True)
    revoked_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "bank_connections"
        indexes = [
            models.Index(fields=["user", "provider_slug"], name="idx_bank_conn_user_provider"),
        ]

    def __str__(self):
        return f"{self.provider_slug} - {self.user_id} ({self.status})"
