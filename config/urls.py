from django.contrib import admin
from django.urls import include, path

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
    path("", include("core.urls")),
]
