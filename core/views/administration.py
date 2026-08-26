from datetime import datetime
from datetime import timezone as dt_timezone

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import IntegrityError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import generics, status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.settings import api_settings as simplejwt_settings
from rest_framework_simplejwt.tokens import RefreshToken

from core.exceptions import ConflictError
from core.filters.administration import (
    AdminIssueFilterSet,
    AdminProductFilterSet,
    AdminReactionFilterSet,
)
from core.models import (
    AdminBlacklistedToken,
    AdminUser,
    ProblemStatement,
    Product,
    Reaction,
    ReportedIssue,
)
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
from core.serializers.budgets import (
    AdminTemplateCreateSerializer,
    AdminTemplateUpdateSerializer,
    StarterTemplateSerializer,
)
from services import ai_service, file_storage

# Built once at process startup with the same configured preferred hasher as
# real admin passwords. Unknown-email attempts still perform one expensive
# password verification, so the early timing does not disclose whether an
# admin address exists.
_ADMIN_LOGIN_DUMMY_HASH = make_password("not-a-real-admin-password")


def _set_admin_refresh_cookie(response, refresh_token: str) -> None:
    """Same as auth.py's _set_refresh_cookie, under a separate cookie name
    so an admin session and a user session never collide."""
    response.set_cookie(
        settings.ADMIN_REFRESH_TOKEN_COOKIE_NAME,
        refresh_token,
        max_age=settings.REFRESH_TOKEN_COOKIE_MAX_AGE,
        httponly=True,
        secure=settings.REFRESH_TOKEN_COOKIE_SECURE,
        samesite=settings.REFRESH_TOKEN_COOKIE_SAMESITE,
        path="/",
    )


def _admin_token_pair_response(admin_user, status_code) -> Response:
    """Shared by AdminLoginView/AdminRefreshView: issues a fresh admin
    token pair (body: access_token/admin_id/role, cookie: refresh_token).

    Not RefreshToken.for_user(): simplejwt's blacklist app hardcodes
    OutstandingToken.user to AUTH_USER_MODEL (core.User), which rejects an
    AdminUser. Rotation/revocation goes through AdminBlacklistedToken
    instead (AdminRefreshView/AdminLogoutView)."""
    refresh = RefreshToken()
    refresh[simplejwt_settings.USER_ID_CLAIM] = str(admin_user.id)
    # Must be set before .access_token is read — it copies claims at that point.
    refresh["is_admin"] = True

    response = Response(
        {
            "access_token": str(refresh.access_token),
            "admin_id": str(admin_user.id),
            "role": admin_user.role,
        },
        status=status_code,
    )
    _set_admin_refresh_cookie(response, str(refresh))
    return response


class AdminLoginView(APIView):
    """
    Authenticate an admin/internal-staff user — a separate credential space
    from end-user auth, never interchangeable. Refresh token is set as an
    httpOnly cookie (see POST /admin/auth/refresh, POST /admin/auth/logout).
    Generic error message on failure, same anti-enumeration reasoning as
    end-user login.
    """

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"

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
        password_hash = (
            admin_user.password_hash if admin_user is not None else _ADMIN_LOGIN_DUMMY_HASH
        )
        password_matches = check_password(password, password_hash)
        if admin_user is None or not password_matches:
            # Deliberately generic — same reasoning as end-user login
            # (core/serializers/auth.py): doesn't reveal whether the email
            # is registered.
            raise ValidationError("Invalid email or password.")

        return _admin_token_pair_response(admin_user, status.HTTP_200_OK)


class AdminRefreshView(APIView):
    """Admin equivalent of POST /auth/refresh — exchanges the httpOnly
    admin refresh cookie for a new access token. Can't reuse simplejwt's
    own rotation (it blacklists via OutstandingToken, which rejects
    AdminUser); rotates against AdminBlacklistedToken instead."""

    permission_classes = [AllowAny]

    @extend_schema(
        request=None,
        responses={200: AdminLoginResponseSerializer, **error_responses(401)},
    )
    def post(self, request):
        raw_token = request.COOKIES.get(settings.ADMIN_REFRESH_TOKEN_COOKIE_NAME)
        if not raw_token:
            raise InvalidToken("No admin refresh token cookie present.")

        try:
            refresh = RefreshToken(raw_token)
        except TokenError as exc:
            raise InvalidToken(str(exc)) from exc

        if not refresh.payload.get("is_admin"):
            raise InvalidToken("Not an admin refresh token.")

        # jti's UNIQUE constraint is the real race guard — a replayed or
        # concurrently-reused token hits IntegrityError, never honored twice.
        try:
            AdminBlacklistedToken.objects.create(
                jti=refresh.payload[simplejwt_settings.JTI_CLAIM],
                expires_at=datetime.fromtimestamp(refresh.payload["exp"], tz=dt_timezone.utc),
            )
        except IntegrityError as exc:
            raise InvalidToken("This admin refresh token has already been used.") from exc

        try:
            admin_user = AdminUser.objects.get(id=refresh.payload[simplejwt_settings.USER_ID_CLAIM])
        except AdminUser.DoesNotExist as exc:
            raise InvalidToken("Admin user not found.") from exc

        return _admin_token_pair_response(admin_user, status.HTTP_200_OK)


