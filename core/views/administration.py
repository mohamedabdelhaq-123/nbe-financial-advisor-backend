from django.contrib.auth.hashers import check_password
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import generics
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.settings import api_settings as simplejwt_settings
from rest_framework_simplejwt.tokens import RefreshToken

from core.filters.administration import (
    AdminIssueFilterSet,
    AdminProductFilterSet,
    AdminReactionFilterSet,
)
from core.models import AdminUser, ProblemStatement, Product, Reaction, ReportedIssue
from core.openapi import error_responses
from core.permissions import AdminAuthMixin, IsSuperAdmin
from core.serializers.administration import (
    AdminIssueSerializer,
    AdminIssueUpdateSerializer,
    AdminLoginResponseSerializer,
    AdminLoginSerializer,
    AdminProductCreateSerializer,
    AdminProductSerializer,
    AdminProductUpdateSerializer,
    AdminReactionSerializer,
)


class AdminLoginView(APIView):
    """
    Authenticate an admin/internal-staff user. This is a completely
    separate credential space from end-user auth (`POST /auth/login`) —
    an admin token is never interchangeable with a user token on any
    endpoint, and vice versa. Unlike the end-user flow, the refresh token
    is returned directly in the response body here (no httpOnly cookie),
    and there is no admin logout/refresh endpoint — admin tokens simply
    expire on their own schedule. On failure, the error message is
    deliberately generic ("Invalid email or password") regardless of
    whether the email is registered, for the same anti-enumeration reason
    as end-user login.
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(
        request=AdminLoginSerializer,
        responses={200: AdminLoginResponseSerializer, **error_responses(422)},
    )
    def post(self, request):
        serializer = AdminLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]
        password = serializer.validated_data["password"]

        admin_user = AdminUser.objects.filter(email=email).first()
        # check_password() against a fixed dummy hash when admin_user is None
        # would be the textbook timing-attack-resistant move; skipped here as
        # disproportionate for a mocked-services/routes checkpoint — noted,
        # not silently overlooked.
        if admin_user is None or not check_password(password, admin_user.password_hash):
            # Deliberately generic — same reasoning as end-user login
            # (core/serializers/auth.py): doesn't reveal whether the email
            # is registered.
            raise ValidationError("Invalid email or password.")

        # Deliberately NOT RefreshToken.for_user(admin_user): with
        # rest_framework_simplejwt.token_blacklist installed (needed for
        # end-user logout — core/views/auth.py), simplejwt's BlacklistMixin
        # overrides for_user() to also insert an OutstandingToken row via
        # `OutstandingToken.objects.create(user=user, ...)` — and that
        # model's `user` FK is hardcoded to AUTH_USER_MODEL (core.User), so
        # it rejects an AdminUser instance outright (confirmed by hitting
        # this exact ValueError during smoke testing). Constructing the
        # token directly replicates the *base* Token.for_user()'s behavior
        # (set the user_id claim, nothing else) without going through the
        # blacklist-specific override — meaning admin tokens are also simply
        # not blacklist-trackable, which is fine: there's no
        # POST /admin/auth/logout in API_Endpoints_1.md §12 to need it.
        refresh = RefreshToken()
        refresh[simplejwt_settings.USER_ID_CLAIM] = str(admin_user.id)
        # The claim that makes an admin token structurally non-interchangeable
        # with a user token — see core/authentication.py's module docstring.
        # Must be set before .access_token is read below, since RefreshToken.
        # access_token copies the refresh token's claims at that point.
        refresh["is_admin"] = True

        return Response(
            {
                "access_token": str(refresh.access_token),
                "refresh_token": str(refresh),
                "admin_id": str(admin_user.id),
                "role": admin_user.role,
            }
        )


class AdminFeedbackListView(AdminAuthMixin, generics.ListAPIView):
    """List feedback (ratings/comments) left by every user, across the
    whole system — cross-user by design, unlike the end-user-facing
    Feedback domain which only ever shows a user their own. Any admin
    role (reviewer or super_admin) can access this."""

    serializer_class = AdminReactionSerializer
    pagination_class = LimitOffsetPagination
    filter_backends = [DjangoFilterBackend]
    filterset_class = AdminReactionFilterSet

    def get_queryset(self):
        return Reaction.objects.all().order_by("-created_at")


class AdminIssueListView(AdminAuthMixin, generics.ListAPIView):
    """List reported issues (bug reports/support requests) filed by every
    user, across the whole system — cross-user by design. Any admin role
    (reviewer or super_admin) can access this; use
    PATCH /admin/issues/{id} to move one through triage."""

    serializer_class = AdminIssueSerializer
    pagination_class = LimitOffsetPagination
    filter_backends = [DjangoFilterBackend]
    filterset_class = AdminIssueFilterSet

    def get_queryset(self):
        return ReportedIssue.objects.all().order_by("-created_at")


class AdminIssueUpdateView(AdminAuthMixin, APIView):
    """
    Move a reported issue through triage: `status` is one of
    `open | in_review | resolved | dismissed`. Setting it to `resolved` or
    `dismissed` stamps `resolved_at` server-side; moving it back to
    `open`/`in_review` clears that timestamp again. Any admin role
    (reviewer or super_admin) can do this — cross-user by design, no
    ownership check (any issue, filed by any user, can be triaged).
    """

    @extend_schema(
        request=AdminIssueUpdateSerializer,
        responses={200: AdminIssueSerializer, **error_responses(404, 422)},
    )
    def patch(self, request, issue_id):
        issue = get_object_or_404(ReportedIssue, id=issue_id)
        serializer = AdminIssueUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_status = serializer.validated_data["status"]

        issue.status = new_status
        # Setting resolved/dismissed sets resolved_at server-side; moving
        # back to open/in_review clears it (Data_Shapes_Administration.md).
        issue.resolved_at = timezone.now() if new_status in ("resolved", "dismissed") else None
        issue.save()

        return Response(AdminIssueSerializer(issue).data)


@extend_schema_view(
    post=extend_schema(
        request=AdminProductCreateSerializer,
        responses={201: AdminProductSerializer, **error_responses(403, 422)},
    )
)
class AdminProductListCreateView(AdminAuthMixin, generics.ListCreateAPIView):
    """
    List every product in the catalog (GET — any admin role, including
    inactive products, unlike the user-facing `GET /recommendations`,
    which only ever surfaces active ones), or add a new one
    (POST — super_admin only, 403 for any other admin role).

    POST's optional `problem_statements` are seed text for the product's
    future semantic-matching embeddings — the product is immediately
    usable for direct display either way, but won't be matchable via
    `GET /recommendations`'s query-based search until an embedding
    pipeline processes them (not wired up yet).
    """

    pagination_class = LimitOffsetPagination
    filter_backends = [DjangoFilterBackend]
    filterset_class = AdminProductFilterSet

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsSuperAdmin()]
        return super().get_permissions()

    def get_serializer_class(self):
        return (
            AdminProductCreateSerializer
            if self.request.method == "POST"
            else AdminProductSerializer
        )

    def get_queryset(self):
        return Product.objects.all().order_by("created_at")

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        problem_statements = data.pop("problem_statements", [])

        product = Product.objects.create(**data)
        for statement_text in problem_statements:
            # embedding stays null — no local embedding model is wired up
            # (services/ai_service.py has no embed() yet); the product is
            # usable for direct display immediately, per this endpoint's
            # documented behavior, but not yet matchable via semantic search
            # until a real embedding pipeline populates this.
            ProblemStatement.objects.create(product=product, statement_text=statement_text)

        return Response(AdminProductSerializer(product).data, status=201)


class AdminProductDetailView(AdminAuthMixin, APIView):
    """Update or remove a single product from the catalog. Both operations
    are super_admin only — any other admin role gets 403."""

    permission_classes = [IsSuperAdmin]

    @extend_schema(
        request=AdminProductUpdateSerializer,
        responses={200: AdminProductSerializer, **error_responses(403, 404, 422)},
    )
    def patch(self, request, product_id):
        product = get_object_or_404(Product, id=product_id)
        serializer = AdminProductUpdateSerializer(product, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(AdminProductSerializer(product).data)

    @extend_schema(
        description=(
            "Hard-delete the product — this also cascades to its problem "
            "statements and any recommendation logs that reference it. "
            'Consider PATCH {"is_active": false} instead if the product '
            "might be reinstated later; this endpoint doesn't enforce "
            "that choice either way, it's just a permanent delete."
        ),
        responses={204: None, **error_responses(403, 404)},
    )
    def delete(self, request, product_id):
        product = get_object_or_404(Product, id=product_id)
        product.delete()
        return Response(status=204)
