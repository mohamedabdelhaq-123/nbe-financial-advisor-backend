from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView
from rest_framework.permissions import AllowAny

urlpatterns = [
    # Django's own built-in HTML admin panel (session/CSRF-based, for devs/ops
    # provisioning AdminUser rows via core/admin.py — System_Architecture.md
    # §9) — relocated off /admin/ specifically because the documented REST
    # API namespace (API_Endpoints_1.md §12: /admin/auth/login,
    # /admin/feedback, /admin/products, ...) lives there. With both at
    # /admin/, `path("admin/", admin.site.urls)` greedily claimed the whole
    # prefix and silently swallowed every core.urls /admin/* route before it
    # was ever reached — discovered when POST /admin/auth/login/ returned a
    # raw Django CSRF error page (Django admin's own login view) instead of
    # this project's JSON response.
    path("django-admin/", admin.site.urls),
    # OpenAPI schema + docs UI (API Design Guidelines §11: generated directly
    # from the DRF serializers/viewsets, never hand-maintained separately).
    # permission_classes=[AllowAny] overrides the project-wide
    # IsAuthenticated default (config/settings.py) — these are meant to be
    # browsable without a JWT, same reasoning as the health/ping/ask dev
    # probes in core/urls.py.
    path(
        "api/schema/",
        SpectacularAPIView.as_view(permission_classes=[AllowAny]),
        name="schema",
    ),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema", permission_classes=[AllowAny]),
        name="swagger-ui",
    ),
    path(
        "api/redoc/",
        SpectacularRedocView.as_view(url_name="schema", permission_classes=[AllowAny]),
        name="redoc",
    ),
    path("", include("core.urls")),
]
