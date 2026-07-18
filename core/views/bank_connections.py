import secrets

from django.conf import settings
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.exceptions import BusinessRuleError
from core.models import BankAccount, BankConnection
from core.openapi import error_responses
from core.serializers.bank_connections import (
    BankConnectionCallbackSerializer,
    BankConnectionInitiateResponseSerializer,
    BankConnectionInitiateSerializer,
    BankConnectionSerializer,
)
from core.serializers.profile import BankAccountSerializer
from core.tasks.bank_sync import ingest_synced_transactions
from services.bank_connectors import BankConnectorError, get_connector


class BankConnectionListCreateView(generics.ListAPIView):
    """
    List the current user's bank connections (linked, pending, or revoked),
    or start linking a new one — same ListCreateAPIView convention as
    BankAccountListCreateView, and the same small-bounded-per-user-collection
    reasoning for skipping pagination.
    """

    serializer_class = BankConnectionSerializer
    pagination_class = None

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return BankConnection.objects.none()
        return BankConnection.objects.filter(user=self.request.user).order_by("-created_at")

    @extend_schema(
        request=BankConnectionInitiateSerializer,
        responses={201: BankConnectionInitiateResponseSerializer, **error_responses(404, 422)},
    )
    def post(self, request, *args, **kwargs):
        serializer = BankConnectionInitiateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        provider_slug = serializer.validated_data["provider_slug"]

        try:
            connector = get_connector(provider_slug)
        except BankConnectorError:
            # Unknown provider — existence-leak-avoidance style (API Design
            # Guidelines §10), same reasoning as an unowned resource id 404ing
            # rather than 403ing.
            raise Http404("Unknown bank provider.")

        # update_or_create rather than a plain create: relinking after a
        # revoke (or retrying a stalled pending_otp) reuses the same row
        # instead of accumulating dead ones. No DB-level uniqueness
        # constraint backs this, so two concurrent initiate calls for the
        # same (user, provider_slug) can still race — acceptable for a
        # single-user-driven linking flow, not safe to assume under
        # concurrent/automated callers.
        connection, _ = BankConnection.objects.update_or_create(
            user=request.user,
            provider_slug=provider_slug,
            defaults={
                "status": BankConnection.STATUS_PENDING_OTP,
                "oauth_state": secrets.token_urlsafe(32),
                "error_reason": None,
            },
        )
        authorize_url = connector.get_authorize_url(
            state=connection.oauth_state,
            redirect_uri=settings.MOCK_BANK_OAUTH_REDIRECT_URI,
        )
        return Response(
            {"connection_id": str(connection.id), "authorize_url": authorize_url},
            status=status.HTTP_201_CREATED,
        )


class BankConnectionCallbackView(APIView):
    """
    POST /bank-connections/{id}/callback/ — called by the frontend once the
    provider's OAuth redirect has landed back on it with ?code&state. The
    frontend calls this endpoint itself (not the provider redirecting
    straight to the backend), authenticated with the user's normal JWT —
    standard SPA-OAuth shape. Exchanges the code for a token, marks the
    connection linked, pulls the linked accounts, and kicks off an initial
    transaction backfill so the account isn't empty until the first sync push.
    """

    @extend_schema(
        request=BankConnectionCallbackSerializer,
        responses={200: BankAccountSerializer(many=True), **error_responses(404, 422)},
    )
    def post(self, request, connection_id):
        connection = get_object_or_404(BankConnection, id=connection_id, user=request.user)
        serializer = BankConnectionCallbackSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        code = serializer.validated_data["code"]
        state = serializer.validated_data["state"]

        if not connection.oauth_state or state != connection.oauth_state:
            raise BusinessRuleError("OAuth state mismatch.", code="invalid_oauth_state")

        connector = get_connector(connection.provider_slug)

        def _mark_failed(exc: BankConnectorError) -> None:
            connection.status = BankConnection.STATUS_FAILED
            connection.error_reason = str(exc)
            connection.oauth_state = None
            connection.save(update_fields=["status", "error_reason", "oauth_state"])

        try:
            token = connector.exchange_code_for_token(code)
        except BankConnectorError as exc:
            _mark_failed(exc)
            raise BusinessRuleError(
                "Failed to complete the bank connection.", code="bank_connection_failed"
            ) from exc

        access_token = token["access_token"]
        try:
            accounts = connector.fetch_accounts(access_token)
        except BankConnectorError as exc:
            # Don't persist the token/STATUS_LINKED at all if we can't even
            # fetch the account list — a connection stuck "linked" with zero
            # accounts, from a transient mock-bank-sync outage, is worse
            # than surfacing the failure and letting the user retry linking.
            _mark_failed(exc)
            raise BusinessRuleError(
                "Failed to complete the bank connection.", code="bank_connection_failed"
            ) from exc

        created_accounts = []
        with transaction.atomic():
            connection.access_token = access_token
            connection.refresh_token = token.get("refresh_token")
            connection.external_customer_id = token.get("external_customer_id")
            connection.status = BankConnection.STATUS_LINKED
            connection.linked_at = timezone.now()
            connection.oauth_state = None
            connection.save(
                update_fields=[
                    "access_token",
                    "refresh_token",
                    "external_customer_id",
                    "status",
                    "linked_at",
                    "oauth_state",
                ]
            )

            for acct in accounts:
                bank_account, _ = BankAccount.objects.update_or_create(
                    connection=connection,
                    external_account_id=acct["external_account_id"],
                    defaults={
                        "user": request.user,
                        "link_type": BankAccount.LINK_TYPE_SYNCED,
                        "bank_name": acct["bank_name"],
                        "account_type": acct.get("account_type"),
                        "masked_account_number": acct["masked_account_number"],
                        "currency": acct.get("currency", "EGP"),
                    },
                )
                created_accounts.append(bank_account)

        for bank_account, acct in zip(created_accounts, accounts):
            # Best-effort: the account is already linked and correctly
            # persisted above regardless of whether this initial backfill
            # succeeds — ongoing sync pushes (BankSyncWebhookView) will
            # populate it either way.
            try:
                transactions = connector.fetch_transactions(
                    access_token, acct["external_account_id"]
                )
            except BankConnectorError:
                continue
            if transactions:
                ingest_synced_transactions.delay(str(bank_account.id), transactions)

        return Response(BankAccountSerializer(created_accounts, many=True).data)
