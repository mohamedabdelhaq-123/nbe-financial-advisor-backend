from datetime import datetime
from datetime import timezone as dt_timezone

from django.conf import settings
from django.contrib.auth.hashers import check_password
from django.db import IntegrityError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.settings import api_settings as simplejwt_settings
from rest_framework_simplejwt.tokens import RefreshToken

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
from services import ai_service


def _set_admin_refresh_cookie(response, refresh_token: str) -> None:
    """
    Admin-credential-space equivalent of core/views/auth.py's
    _set_refresh_cookie — same cookie attributes (Secure/SameSite/MaxAge),
    a different name (ADMIN_REFRESH_TOKEN_COOKIE_NAME) so an admin session
    and an end-user session in the same browser never collide.
    """
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
    """
    Shared by AdminLoginView and AdminRefreshView below: issues a fresh
    admin token pair and returns it in the one shape both endpoints
    document (AdminLoginResponseSerializer) — access_token/admin_id/role in
    the body, refresh_token as an httpOnly cookie (SEC-009).

    Deliberately NOT RefreshToken.for_user(admin_user): with
    rest_framework_simplejwt.token_blacklist installed (needed for
    end-user logout — core/views/auth.py), simplejwt's BlacklistMixin
    overrides for_user() to also insert an OutstandingToken row via
    `OutstandingToken.objects.create(user=user, ...)` — and that model's
    `user` FK is hardcoded to AUTH_USER_MODEL (core.User), so it rejects an
    AdminUser instance outright (confirmed by hitting this exact ValueError
    during smoke testing). Constructing the token directly replicates the
    *base* Token.for_user()'s behavior (set the user_id claim, nothing
    else) without going through the blacklist-specific override — rotation/
    revocation for admin tokens goes through AdminBlacklistedToken instead
    (AdminRefreshView/AdminLogoutView), this app's own minimal equivalent.
    """
    refresh = RefreshToken()
    refresh[simplejwt_settings.USER_ID_CLAIM] = str(admin_user.id)
    # The claim that makes an admin token structurally non-interchangeable
    # with a user token — see core/authentication.py's module docstring.
    # Must be set before .access_token is read below, since RefreshToken.
    # access_token copies the refresh token's claims at that point.
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
    Authenticate an admin/internal-staff user. This is a completely
    separate credential space from end-user auth (`POST /auth/login`) —
    an admin token is never interchangeable with a user token on any
    endpoint, and vice versa. The refresh token is set as an httpOnly
    cookie (ADMIN_REFRESH_TOKEN_COOKIE_NAME), never in the response body —
    see POST /admin/auth/refresh for how it's used later, and POST
    /admin/auth/logout to end the session early (SEC-009: this used to not
    exist, which is why the frontend persisted the admin bearer token in
    sessionStorage — JS-readable, and a materially higher-value XSS target
    than the end-user token specifically because it had no way to be
    revoked or silently restored any other way). On failure, the error
    message is deliberately generic ("Invalid email or password")
    regardless of whether the email is registered, for the same
    anti-enumeration reason as end-user login.
    """

    authentication_classes = []
    permission_classes = [AllowAny]
    # SEC-004 — shares the "auth" scope (config/settings.py's
    # DEFAULT_THROTTLE_RATES) with end-user login/signup/password-reset;
    # same brute-force concern, separate credential space.
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
        # check_password() against a fixed dummy hash when admin_user is None
        # would be the textbook timing-attack-resistant move; skipped here as
        # disproportionate for a mocked-services/routes checkpoint — noted,
        # not silently overlooked.
        if admin_user is None or not check_password(password, admin_user.password_hash):
            # Deliberately generic — same reasoning as end-user login
            # (core/serializers/auth.py): doesn't reveal whether the email
            # is registered.
            raise ValidationError("Invalid email or password.")

        return _admin_token_pair_response(admin_user, status.HTTP_200_OK)


class AdminRefreshView(APIView):
    """
    Exchange the httpOnly admin refresh-token cookie for a new access
    token — the admin-credential-space equivalent of POST /auth/refresh
    (core/views/auth.py's RefreshView), added to close SEC-009.

    Can't reuse RefreshView/TokenRefreshSerializer as-is: simplejwt's own
    rotation logic calls RefreshToken.blacklist(), which looks the token up
    in OutstandingToken — a table admin refresh tokens are never inserted
    into (see _admin_token_pair_response's docstring for why: that model's
    `user` FK is hardcoded to AUTH_USER_MODEL/core.User, which rejects an
    AdminUser outright). Rotation here checks/records against
    AdminBlacklistedToken instead.

    Takes no request body — same as RefreshView, the refresh token comes
    from the cookie, never a client-supplied field.
    """

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

        # Rotate: blacklist the presented token FIRST, atomically — the
        # UNIQUE constraint on `jti` is the actual race guard here, not a
        # separate exists()-then-create() (which would leave a window for
        # two concurrent requests presenting the same token to both pass a
        # check before either writes) — so a replayed/concurrently-reused
        # token is rejected via IntegrityError, never honored twice.
        try:
            AdminBlacklistedToken.objects.create(
                jti=refresh.payload[simplejwt_settings.JTI_CLAIM],
                expires_at=datetime.fromtimestamp(refresh.payload["exp"], tz=dt_timezone.utc),
            )
        except IntegrityError as exc:
            raise InvalidToken("This admin refresh token has already been used.") from exc

        try:
            admin_user = AdminUser.objects.get(
                id=refresh.payload[simplejwt_settings.USER_ID_CLAIM]
            )
        except AdminUser.DoesNotExist as exc:
            raise InvalidToken("Admin user not found.") from exc

        return _admin_token_pair_response(admin_user, status.HTTP_200_OK)


class AdminLogoutView(AdminAuthMixin, APIView):
    """
    End the current admin session: blacklists the refresh token (so it can
    never be exchanged for a new access token again, even if it leaked)
    and clears the httpOnly cookie — the admin-credential-space equivalent
    of POST /auth/logout (core/views/auth.py's LogoutView), added to close
    SEC-009. Requires a currently-valid admin access_token (AdminAuthMixin)
    — if that's already expired there's nothing meaningful left to
    blacklist server-side via this route anyway, same reasoning as the
    end-user version.

    Takes no request body — the refresh token comes from the cookie.
    Idempotent: no cookie present is "already logged out" (204), not an
    error, same as LogoutView.
    """

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
                # Already malformed/expired — nothing meaningful to
                # blacklist, same tolerance as LogoutView's TokenError
                # handling for the end-user flow.
                pass
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
            response = ai_service.create_embeddings(problem_statements, dimensions=768)
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
