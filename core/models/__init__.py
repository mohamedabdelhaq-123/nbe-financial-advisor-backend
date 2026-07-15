# Import Profile Domain
# Import Administration Domain
from .administration.admin_user import AdminUser
from .aggregations.anomaly_flag import AnomalyFlag
from .aggregations.monthly_summary import MonthlySummary
from .aggregations.net_worth_snapshot import NetWorthSnapshot
from .aggregations.recurring_charge import RecurringCharge
from .aggregations.spending_pattern_insight import SpendingPatternInsight

# Import Aggregations Domain
from .aggregations.transaction import Transaction
from .budgets.allocation import BudgetAllocation

# Import Categories Domain
from .categories.category import Category

# Import Budgets Domain
from .budgets.budget import Budget
from .budgets.goal import Goal
from .budgets.history import BudgetHistory

# Import Conversations Domain
from .conversations.conversation import Conversation
from .conversations.message import Message
from .conversations.reference import MessageReference

# Import Feedback Domain
from .feedback.reaction import Reaction
from .feedback.reported_issue import ReportedIssue

# Import Test model
from .ping import Ping
from .profile.bank_account import BankAccount
from .profile.consent_record import ConsentRecord
from .profile.user import User
from .profile.user_preference import UserPreference
from .recommendation.log import RecommendationLog
from .recommendation.problem_statement import ProblemStatement

# Import Recommendation Domain
from .recommendation.product import Product
from .statements.file import StatementFile
from .statements.normalized import StatementNormalized
from .statements.ocr import StatementOcrResult

# Import Statements Domain
from .statements.template import BankStatementTemplate

# Explicitly define __all__ so Django's migration engine registers them smoothly
__all__ = [
    # Profile
    "User",
    "BankAccount",
    "ConsentRecord",
    "UserPreference",
    # Statements
    "BankStatementTemplate",
    "StatementFile",
    "StatementOcrResult",
    "StatementNormalized",
    # Conversations
    "Conversation",
    "Message",
    "MessageReference",
    # Budgets
    "Budget",
    "BudgetAllocation",
    "BudgetHistory",
    "Goal",
    # Feedback
    "Reaction",
    "ReportedIssue",
    # Recommendation
    "Product",
    "ProblemStatement",
    "RecommendationLog",
    # Categories
    "Category",
    # Aggregations
    "Transaction",
    "MonthlySummary",
    "RecurringCharge",
    "AnomalyFlag",
    "SpendingPatternInsight",
    "NetWorthSnapshot",
    # Administration
    "AdminUser",
    # Ping
    "Ping",
]
