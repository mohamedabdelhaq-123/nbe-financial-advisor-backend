from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, mixins, status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import BankAccount, ConsentRecord, UserPreference
from core.serializers.profile import (
    BankAccountSerializer,
    ConsentGrantSerializer,
    ConsentRecordSerializer,
    UserPreferenceSerializer,
    UserSerializer,
)


class MeView(APIView):
    """GET/PATCH /users/me, DELETE /users/me (full account + data deletion)."""

    def get(self, request):
        return Response(UserSerializer(request.user).data)

    def patch(self, request):
        serializer = UserSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

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
    """GET/PATCH /users/me/preferences"""

    def _get_preferences(self, request):
        # get_or_create rather than assuming request.user.preferences exists —
        # protects against any user row created outside the normal signup path
        # (e.g. `manage.py createsuperuser`, or a user created before this
        # endpoint existed) still getting a sensible-defaults preferences row
        # on first access instead of a 500 from a missing OneToOne.
        preferences, _ = UserPreference.objects.get_or_create(user=request.user)
        return preferences

    def get(self, request):
        return Response(UserPreferenceSerializer(self._get_preferences(request)).data)

    def patch(self, request):
        serializer = UserPreferenceSerializer(
            self._get_preferences(request), data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class MeConsentView(APIView):
    """POST /users/me/consent — records a grant event."""

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
    DELETE /users/me/consent/{consent_id}

    Consent history is append-only (Data_Governance_Specs.md §1, DB_Schema.md's
    comment on consent_records: "rows are never updated/deleted, only inserted
    (grant or revoke event)"). So despite the HTTP verb, this never deletes or
    mutates the referenced row — it looks it up only to confirm ownership and
    to copy its consent_type/policy_version, then inserts a NEW row recording
    a revoke event, keeping the full grant/revoke timeline reconstructable.
    """

    def delete(self, request, consent_id):
        target = get_object_or_404(ConsentRecord, id=consent_id, user=request.user)
        ConsentRecord.objects.create(
            user=request.user,
            consent_type=target.consent_type,
            policy_version=target.policy_version,
            revoked_at=timezone.now(),
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class BankAccountListCreateView(generics.ListCreateAPIView):
    """
    GET/POST /accounts

    GET returns a plain array, not the offset-paginated {count,next,previous,
    results} envelope — there's no dedicated Data Shapes doc for this domain,
    so this follows the precedent set by the Aggregations domain's small,
    bounded per-user lists (monthly-summaries, recurring-charges, anomalies —
    all explicitly "Pagination: none") rather than admin/products' precedent
    (paginated despite being small, because that one is a cross-user catalog).
    A user's own linked bank accounts is the same shape of collection as
    those: small and bounded per user, not cross-user or unboundedly growing.
    """

    serializer_class = BankAccountSerializer

    def get_queryset(self):
        return BankAccount.objects.filter(user=self.request.user).order_by("-created_at")

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class BankAccountDetailView(
    mixins.UpdateModelMixin, mixins.DestroyModelMixin, generics.GenericAPIView
):
    """
    PATCH/DELETE /accounts/{account_id}

    Deliberately not RetrieveUpdateDestroyAPIView — API_Endpoints_1.md §3 only
    documents PATCH and DELETE on this path (no singular GET), so no GET
    method is exposed here to keep the route surface matching the doc exactly.

    DELETE is a hard delete, which cascades to every transaction on this
    account (DB_Schema.md: `transactions.account_id ... ON DELETE CASCADE`) —
    a deliberate consequence of the documented schema, not an oversight here.
    """

    serializer_class = BankAccountSerializer
    lookup_url_kwarg = "account_id"

    def get_queryset(self):
        # Filtering by owner here (rather than a separate permission check)
        # means an unowned account_id 404s instead of 403ing, per API Design
        # Guidelines §10's existence-leak avoidance rule.
        return BankAccount.objects.filter(user=self.request.user)

    def patch(self, request, *args, **kwargs):
        return self.partial_update(request, *args, **kwargs)

    def delete(self, request, *args, **kwargs):
        return self.destroy(request, *args, **kwargs)
