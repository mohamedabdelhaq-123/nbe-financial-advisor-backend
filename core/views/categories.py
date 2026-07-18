from django.db.models import ProtectedError
from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.generics import ListAPIView, ListCreateAPIView
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from core.exceptions import ConflictError
from core.filters.categories import CategoryFilterSet
from core.models import Category
from core.openapi import error_responses
from core.permissions import AdminAuthMixin, IsSuperAdmin
from core.serializers.categories import (
    CategoryCreateSerializer,
    CategorySerializer,
    CategoryUpdateSerializer,
)


class CategoryListView(ListAPIView):
    """List the category taxonomy — every authenticated user, read-only.

    This is what the frontend calls to render income vs. expense categories
    distinctly; writes are admin-only (see Admin* views below).
    """

    serializer_class = CategorySerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = CategoryFilterSet

    def get_queryset(self):
        return Category.objects.all().order_by("category_type", "name")


@extend_schema_view(
    post=extend_schema(
        request=CategoryCreateSerializer,
        responses={201: CategorySerializer, **error_responses(403, 422)},
    )
)
class AdminCategoryListCreateView(AdminAuthMixin, ListCreateAPIView):
    """List every category (GET — any admin role) or add a new one
    (POST — super_admin only, 403 for any other admin role).

    A duplicate `name` is a 422 (`Category.name` is a unique model field, so
    DRF's ModelSerializer validates it automatically), not a 409 — no manual
    check needed here.
    """

    filter_backends = [DjangoFilterBackend]
    filterset_class = CategoryFilterSet
    pagination_class = LimitOffsetPagination

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsSuperAdmin()]
        return super().get_permissions()

    def get_serializer_class(self):
        return CategoryCreateSerializer if self.request.method == "POST" else CategorySerializer

    def get_queryset(self):
        return Category.objects.all().order_by("category_type", "name")

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        category = Category.objects.create(**serializer.validated_data)
        return Response(CategorySerializer(category).data, status=201)


class AdminCategoryDetailView(AdminAuthMixin, APIView):
    """Update or remove a single category. Both operations are super_admin
    only — any other admin role gets 403."""

    permission_classes = [IsSuperAdmin]

    @extend_schema(
        request=CategoryUpdateSerializer,
        responses={200: CategorySerializer, **error_responses(403, 404, 422)},
    )
    def patch(self, request, category_id):
        category = get_object_or_404(Category, id=category_id)
        serializer = CategoryUpdateSerializer(category, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(CategorySerializer(category).data)

    @extend_schema(
        description=(
            "Hard-delete the category. Rejected with 409 if any transaction "
            "or budget allocation still references it (category is a "
            "PROTECT-ed foreign key) — reassign or delete those first."
        ),
        responses={204: None, **error_responses(403, 404, 409)},
    )
    def delete(self, request, category_id):
        category = get_object_or_404(Category, id=category_id)
        try:
            category.delete()
        except ProtectedError as exc:
            raise ConflictError(
                "This category is still in use by existing transactions or "
                "budget allocations and cannot be deleted."
            ) from exc
        return Response(status=204)
