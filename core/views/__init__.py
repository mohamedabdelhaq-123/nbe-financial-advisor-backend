from core.views.aggregations import (
    AnomaliesView,
    AnomalyResolveView,
    CategoryBreakdownView,
    MonthlySummariesView,
    NetWorthView,
    RecurringChargesView,
    SpendingInsightsView,
    StabilityScoreView,
    TransactionDetailView,
    TransactionListCreateView,
)
from core.views.auth import LoginView, LogoutView, RefreshView, SignupView
from core.views.health import db_check, health, ping
from core.views.profile import (
    BankAccountDetailView,
    BankAccountListCreateView,
    MeConsentRevokeView,
    MeConsentView,
    MePreferencesView,
    MeView,
)
from core.views.statements import (
    StatementDetailView,
    StatementListCreateView,
    StatementNormalizedView,
    StatementOcrResultView,
)

__all__ = [
    "health",
    "db_check",
    "ping",
    "SignupView",
    "LoginView",
    "RefreshView",
    "LogoutView",
    "MeView",
    "MePreferencesView",
    "MeConsentView",
    "MeConsentRevokeView",
    "BankAccountListCreateView",
    "BankAccountDetailView",
    "StatementListCreateView",
    "StatementDetailView",
    "StatementOcrResultView",
    "StatementNormalizedView",
    "TransactionListCreateView",
    "TransactionDetailView",
    "MonthlySummariesView",
    "CategoryBreakdownView",
    "RecurringChargesView",
    "AnomaliesView",
    "AnomalyResolveView",
    "SpendingInsightsView",
    "NetWorthView",
    "StabilityScoreView",
]
