from django.urls import path

from core.ask_view import ask
from core.views import (
    AdminFeedbackListView,
    AdminIssueListView,
    AdminIssueUpdateView,
    AdminLoginView,
    AdminProductDetailView,
    AdminProductListCreateView,
    AnomaliesView,
    AnomalyResolveView,
    BankAccountDetailView,
    BankAccountListCreateView,
    BudgetHistoryView,
    BudgetProgressView,
    BudgetView,
    CategoryBreakdownView,
    ConversationAttachmentsView,
    ConversationDetailView,
    ConversationListCreateView,
    ConversationMessagesView,
    DashboardGoalView,
    DashboardView,
    FeedbackCreateView,
    IssueListCreateView,
    LoginView,
    LogoutView,
    MeConsentRevokeView,
    MeConsentView,
    MePreferencesView,
    MeView,
    MonthlySummariesView,
    NetWorthView,
    RecommendationFeedbackView,
    RecommendationsView,
    RecurringChargesView,
    RefreshView,
    SavingsProgressView,
    SignupView,
    SpendingInsightsView,
    StabilityScoreView,
    StarterTemplatesView,
    StatementDetailView,
    StatementListCreateView,
    StatementOcrArtifactDownloadView,
    StatementOcrResultView,
    StatementTransactionApprovalView,
    TransactionDetailView,
    TransactionListCreateView,
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
    path(
        "statements/<uuid:statement_id>/ocr-result/download/",
        StatementOcrArtifactDownloadView.as_view(),
        name="statement-ocr-artifact-download",
    ),
    path(
        "statements/<uuid:statement_id>/transactions/",
        StatementTransactionApprovalView.as_view(),
    ),
    # 5. Transactions (API_Endpoints_1.md §5)
    path("transactions/", TransactionListCreateView.as_view()),
    path("transactions/<uuid:transaction_id>/", TransactionDetailView.as_view()),
    # 6. Budget (API_Endpoints_1.md §6)
    path("budget/", BudgetView.as_view()),
    path("budget/history/", BudgetHistoryView.as_view()),
    path("budget/progress/", BudgetProgressView.as_view()),
    path("budget/savings-progress/", SavingsProgressView.as_view()),
    path("budget/starter-templates/", StarterTemplatesView.as_view()),
    # 7. Dashboard (API_Endpoints_1.md §7)
    path("dashboard/", DashboardView.as_view()),
    path("dashboard/goal/", DashboardGoalView.as_view()),
    # 9. AI Assistant / Conversations (API_Endpoints_1.md §9)
    path("chat/conversations/", ConversationListCreateView.as_view()),
    path("chat/conversations/<uuid:conversation_id>/", ConversationDetailView.as_view()),
    path("chat/conversations/<uuid:conversation_id>/messages/", ConversationMessagesView.as_view()),
    path(
        "chat/conversations/<uuid:conversation_id>/attachments/",
        ConversationAttachmentsView.as_view(),
    ),
    # 8. Analytics (API_Endpoints_1.md §8)
    path("analytics/monthly-summaries/", MonthlySummariesView.as_view()),
    path("analytics/category-breakdown/", CategoryBreakdownView.as_view()),
    path("analytics/recurring-charges/", RecurringChargesView.as_view()),
    path("analytics/anomalies/", AnomaliesView.as_view()),
    path("analytics/anomalies/<uuid:anomaly_id>/", AnomalyResolveView.as_view()),
    path("analytics/spending-insights/", SpendingInsightsView.as_view()),
    path("analytics/net-worth/", NetWorthView.as_view()),
    path("analytics/stability-score/", StabilityScoreView.as_view()),
    # 11. Feedback & Support (API_Endpoints_1.md §11)
    path("feedback/", FeedbackCreateView.as_view()),
    path("issues/", IssueListCreateView.as_view()),
    # 10. Recommendations (API_Endpoints_1.md §10)
    path("recommendations/", RecommendationsView.as_view()),
    path(
        "recommendations/<uuid:recommendation_id>/feedback/", RecommendationFeedbackView.as_view()
    ),
    # 12. Administration [admin] (API_Endpoints_1.md §12)
    path("admin/auth/login/", AdminLoginView.as_view()),
    path("admin/feedback/", AdminFeedbackListView.as_view()),
    path("admin/issues/", AdminIssueListView.as_view()),
    path("admin/issues/<uuid:issue_id>/", AdminIssueUpdateView.as_view()),
    path("admin/products/", AdminProductListCreateView.as_view()),
    path("admin/products/<uuid:product_id>/", AdminProductDetailView.as_view()),
]
