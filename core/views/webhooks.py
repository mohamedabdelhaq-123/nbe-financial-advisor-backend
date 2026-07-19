"""
Machine-to-machine endpoint — no end-user JWT, authenticated via the
shared-secret class in core/authentication.py instead. Uses AllowAny as its
DRF permission_classes because the *authentication* step already does the
gatekeeping (a missing/invalid shared secret raises 401 there); AllowAny
just tells DRF not to additionally require request.user to be
authenticated, since this caller has no User at all (see
_SharedSecretAuthentication's docstring).
"""

from django.http import Http404
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.authentication import BankSyncServiceAuthentication
from core.models import BankAccount, BankConnection
from core.openapi import error_responses
from core.serializers.bank_connections import BankSyncWebhookSerializer
from core.tasks.bank_sync import ingest_synced_transactions
from services.bank_connectors import BankConnectorError, get_connector
from services.bank_connectors.sync import apply_synced_accounts


class BankSyncWebhookView(APIView):
    """
    POST /webhooks/bank-sync/ — inbound push from mock-bank-sync (later: a
    real bank's own sync feed). Identity is derived entirely from the
    payload's (provider_slug, external_account_id) pair, resolved against an
    existing synced BankAccount — never trusted from a client-supplied user
    id (see BankSyncServiceAuthentication's docstring).
    """

    authentication_classes = [BankSyncServiceAuthentication]
    permission_classes = [AllowAny]

    @extend_schema(
        request=BankSyncWebhookSerializer,
        responses={202: None, **error_responses(401, 404, 422)},
    )
    def post(self, request):
        serializer = BankSyncWebhookSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        account = BankAccount.objects.filter(
            connection__provider_slug=data["provider_slug"],
            connection__status=BankConnection.STATUS_LINKED,
            external_account_id=data["external_account_id"],
            link_type=BankAccount.LINK_TYPE_SYNCED,
        ).first()

        if account is None:
            # Not yet known — most likely a new account opened at an
            # already-linked bank since the last fetch_accounts() pull.
            # Re-pull the connection's account list and land it via the
            # same shared step every other sync path uses, rather than
            # 404ing on real, legitimate data.
            connection = get_object_or_404(
                BankConnection,
                provider_slug=data["provider_slug"],
                external_customer_id=data["external_customer_id"],
                status=BankConnection.STATUS_LINKED,
            )
            connector = get_connector(connection.provider_slug)
            try:
                accounts = connector.fetch_accounts(connection.access_token)
            except BankConnectorError as exc:
                raise Http404(str(exc))
            apply_synced_accounts(connection, accounts, connector)
            account = BankAccount.objects.filter(
                connection=connection, external_account_id=data["external_account_id"]
            ).first()
            if account is None:
                # The bank itself doesn't know this account either — a
                # genuinely unknown id, not just one we hadn't seen yet.
                raise Http404("Unknown external_account_id.")

        ingest_synced_transactions.delay(str(account.id), data["transactions"])
        return Response(status=status.HTTP_202_ACCEPTED)
