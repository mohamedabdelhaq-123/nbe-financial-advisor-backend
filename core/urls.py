from django.urls import path

from core.ask_view import ask
from core.views import (
    AdminCategoryDetailView,
    AdminCategoryListCreateView,
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
    BankConnectionCallbackView,
    BankConnectionListCreateView,
    BankLoginCallbackView,
    BankLoginInitiateView,
    BankSyncWebhookView,
    BudgetHistoryView,
    BudgetProgressView,
    BudgetView,
    CategoryBreakdownView,
    CategoryListView,
    ConversationAttachmentsView,
    ConversationDetailView,
    ConversationListCreateView,
    ConversationMessagesView,
    DashboardGoalView,
    DashboardView,
    EmailVerificationConfirmView,
    EmailVerificationRequestView,
    EventStreamView,
    FeedbackCreateView,
    GoalView,
    IssueListCreateView,
    LoginView,
    LogoutView,
    MeConsentRevokeView,
    MeConsentView,
    MePreferencesView,
    MeView,
    MonthlySummariesView,
    NetWorthView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
    RecommendationFeedbackView,
    RecommendationsView,
    RecurringChargesView,
    RefreshView,
    SavingsProgressView,
    SignupView,
    SpendingInsightsView,
    SSETicketMintView,
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
    # Bank login — a secondary sign-in path alongside signup/login above:
    # authenticate as a bank customer via OAuth+OTP instead of an app
    # password (services/bank_connectors/, mock-bank-oauth/). POST initiate/
    # returns an authorize_url for the frontend to send the user's browser
    # to; the callback below is where the frontend lands the OAuth code and
    # receives a normal token pair back.
    path("auth/bank-login/initiate/", BankLoginInitiateView.as_view()),
    path("auth/bank-login/callback/", BankLoginCallbackView.as_view()),
    path("auth/password-reset/request/", PasswordResetRequestView.as_view()),
    path("auth/password-reset/confirm/", PasswordResetConfirmView.as_view()),
    path("auth/verify-email/request/", EmailVerificationRequestView.as_view()),
    path("auth/verify-email/confirm/", EmailVerificationConfirmView.as_view()),
    # 2. Profile & Preferences (API_Endpoints_1.md §2)
    path("users/me/", MeView.as_view()),
    path("users/me/preferences/", MePreferencesView.as_view()),
    path("users/me/consent/", MeConsentView.as_view()),
    path("users/me/consent/<uuid:consent_id>/", MeConsentRevokeView.as_view()),
    # 3. Bank Accounts (API_Endpoints_1.md §3)
    path("accounts/", BankAccountListCreateView.as_view()),
    path("accounts/<uuid:account_id>/", BankAccountDetailView.as_view()),
    # Bank connections — OAuth+OTP linking of integrated bank accounts
    # (services/bank_connectors/, mock-bank-oauth/). POST initiates a link,
    # returning an authorize_url for the frontend to send the user's browser
    # to; the callback below is where the frontend lands the OAuth code.
    path("bank-connections/", BankConnectionListCreateView.as_view()),
    path(
        "bank-connections/<uuid:connection_id>/callback/",
        BankConnectionCallbackView.as_view(),
    ),
    # Inbound machine-to-machine endpoint — shared-secret authenticated,
    # never end-user JWT (core/authentication.py's _SharedSecretAuthentication
    # subclasses). mock-bank-sync (later: a real bank's own sync feed) pushes
    # transaction batches here.
    path("webhooks/bank-sync/", BankSyncWebhookView.as_view()),
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
    # Categories — shared income/expense taxonomy for transactions and budget
    # allocations; read-only here, writes are admin-only (see §12 below).
    path("categories/", CategoryListView.as_view()),
    # 5. Transactions (API_Endpoints_1.md §5)
    path("transactions/", TransactionListCreateView.as_view()),
    path("transactions/<uuid:transaction_id>/", TransactionDetailView.as_view()),
    # 6. Budget (API_Endpoints_1.md §6)
    path("budget/", BudgetView.as_view()),
    path("budget/history/", BudgetHistoryView.as_view()),
    path("budget/progress/", BudgetProgressView.as_view()),
    path("budget/savings-progress/", SavingsProgressView.as_view()),
    path("budget/starter-templates/", StarterTemplatesView.as_view()),
    # Goal — its own entity, one-to-one with User (PLAN.md Checkpoint C),
    # not nested under Budget.
    path("goal/", GoalView.as_view()),
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
    # Events (SSE) — async infra phase, single multiplexed connection per user
    path("events/ticket/", SSETicketMintView.as_view()),
    path("events/stream/", EventStreamView.as_view()),
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
    path("admin/categories/", AdminCategoryListCreateView.as_view()),
    path("admin/categories/<uuid:category_id>/", AdminCategoryDetailView.as_view()),
]
