from django.shortcuts import get_object_or_404
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import generics, mixins, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from core.exceptions import BusinessRuleError
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
from core.tasks.data_export import send_account_data_export


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
    """GET the user's full consent grant/revoke history (newest first — see
    MeConsentRevokeView's docstring for why this is a flat append-only log
    rather than one row per consent_type), or POST to record that the user
    granted consent (e.g. to a specific policy version) — appends a new
    consent-record row rather than updating any existing one."""

    @extend_schema(responses={200: ConsentRecordSerializer(many=True)})
    def get(self, request):
        records = ConsentRecord.objects.filter(user=request.user).order_by("-created_at")
        return Response(ConsentRecordSerializer(records, many=True).data)

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


class MeDataExportView(APIView):
    """
    "Request my account data" (profile page's Account Management section).
    Kicks off an async export of everything the product stores about the
    current user — profile, budget/allocations, goal, bank accounts,
    transactions, consent history, reported issues, feedback
    (core.tasks.data_export.send_account_data_export) — and emails it as a
    JSON attachment. Never generated inline: walking a user's full
    transaction history isn't bounded the way most request/response work
    here is, so this endpoint's job is done the moment the job is enqueued,
    not when the export actually finishes.

    Requires a verified email UNLESS the account has no usable password
    (a bank-login account, whose identity was already proven by bank OTP —
    same has_password check UserSerializer/VerifyEmailBanner use). Every
    other account could have signed up with an email typo or someone
    else's address; mailing a full financial data dump to an unconfirmed
    inbox would hand it to whoever actually controls that address, not
    necessarily this user.
    """

    # Same scope/rate as the other email-sending auth endpoints — cheap
    # enough to reuse rather than defining a dedicated scope for one view.
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

    @extend_schema(request=None, responses={202: None, **error_responses(401, 403)})
    def post(self, request):
        if not request.user.email_verified and request.user.has_usable_password():
            raise PermissionDenied(
                "Verify your email before requesting a data export — this "
                "makes sure it's sent to an address you actually control."
            )
        send_account_data_export.delay(str(request.user.id))
        return Response(status=status.HTTP_202_ACCEPTED)


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

    `account_number`/`bank_name` query params let the frontend check whether
    the user already has an account matching an OCR-derived account number
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
        # account_number/bank_name (BankAccountFilterSet) let the frontend
        # check "does the user already have an account matching this
        # OCR-derived account number?" (PLAN.md Checkpoint A) before/without
        # creating a duplicate — an exact match works across every creation
        # path (manual entry, statement normalization, and bank sync) because
        # all three store the real number, and the serializer hands it back
        # unmasked, so a value read off a GET /accounts response round-trips
        # as a filter unchanged.
        return BankAccount.objects.filter(user=self.request.user).order_by("-created_at")

    def perform_create(self, serializer):
        assert_bank_not_already_synced(self.request.user, serializer.validated_data["bank_name"])
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
        assert_account_mutable(self.get_object())
        new_bank_name = request.data.get("bank_name")
        if new_bank_name:
            assert_bank_not_already_synced(request.user, new_bank_name)
        return self.partial_update(request, *args, **kwargs)

    @extend_schema(responses={204: None, **error_responses(404, 422)})
    def delete(self, request, *args, **kwargs):
        assert_account_mutable(self.get_object())
        return self.destroy(request, *args, **kwargs)


def assert_account_mutable(account: BankAccount) -> None:
    """
    Raise if `account` is bank-integrated and therefore read-only to the end
    user. The one shared call every write path that touches a BankAccount or
    its transactions goes through — BankAccountDetailView above, and
    core.views.aggregations's TransactionListCreateView/TransactionDetailView
    (imported from there). Lives at the view layer (not on the model itself)
    because BusinessRuleError is a DRF/HTTP concern, not a domain one — see
    BankAccount.is_synced's docstring.
    """
    if account.is_synced:
        raise BusinessRuleError(
            "This account is bank-integrated and syncs automatically; "
            "it can't be edited or deleted manually.",
            code="synced_account_read_only",
        )


def assert_bank_not_already_synced(user, bank_name: str) -> None:
    """
    Raise if `bank_name` matches a bank the user already has synced
    accounts for — that bank alone is the source of truth for its own
    accounts once connected (services/bank_connectors/sync.py's
    apply_synced_accounts), so a manual account can't shadow or duplicate
    one under the same name. Accounts at any other bank are unaffected —
    this is scoped per-bank, not a blanket restriction on the user.
    """
    if BankAccount.objects.filter(
        user=user, link_type=BankAccount.LINK_TYPE_SYNCED, bank_name__iexact=bank_name
    ).exists():
        raise BusinessRuleError(
            "This bank is already connected; its accounts can only arrive through sync.",
            code="bank_account_source_of_truth",
        )
