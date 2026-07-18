"""
Machine-to-machine endpoints — no end-user JWT, authenticated via the
shared-secret classes in core/authentication.py instead. Both use AllowAny
as their DRF permission_classes because the *authentication* step already
does the gatekeeping (a missing/invalid shared secret raises 401 there);
AllowAny just tells DRF not to additionally require request.user to be
authenticated, since these callers have no User at all (see
_SharedSecretAuthentication's docstring).
"""

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.authentication import BankSyncServiceAuthentication, MockBankServiceAuthentication
from core.exceptions import NotificationServiceUnavailable
from core.models import BankAccount, BankConnection
from core.openapi import error_responses
from core.serializers.bank_connections import BankSyncWebhookSerializer, InternalEmailSerializer
from core.serializers.errors import ErrorResponseSerializer
from core.tasks.bank_sync import ingest_synced_transactions
from services import notification_service


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

        account = get_object_or_404(
            BankAccount,
            connection__provider_slug=data["provider_slug"],
            connection__status=BankConnection.STATUS_LINKED,
            external_account_id=data["external_account_id"],
            link_type=BankAccount.LINK_TYPE_SYNCED,
        )
        ingest_synced_transactions.delay(str(account.id), data["transactions"])
        return Response(status=status.HTTP_202_ACCEPTED)


class InternalNotificationEmailView(APIView):
    """
    POST /internal/notifications/email/ — called only by mock-bank-oauth to
    send its OTP emails through the one real notification client
    (services/notification_service.py) rather than reimplementing SMTP.
    """

    authentication_classes = [MockBankServiceAuthentication]
    permission_classes = [AllowAny]

    @extend_schema(
        request=InternalEmailSerializer,
        responses={
            202: None,
            **error_responses(401, 422),
            502: OpenApiResponse(
                response=ErrorResponseSerializer,
                description="The notification service failed or is unreachable.",
            ),
        },
    )
    def post(self, request):
        serializer = InternalEmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            notification_service.send_email(data["to"], data["subject"], data["body"])
        except notification_service.NotificationServiceError as exc:
            raise NotificationServiceUnavailable(str(exc)) from exc
        return Response(status=status.HTTP_202_ACCEPTED)
