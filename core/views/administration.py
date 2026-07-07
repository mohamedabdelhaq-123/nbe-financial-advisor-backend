from django.contrib.auth.hashers import check_password
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import generics
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.settings import api_settings as simplejwt_settings
from rest_framework_simplejwt.tokens import RefreshToken

from core.models import AdminUser, ProblemStatement, Product, Reaction, ReportedIssue
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
    """POST /admin/auth/login — pre-auth, own credential space (see core/authentication.py)."""

    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(request=AdminLoginSerializer, responses={200: AdminLoginResponseSerializer})
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
    """GET /admin/feedback — cross-user by design, reviewer or super_admin."""

    serializer_class = AdminReactionSerializer
    pagination_class = LimitOffsetPagination

    def get_queryset(self):
        qs = Reaction.objects.all()
        params = self.request.query_params
        if params.get("target_type"):
            qs = qs.filter(target_type=params["target_type"])
        if params.get("rating"):
            qs = qs.filter(rating=params["rating"])
        if params.get("from"):
            qs = qs.filter(created_at__date__gte=params["from"])
        if params.get("to"):
            qs = qs.filter(created_at__date__lte=params["to"])
        return qs.order_by("-created_at")


class AdminIssueListView(AdminAuthMixin, generics.ListAPIView):
    """GET /admin/issues — cross-user by design, reviewer or super_admin."""

    serializer_class = AdminIssueSerializer
    pagination_class = LimitOffsetPagination

    def get_queryset(self):
        qs = ReportedIssue.objects.all()
        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)
        return qs.order_by("-created_at")


class AdminIssueUpdateView(AdminAuthMixin, APIView):
    """PATCH /admin/issues/{issue_id} — reviewer or super_admin."""

    @extend_schema(request=AdminIssueUpdateSerializer, responses={200: AdminIssueSerializer})
    def patch(self, request, issue_id):
        issue = get_object_or_404(ReportedIssue, id=issue_id)  # cross-user: no ownership filter
        serializer = AdminIssueUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_status = serializer.validated_data["status"]

        issue.status = new_status
        # Setting resolved/dismissed sets resolved_at server-side; moving
        # back to open/in_review clears it (Data_Shapes_Administration.md).
        issue.resolved_at = timezone.now() if new_status in ("resolved", "dismissed") else None
        issue.save()

        return Response(AdminIssueSerializer(issue).data)


class AdminProductListCreateView(AdminAuthMixin, generics.ListCreateAPIView):
    """
    GET /admin/products — any admin role.
    POST /admin/products — super_admin only.
    """

    pagination_class = LimitOffsetPagination

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsSuperAdmin()]
        return super().get_permissions()

    def get_serializer_class(self):
        return AdminProductCreateSerializer if self.request.method == "POST" else AdminProductSerializer

    def get_queryset(self):
        # Includes inactive products — unlike the user-facing GET
        # /recommendations, which only ever surfaces active, matched ones
        # (Data_Shapes_Administration.md).
        qs = Product.objects.all()
        params = self.request.query_params
        if params.get("is_active") is not None:
            qs = qs.filter(is_active=params["is_active"].lower() == "true")
        if params.get("category"):
            qs = qs.filter(categories__contains=[params["category"]])
        return qs.order_by("created_at")

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
    """PATCH/DELETE /admin/products/{product_id} — super_admin only."""

    permission_classes = [IsSuperAdmin]

    @extend_schema(request=AdminProductUpdateSerializer, responses={200: AdminProductSerializer})
    def patch(self, request, product_id):
        product = get_object_or_404(Product, id=product_id)
        serializer = AdminProductUpdateSerializer(product, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(AdminProductSerializer(product).data)

    @extend_schema(responses={204: None})
    def delete(self, request, product_id):
        # Hard delete, cascades to problem_statements and recommendation_logs
        # (DB_Schema.md: ON DELETE CASCADE on both). Data_Shapes_
        # Administration.md recommends PATCH {"is_active": false} instead
        # where a product might be reinstated later — that's the caller's
        # choice, not enforced here.
        product = get_object_or_404(Product, id=product_id)
        product.delete()
        return Response(status=204)
