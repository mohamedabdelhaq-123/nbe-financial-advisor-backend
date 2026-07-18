from rest_framework import serializers

from core.models import BankConnection


class BankConnectionSerializer(serializers.ModelSerializer):
    """Read-only shape — same convention as RecurringChargeSerializer/
    ConsentRecordSerializer: nothing here is ever client-writable directly,
    only through the initiate/callback flow below."""

    class Meta:
        model = BankConnection
        fields = ["id", "provider_slug", "status", "linked_at", "revoked_at", "created_at"]
        read_only_fields = fields


class BankConnectionInitiateSerializer(serializers.Serializer):
    """POST /bank-connections/ request body."""

    provider_slug = serializers.CharField(max_length=50)


class BankConnectionInitiateResponseSerializer(serializers.Serializer):
    """POST /bank-connections/ response body — the frontend redirects the
    user's browser to authorize_url to continue the OAuth+OTP flow."""

    connection_id = serializers.UUIDField()
    authorize_url = serializers.URLField()


class BankConnectionCallbackSerializer(serializers.Serializer):
    """POST /bank-connections/{id}/callback/ request body — the `code`/
    `state` the frontend read off the OAuth redirect."""

    code = serializers.CharField()
    state = serializers.CharField()


class BankSyncTransactionSerializer(serializers.Serializer):
    """One entry in BankSyncWebhookSerializer's `transactions` list — the
    same field names services/bank_connectors/mock_bank.py's
    fetch_transactions() returns, since both land in the same
    ingest_synced_transactions task (core/tasks/bank_sync.py)."""

    external_transaction_id = serializers.CharField(required=False, allow_null=True)
    transaction_date = serializers.DateField()
    merchant_raw = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    transaction_type = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    currency = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    balance = serializers.DecimalField(
        max_digits=14, decimal_places=2, required=False, allow_null=True
    )


class BankSyncWebhookSerializer(serializers.Serializer):
    """POST /webhooks/bank-sync/ request body — pushed by mock-bank-sync
    (later: a real bank's own sync feed). Identity is derived entirely from
    (provider_slug, external_account_id), matched against an existing synced
    BankAccount — never from a client-supplied user id (see
    BankSyncServiceAuthentication's docstring). external_customer_id backs a
    fallback lookup by (provider_slug, external_customer_id) when the
    account isn't found — a brand-new account opened at an already-linked
    bank, not yet discovered via a fetch_accounts() pull."""

    provider_slug = serializers.CharField(max_length=50)
    external_account_id = serializers.CharField(max_length=255)
    external_customer_id = serializers.CharField(max_length=255)
    transactions = BankSyncTransactionSerializer(many=True)


class InternalEmailSerializer(serializers.Serializer):
    """POST /internal/notifications/email/ request body — called only by
    mock-bank-oauth to deliver its OTP emails through the one real
    notification client (services/notification_service.py)."""

    to = serializers.EmailField()
    subject = serializers.CharField(max_length=255)
    body = serializers.CharField()
