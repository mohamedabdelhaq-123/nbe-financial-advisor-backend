from django.urls import path

from core.ask_view import ask
from core.views import LoginView, LogoutView, RefreshView, SignupView, db_check, health, ping

urlpatterns = [
    # Dev/ops probes — deliberately outside DRF (plain Django views), so the
    # DEFAULT_PERMISSION_CLASSES = IsAuthenticated default doesn't apply to them.
    path("health/", health),
    path("db/", db_check),
    path("ping/", ping),
    path("ask/", ask),
    # 1. Auth & Onboarding (docs/API_GUIDE/API_Endpoints_1.md §1)
    path("auth/signup/", SignupView.as_view()),
    path("auth/login/", LoginView.as_view()),
    path("auth/refresh/", RefreshView.as_view()),
    path("auth/logout/", LogoutView.as_view()),
]