class AdminLogoutView(AdminAuthMixin, APIView):
    """Admin equivalent of POST /auth/logout — blacklists the refresh
    token and clears the cookie. Idempotent: no cookie is "already logged
    out" (204), not an error."""

    @extend_schema(
        request=None,
        responses={204: None, **error_responses(401)},
    )
    def post(self, request):
        raw_token = request.COOKIES.get(settings.ADMIN_REFRESH_TOKEN_COOKIE_NAME)
        if raw_token:
            try:
                refresh = RefreshToken(raw_token)
                AdminBlacklistedToken.objects.get_or_create(
                    jti=refresh.payload[simplejwt_settings.JTI_CLAIM],
                    defaults={
                        "expires_at": datetime.fromtimestamp(
                            refresh.payload["exp"], tz=dt_timezone.utc
                        )
                    },
                )
            except TokenError:
                pass  # already malformed/expired, nothing to blacklist
        response = Response(status=status.HTTP_204_NO_CONTENT)
        response.delete_cookie(settings.ADMIN_REFRESH_TOKEN_COOKIE_NAME, path="/")
        return response


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

    POST's optional `problem_statements` are seed text embedded in one
    batch call to the AI service (services/ai_service.py's
    create_embeddings()) and stored on each ProblemStatement.embedding,
    powering `GET /recommendations`'s semantic-match search. The product
    itself is usable for direct display immediately either way. A create
    failure (AIServiceError) is not caught here, so it surfaces as a clear
    error rather than silently leaving problem_statements unembedded.
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

        # Embedded in one batch call (not per-statement) — /internal/embeddings
        # takes a list of texts and returns one vector per index. A failure
        # here (AIServiceError) is deliberately not caught, so it surfaces as
        # a clear 500 rather than silently leaving problem_statements unembedded.
        embeddings = []
        if problem_statements:
            response = ai_service.get_client().create_embeddings(problem_statements, dimensions=768)
            # Matched by `index`, not array position — the response's own
            # ordering isn't documented as guaranteed to match input order.
            by_index = {datum["index"]: datum["embedding"] for datum in response["data"]}
            embeddings = [by_index[i] for i in range(len(problem_statements))]
        for statement_text, embedding in zip(problem_statements, embeddings):
            ProblemStatement.objects.create(
                product=product, statement_text=statement_text, embedding=embedding
            )

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


@extend_schema_view(
    post=extend_schema(
        request=AdminTemplateCreateSerializer,
        responses={201: StarterTemplateSerializer, **error_responses(403, 409, 422)},
    )
)
class AdminOnboardingTemplateListCreateView(AdminAuthMixin, APIView):
    """
    List every onboarding starter template (GET — any admin role) or add a
    new one (POST — super_admin only, 403 for any other admin role). Backed
    by pfm-reference-data/onboarding-templates/*.json
    (services/file_storage.py), not a DB table, since GET
    /budget/starter-templates reads that same reference data directly — a
    template created/edited here is immediately what onboarding shows, no
    separate publish step. `is_suggested` (present on the public endpoint's
    response) isn't part of the stored object — it's computed per-request
    from the signed-in admin's own profile there, which is meaningless for
    an admin session, so it's omitted here entirely rather than showing a
    misleading value.
    """

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsSuperAdmin()]
        return super().get_permissions()

    @extend_schema(responses={200: StarterTemplateSerializer(many=True)})
    def get(self, request):
        templates = file_storage.get_onboarding_templates()
        templates.sort(key=lambda t: t["template_key"])
        return Response(templates)

    def post(self, request):
        serializer = AdminTemplateCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        template_key = data["template_key"]

        if file_storage.get_onboarding_template(template_key) is not None:
            raise ConflictError(
                f"A template with key '{template_key}' already exists. Use "
                f"PATCH /admin/onboarding-templates/{template_key}/ to update it."
            )

        template = {
            "template_key": template_key,
            "name": data["name"],
            "description": data.get("description", ""),
            "allocations": [
                {
                    "category": a["category"].name,
                    "allocated_percentage": float(a["allocated_percentage"]),
                }
                for a in data["allocations"]
            ],
        }
        file_storage.put_onboarding_template(template)
        return Response(template, status=201)


class AdminOnboardingTemplateDetailView(AdminAuthMixin, APIView):
    """Update or remove a single onboarding starter template. Both
    operations are super_admin only — any other admin role gets 403.
    `template_key` itself is never reassignable through PATCH — it's the
    object's identity (and filename); delete and re-create under a new key
    instead."""

    permission_classes = [IsSuperAdmin]

    @extend_schema(
        request=AdminTemplateUpdateSerializer,
        responses={200: StarterTemplateSerializer, **error_responses(403, 404, 422)},
    )
    def patch(self, request, template_key):
        template = file_storage.get_onboarding_template(template_key)
        if template is None:
            raise NotFound(f"No template with key '{template_key}'.")

        serializer = AdminTemplateUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if "name" in data:
            template["name"] = data["name"]
        if "description" in data:
            template["description"] = data["description"]
        if "allocations" in data:
            template["allocations"] = [
                {
                    "category": a["category"].name,
                    "allocated_percentage": float(a["allocated_percentage"]),
                }
                for a in data["allocations"]
            ]

        file_storage.put_onboarding_template(template)
        return Response(template)

    @extend_schema(responses={204: None, **error_responses(403, 404)})
    def delete(self, request, template_key):
        if file_storage.get_onboarding_template(template_key) is None:
            raise NotFound(f"No template with key '{template_key}'.")
        file_storage.delete_onboarding_template(template_key)
        return Response(status=204)
