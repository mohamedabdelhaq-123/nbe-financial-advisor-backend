from django.urls import path

from core.ask_view import ask
from core.views import (
    BankAccountDetailView,
    BankAccountListCreateView,
    LoginView,
    LogoutView,
    MeConsentRevokeView,
    MeConsentView,
    MePreferencesView,
    MeView,
    RefreshView,
    SignupView,
    StatementDetailView,
    StatementListCreateView,
    StatementNormalizedView,
    StatementOcrResultView,
    db_check,
    health,
    ping,
)

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
    # 2. Profile & Preferences (API_Endpoints_1.md §2)
    path("users/me/", MeView.as_view()),
    path("users/me/preferences/", MePreferencesView.as_view()),
    path("users/me/consent/", MeConsentView.as_view()),
    path("users/me/consent/<uuid:consent_id>/", MeConsentRevokeView.as_view()),
    # 3. Bank Accounts (API_Endpoints_1.md §3)
    path("accounts/", BankAccountListCreateView.as_view()),
    path("accounts/<uuid:account_id>/", BankAccountDetailView.as_view()),
    # 4. Statements & Document Ingestion (API_Endpoints_1.md §4)
    path("statements/", StatementListCreateView.as_view()),
    path("statements/<uuid:statement_id>/", StatementDetailView.as_view()),
    path("statements/<uuid:statement_id>/ocr-result/", StatementOcrResultView.as_view()),
    path("statements/<uuid:statement_id>/normalized/", StatementNormalizedView.as_view()),
]
