from django.shortcuts import get_object_or_404
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import generics, mixins, status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.filters.profile import BankAccountFilterSet
from core.models import BankAccount, ConsentRecord, UserPreference
from core.openapi import error_responses
from core.serializers.profile import (
    BankAccountSerializer,
    ConsentGrantSerializer,
    ConsentRecordSerializer,
    UserPreferenceSerializer,
    UserSerializer,
)


class MeView(APIView):
    """GET/PATCH the current user's own profile, or DELETE the account
    entirely. DELETE cascades to every one of the user's rows across the
    whole schema (accounts, transactions, budgets, conversations,
    statements, ...) since every domain table's foreign key back to the
    user is ON DELETE CASCADE — this is one call, not a per-domain cleanup
    the frontend needs to orchestrate."""

    @extend_schema(responses={200: UserSerializer})
    def get(self, request):
        return Response(UserSerializer(request.user).data)

    @extend_schema(
        request=UserSerializer,
        responses={200: UserSerializer, **error_responses(422)},
    )
    def patch(self, request):
        serializer = UserSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    @extend_schema(responses={204: None})
    def delete(self, request):
        # Every domain FK to `users` is ON DELETE CASCADE per DB_Schema.md, so
        # this single call removes the user's entire footprint (accounts,
        # transactions, budgets, conversations, statements, etc.) at the DB
        # level. Raw file cleanup in SeaweedFS (File_System_Structure.md §6's
        # "{user_id}/ prefix" deletion) is deferred until the Statements
        # checkpoint wires up services/file_storage.py — there's no file
        # storage integration to call yet.
        request.user.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MePreferencesView(APIView):
    """GET/PATCH the current user's notification/display preferences. A
    preferences row is created lazily with sensible defaults on first
    access if one doesn't already exist (e.g. for a user created before
    this endpoint existed, or via `manage.py createsuperuser`), so GET
    never 404s for a signed-in user."""

    def _get_preferences(self, request):
        # get_or_create rather than assuming request.user.preferences exists —
        # protects against any user row created outside the normal signup path
        # (e.g. `manage.py createsuperuser`, or a user created before this
        # endpoint existed) still getting a sensible-defaults preferences row
        # on first access instead of a 500 from a missing OneToOne.
        preferences, _ = UserPreference.objects.get_or_create(user=request.user)
        return preferences

    @extend_schema(responses={200: UserPreferenceSerializer})
    def get(self, request):
        return Response(UserPreferenceSerializer(self._get_preferences(request)).data)

    @extend_schema(
        request=UserPreferenceSerializer,
        responses={200: UserPreferenceSerializer, **error_responses(422)},
    )
    def patch(self, request):
        serializer = UserPreferenceSerializer(
            self._get_preferences(request), data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class MeConsentView(APIView):
    """Record that the user granted consent (e.g. to a specific policy
    version) — appends a new consent-record row rather than updating any
    existing one, since the full grant/revoke history is kept, not just
    the latest state."""

    @extend_schema(
        request=ConsentGrantSerializer,
        responses={201: ConsentRecordSerializer, **error_responses(422)},
    )
    def post(self, request):
        serializer = ConsentGrantSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        record = ConsentRecord.objects.create(
            user=request.user,
            granted_at=timezone.now(),
            **serializer.validated_data,
        )
        return Response(ConsentRecordSerializer(record).data, status=status.HTTP_201_CREATED)


class MeConsentRevokeView(APIView):
    """
    Revoke a previously granted consent. Despite the DELETE verb, this never
    deletes or mutates the referenced consent record — consent history is
    append-only, so every grant/revoke is a separate row and the full
    timeline stays reconstructable. This endpoint looks up the target
    record only to confirm it belongs to the current user and to copy its
    `consent_type`/`policy_version`, then inserts a brand new row recording
    a revoke event against those same values.
    """

    @extend_schema(responses={204: None, **error_responses(404)})
    def delete(self, request, consent_id):
        target = get_object_or_404(ConsentRecord, id=consent_id, user=request.user)
        ConsentRecord.objects.create(
            user=request.user,
            consent_type=target.consent_type,
            policy_version=target.policy_version,
            revoked_at=timezone.now(),
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema_view(
    post=extend_schema(responses={201: BankAccountSerializer, **error_responses(422)})
)
class BankAccountListCreateView(generics.ListCreateAPIView):
    """
    List the current user's linked bank accounts, or link a new one.

    GET returns a plain array, not the offset-paginated
    `{count,next,previous,results}` envelope used elsewhere in the API —
    a single user's own linked accounts is a small, bounded, per-user
    collection (unlike a cross-user catalog such as `GET /admin/products`,
    which is paginated despite also being small), so pagination would add
    overhead without solving any real problem here.

    `masked_account_number`/`bank_name` query params let the frontend check
    whether the user already has an account matching an OCR-derived mask
    before creating a duplicate from a newly uploaded statement.
    """

    serializer_class = BankAccountSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = BankAccountFilterSet

    def get_queryset(self):
        # swagger_fake_view: see core/views/aggregations.py's
        # TransactionListCreateView.get_queryset().
        if getattr(self, "swagger_fake_view", False):
            return BankAccount.objects.none()
        # masked_account_number/bank_name (BankAccountFilterSet) let the
        # frontend check "does the user already have an account matching
        # this OCR-derived mask?" (PLAN.md Checkpoint A) before/without
        # creating a duplicate — exact match, same masking strategy
        # core/tasks/statements.py's run_normalization_phase() already uses
        # to resolve/create accounts.
        return BankAccount.objects.filter(user=self.request.user).order_by("-created_at")

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class BankAccountDetailView(
    mixins.UpdateModelMixin, mixins.DestroyModelMixin, generics.GenericAPIView
):
    """
    Update or unlink one of the current user's bank accounts. There's
    deliberately no GET on this path (only the list view, `GET /accounts`,
    returns a single account's data) — fetch the account from the list
    response rather than expecting a singular retrieve here.

    DELETE is a hard delete that cascades to every transaction recorded
    against this account — removing an account also permanently removes its
    transaction history, not just the account row itself.
    """

    serializer_class = BankAccountSerializer
    lookup_url_kwarg = "account_id"

    def get_queryset(self):
        # Filtering by owner here (rather than a separate permission check)
        # means an unowned account_id 404s instead of 403ing, per API Design
        # Guidelines §10's existence-leak avoidance rule.
        return BankAccount.objects.filter(user=self.request.user)

    @extend_schema(responses={200: BankAccountSerializer, **error_responses(404, 422)})
    def patch(self, request, *args, **kwargs):
        return self.partial_update(request, *args, **kwargs)

    @extend_schema(responses={204: None, **error_responses(404)})
    def delete(self, request, *args, **kwargs):
        return self.destroy(request, *args, **kwargs)
