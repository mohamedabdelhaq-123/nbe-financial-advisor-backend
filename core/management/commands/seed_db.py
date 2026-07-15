"""Seed the local database with synthetic mock data across every domain.

See PLAN_SEED.md at the repo root for the full rationale. Short version:
local-dev-only (guarded by settings.DEBUG), fully synthetic (no real NBE
data), every run flushes its own previously-seeded rows first so reruns are
reproducible rather than accumulating duplicates.
"""

import argparse
import random
import uuid
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction as db_transaction
from django.utils import timezone

from core.models import (
    AdminUser,
    AnomalyFlag,
    BankAccount,
    BankStatementTemplate,
    Budget,
    BudgetAllocation,
    BudgetHistory,
    Category,
    ConsentRecord,
    Conversation,
    Goal,
    Message,
    MessageReference,
    MonthlySummary,
    NetWorthSnapshot,
    ProblemStatement,
    Product,
    Reaction,
    RecommendationLog,
    RecurringCharge,
    ReportedIssue,
    SpendingPatternInsight,
    StatementFile,
    StatementNormalized,
    StatementOcrResult,
    Transaction,
    User,
    UserPreference,
)

SEED_USER_EMAIL_PREFIX = "seed_user_"
SEED_ADMIN_EMAIL_PREFIX = "seed_admin_"
SEED_PRODUCT_LINK_PREFIX = "https://example.com/seed-products/"
SEED_TEMPLATE_SIGNATURE_PREFIX = "seed-"

BANKS = [
    "National Bank of Egypt",
    "Banque Misr",
    "Commercial International Bank",
    "QNB Alahli",
    "Arab African International Bank",
    "HSBC Egypt",
]

# (merchant_raw, merchant_normalized, category, transaction_type, (min, max) amount)
ONE_OFF_MERCHANTS = [
    ("CARREFOUR MAADI EG", "Carrefour", "food", "debit", (150, 900)),
    ("TALABAT EG*ORDER", "Talabat", "food", "debit", (80, 400)),
    ("UBER *TRIP", "Uber", "transport", "debit", (40, 250)),
    ("NOON.COM ONLINE", "Noon", "lifestyle", "debit", (200, 3000)),
    ("CIB ATM WITHDRAWAL", "ATM Withdrawal", "other", "debit", (500, 3000)),
    ("EGYPTAIR RESERVATIONS", "EgyptAir", "lifestyle", "debit", (2000, 9000)),
    ("SPINNEYS SUPERMARKET", "Spinneys", "food", "debit", (200, 1200)),
    ("CFC MALL RETAIL", "Cairo Festival City Mall", "lifestyle", "debit", (300, 2500)),
    ("BANK MAINTENANCE FEE", "Bank Maintenance Fee", "other", "fee", (25, 75)),
    ("NOON.COM REFUND", "Noon", "lifestyle", "credit", (100, 600)),
    ("RIGHT2LEARN TUITION", "Right2Learn", "other", "debit", (500, 2500)),
    ("VEZEETA CLINIC", "Vezeeta", "other", "debit", (150, 800)),
]

# (merchant_raw, merchant_normalized, category, transaction_type, (min, max) amount)
RECURRING_MERCHANTS = [
    ("VODAFONE EGYPT-POSTPAID", "Vodafone Egypt", "housing", "debit", (180, 220)),
    ("NETFLIX.COM", "Netflix", "lifestyle", "debit", (190, 210)),
    ("GOLDS GYM MEMBERSHIP", "Gold's Gym", "other", "debit", (450, 550)),
]

CONSENT_TYPES = ["data_processing", "terms_of_service"]
EMPLOYMENT_STATUSES = ["employed", "self_employed", "unemployed"]
INCOME_BRACKETS = ["low", "medium", "high"]


def _bool_arg(value):
    """argparse type: accepts a bare flag (True) or an explicit true/false value,
    so both `--gen_statements` and `--gen_statements=true|false` work."""
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in ("1", "true", "yes", "y"):
        return True
    if normalized in ("0", "false", "no", "n"):
        return False
    raise argparse.ArgumentTypeError(f"Expected a boolean value, got {value!r}")


def _money(amount, low=None, high=None):
    if low is not None:
        amount = random.uniform(low, high)
    return Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _json_money(value):
    """Decimals aren't JSON-serializable by plain json.dumps (which is what a
    bare models.JSONField uses) — every amount stored inside a JSONField
    (as opposed to a real DecimalField column) must be cast to float first."""
    return float(value)


class Command(BaseCommand):
    help = "Seed the local database with synthetic mock data (local dev only)."

    def add_arguments(self, parser):
        parser.add_argument("--users", type=int, default=5, help="Number of end users to generate.")
        parser.add_argument(
            "--seed", type=int, default=None, help="Fix random.seed() for reproducible output."
        )
        parser.add_argument(
            "--gen_statements",
            type=_bool_arg,
            nargs="?",
            const=True,
            default=False,
            help="Also generate Statements-domain rows (templates/files/OCR/"
            "normalized), with no backing file in SeaweedFS. Default: False "
            "— transactions are plain manual entries with no dangling "
            "statement pointers.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            default=False,
            help="Bypass the DEBUG-only guardrail.",
        )

    def handle(self, *args, **options):
        if not settings.DEBUG and not options["force"]:
            raise CommandError(
                "Refusing to seed synthetic data: settings.DEBUG is False. "
                "docs/Environment_Profiles.md restricts synthetic/fake data to "
                "local development — this looks like a non-local environment. "
                "Pass --force to override."
            )

        if options["seed"] is not None:
            random.seed(options["seed"])

        num_users = options["users"]
        gen_statements = options["gen_statements"]

        with db_transaction.atomic():
            self._flush()
            self._categories = {c.name: c for c in Category.objects.all()}
            admins = self._seed_admins()
            products = self._seed_products()
            templates = self._seed_templates() if gen_statements else []

            all_users = []
            all_recommendation_targets = []
            for i in range(num_users):
                user, accounts, budget = self._seed_user_graph(i, templates, gen_statements)
                all_users.append(user)
                all_recommendation_targets.append((user, accounts, budget))

            self._seed_recommendation_logs(all_recommendation_targets, products)

        self.stdout.write(self.style.SUCCESS(f"Seeded {len(all_users)} users."))
        self.stdout.write(
            f"Statements domain: {'generated' if gen_statements else 'skipped (default)'}"
        )
        self.stdout.write("\nSeeded user credentials (password is the same for all): SeedPass123!")
        for user in all_users:
            self.stdout.write(f"  {user.email}")
        self.stdout.write("\nSeeded admin credentials (password: SeedAdminPass123!):")
        for admin in admins:
            self.stdout.write(f"  {admin.email} ({admin.role})")

    # ------------------------------------------------------------------
    # Flush
    # ------------------------------------------------------------------

    def _flush(self):
        """Deletes only rows this command owns, identified by a fixed marker
        (seeded emails/links), never a developer's own manually-entered data.
        User deletion cascades (CASCADE/OneToOne FKs) through essentially
        every other per-user domain — see PLAN_SEED.md's Implementation section."""
        User.objects.filter(email__startswith=SEED_USER_EMAIL_PREFIX).delete()
        AdminUser.objects.filter(email__startswith=SEED_ADMIN_EMAIL_PREFIX).delete()
        Product.objects.filter(external_link__startswith=SEED_PRODUCT_LINK_PREFIX).delete()
        BankStatementTemplate.objects.filter(
            layout_signature__startswith=SEED_TEMPLATE_SIGNATURE_PREFIX
        ).delete()

    # ------------------------------------------------------------------
    # Shared/global catalogs
    # ------------------------------------------------------------------

    def _seed_admins(self):
        reviewer = AdminUser.objects.create(
            name="Seed Reviewer",
            email=f"{SEED_ADMIN_EMAIL_PREFIX}reviewer@example.com",
            password_hash=make_password("SeedAdminPass123!"),
            role="reviewer",
        )
        super_admin = AdminUser.objects.create(
            name="Seed Super Admin",
            email=f"{SEED_ADMIN_EMAIL_PREFIX}super@example.com",
            password_hash=make_password("SeedAdminPass123!"),
            role="super_admin",
        )
        return [reviewer, super_admin]

    def _seed_products(self):
        catalog = [
            (
                "NBE Certificate of Deposit - 3 Years",
                "Fixed-return savings certificate with a 3-year lock-in period.",
                ["savings", "certificates"],
                ["fixed-income", "long-term"],
                {"interest_rate": "22.5%", "min_amount": 1000, "term_months": 36},
            ),
            (
                "NBE Certificate of Deposit - 1 Year",
                "Fixed-return savings certificate with a 1-year lock-in period.",
                ["savings", "certificates"],
                ["fixed-income", "short-term"],
                {"interest_rate": "20%", "min_amount": 1000, "term_months": 12},
            ),
            (
                "Personal Loan - Salary Backed",
                "Unsecured personal loan for salaried employees with direct deposit.",
                ["loans"],
                ["personal-finance", "salary-backed"],
                {"max_amount": 500000, "term_months": 60, "interest_rate": "18%"},
            ),
            (
                "Auto Loan",
                "Financing for new and used vehicle purchases.",
                ["loans", "auto"],
                ["personal-finance"],
                {"max_amount": 1500000, "term_months": 84, "interest_rate": "19%"},
            ),
            (
                "High-Yield Savings Account",
                "Everyday savings account with tiered interest based on balance.",
                ["savings"],
                ["liquid", "everyday"],
                {"interest_rate": "10%", "min_amount": 500},
            ),
            (
                "Youth Credit Card",
                "Entry-level credit card for first-time cardholders aged 21-30.",
                ["cards"],
                ["credit", "youth"],
                {"annual_fee": 0, "credit_limit_range": "5000-50000"},
            ),
            (
                "Home Renovation Loan",
                "Installment loan earmarked for home improvement projects.",
                ["loans", "home"],
                ["personal-finance"],
                {"max_amount": 300000, "term_months": 48, "interest_rate": "17.5%"},
            ),
        ]
        products = []
        for title, description, categories, tags, features in catalog:
            slug = title.lower().replace(" ", "-").replace("--", "-")
            product = Product.objects.create(
                title=title,
                description=description,
                categories=categories,
                tags=tags,
                features=features,
                external_link=f"{SEED_PRODUCT_LINK_PREFIX}{slug}",
                is_active=True,
            )
            ProblemStatement.objects.create(
                product=product,
                statement_text=f"Looking for {description[0].lower()}{description[1:]}",
            )
            products.append(product)
        return products

    def _seed_templates(self):
        specs = [
            ("National Bank of Egypt", "%d/%m/%Y"),
            ("Commercial International Bank", "%m/%d/%Y"),
            ("Banque Misr", "%d-%m-%Y"),
        ]
        templates = []
        for bank_name, date_format in specs:
            slug = bank_name.lower().replace(" ", "-")
            templates.append(
                BankStatementTemplate.objects.create(
                    bank_name=bank_name,
                    layout_signature=f"{SEED_TEMPLATE_SIGNATURE_PREFIX}{slug}-v1",
                    column_mapping_json={
                        "date": "Transaction Date",
                        "description": "Description",
                        "amount": "Amount",
                        "balance": "Balance",
                    },
                    date_format=date_format,
                )
            )
        return templates

    # ------------------------------------------------------------------
    # Per-user graph
    # ------------------------------------------------------------------

    def _seed_user_graph(self, index, templates, gen_statements):
        user = self._seed_user(index)
        UserPreference.objects.create(user=user)
        accounts = self._seed_bank_accounts(user)
        self._seed_consent(user)

        today = timezone.now().date()
        period_start = today - timedelta(days=120)

        statements_by_account = {}
        if gen_statements:
            for account in accounts:
                statements_by_account[account.id] = self._seed_statements(
                    user, account, templates, period_start, today
                )

        transactions_by_account = {}
        for account in accounts:
            transactions_by_account[account.id] = self._seed_transactions(
                user, account, statements_by_account.get(account.id, []), period_start, today
            )

        all_transactions = [t for txns in transactions_by_account.values() for t in txns]

        self._seed_recurring_charges(user, transactions_by_account)
        self._seed_anomalies(all_transactions)
        self._seed_pattern_insights(user, all_transactions)
        self._seed_monthly_summaries(user, transactions_by_account)
        self._seed_net_worth_snapshot(user, accounts, today)

        budget = self._seed_budget(user, goal_since=period_start)
        self._seed_conversations(user, all_transactions, statements_by_account)
        self._seed_feedback(user, all_transactions)

        return user, accounts, budget

    def _seed_user(self, index):
        return User.objects.create_user(
            email=f"{SEED_USER_EMAIL_PREFIX}{index}@example.com",
            password="SeedPass123!",
            name=f"Seed User {index}",
            phone=f"+2010{random.randint(10000000, 99999999)}",
            employment_status=random.choice(EMPLOYMENT_STATUSES),
            income_bracket=random.choice(INCOME_BRACKETS),
            monthly_income=_money(0, 8000, 45000),
            income_steadiness=random.choice(["steady", "variable"]),
            dependents_count=random.randint(0, 4),
            onboarding_date=timezone.now() - timedelta(days=random.randint(30, 400)),
            status="active",
        )

    def _seed_bank_accounts(self, user):
        num_accounts = random.randint(1, 2)
        banks = random.sample(BANKS, k=num_accounts)
        accounts = []
        for bank_name in banks:
            accounts.append(
                BankAccount.objects.create(
                    user=user,
                    bank_name=bank_name,
                    account_type=random.choice(["checking", "savings"]),
                    masked_account_number=f"****{random.randint(1000, 9999)}",
                    currency="EGP",
                    is_active=True,
                )
            )
        return accounts

    def _seed_consent(self, user):
        # A subset get an older granted+revoked pair first, exercising the
        # append-only grant/revoke timeline (Data_Governance_Specs.md §1)
        # rather than a single row.
        if random.random() < 0.3:
            old_grant = timezone.now() - timedelta(days=300)
            ConsentRecord.objects.create(
                user=user,
                consent_type="data_processing",
                policy_version="v1.0",
                granted_at=old_grant,
                revoked_at=old_grant + timedelta(days=60),
            )
        for consent_type in CONSENT_TYPES:
            ConsentRecord.objects.create(
                user=user,
                consent_type=consent_type,
                policy_version="v2.0",
                granted_at=timezone.now() - timedelta(days=random.randint(1, 200)),
                revoked_at=None,
            )

    # ------------------------------------------------------------------
    # Statements (only when --gen_statements)
    # ------------------------------------------------------------------

    def _seed_statements(self, user, account, templates, period_start, today):
        midpoint = period_start + (today - period_start) / 2
        halves = [(period_start, midpoint), (midpoint, today)]
        statement_files = []
        matching_templates = [t for t in templates if t.bank_name == account.bank_name]
        for start, end in halves:
            template = random.choice(matching_templates or templates)
            file_type = random.choice(["pdf", "jpg", "png"])
            statement_file = StatementFile.objects.create(
                user=user,
                account=account,
                template=template,
                seaweed_file_id=f"seed-placeholder-{uuid.uuid4()}",
                checksum=uuid.uuid4().hex + uuid.uuid4().hex,
                file_size=random.randint(80_000, 2_000_000),  # realistic statement size in bytes
                file_type=file_type,
                status="approved",
                start_transaction_date=start,
                last_transaction_date=end,
            )
            StatementOcrResult.objects.create(
                statement=statement_file,
                seaweed_file_id=statement_file.seaweed_file_id,
                ocr_engine="MinerU",
                confidence_score=_money(0, 0.85, 0.99),
            )
            StatementNormalized.objects.create(
                statement=statement_file,
                # Mirrors the real normalization shape (bank_name/account_hint
                # feed StatementFileSerializer's inline metadata fields) — these
                # statements are already `processed`, so the ledger, not this
                # payload, is the source of the transaction rows.
                normalized_json={
                    "bank_name": account.bank_name,
                    "account_hint": account.masked_account_number,
                    "period_start": start.isoformat(),
                    "period_end": end.isoformat(),
                    "note": "Synthetic seed data — no backing file in SeaweedFS.",
                },
                model_used="seed-mock-llm",
            )
            statement_files.append(statement_file)
        return statement_files

    # ------------------------------------------------------------------
    # Aggregations — the ledger
    # ------------------------------------------------------------------

    def _seed_transactions(self, user, account, statement_files, period_start, today):
        seen_keys = set()
        rows = []  # list of dicts describing a transaction before balance is assigned

        def add_row(
            txn_date, merchant_raw, merchant_normalized, category, txn_type, amount, is_recurring
        ):
            key = (txn_date, amount, merchant_raw)
            if key in seen_keys:
                # Nudge the amount by a few cents to keep the composite
                # uniqueness constraint (user, account, date, amount, merchant_raw) intact.
                amount = amount + Decimal("0.13")
                key = (txn_date, amount, merchant_raw)
            seen_keys.add(key)
            rows.append(
                {
                    "transaction_date": txn_date,
                    "merchant_raw": merchant_raw,
                    "merchant_normalized": merchant_normalized,
                    "category": category,
                    "transaction_type": txn_type,
                    "amount": amount,
                    "is_recurring": is_recurring,
                }
            )

        # Monthly salary credit — gives StabilityScoreView/SpendingPatternInsight
        # real inflow history to compute against.
        month_starts = []
        cursor = period_start.replace(day=1)
        while cursor <= today:
            month_starts.append(cursor)
            cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)

        for month_start in month_starts:
            pay_day = min(28, month_start.day + 4)
            pay_date = month_start.replace(day=pay_day)
            if period_start <= pay_date <= today:
                add_row(
                    pay_date,
                    "EMPLOYER PAYROLL",
                    "Employer Payroll",
                    "other",
                    "credit",
                    _money(0, float(user.monthly_income) * 0.97, float(user.monthly_income) * 1.03),
                    True,
                )

        # Recurring merchants — same cadence used to derive RecurringCharge below.
        for (
            merchant_raw,
            merchant_normalized,
            category,
            txn_type,
            amount_range,
        ) in RECURRING_MERCHANTS:
            for month_start in month_starts:
                charge_day = min(28, month_start.day + random.randint(1, 10))
                charge_date = month_start.replace(day=charge_day)
                if period_start <= charge_date <= today:
                    add_row(
                        charge_date,
                        merchant_raw,
                        merchant_normalized,
                        category,
                        txn_type,
                        _money(0, *amount_range),
                        True,
                    )

        # One-off transactions spread across the period.
        num_one_off = random.randint(15, 30)
        span_days = (today - period_start).days
        for _ in range(num_one_off):
            merchant_raw, merchant_normalized, category, txn_type, amount_range = random.choice(
                ONE_OFF_MERCHANTS
            )
            txn_date = period_start + timedelta(days=random.randint(0, span_days))
            add_row(
                txn_date,
                merchant_raw,
                merchant_normalized,
                category,
                txn_type,
                _money(0, *amount_range),
                False,
            )

        rows.sort(key=lambda r: r["transaction_date"])

        running_balance = _money(0, 5000, 40000)
        transactions = []
        for row in rows:
            if row["transaction_type"] == "credit":
                running_balance += row["amount"]
            else:
                running_balance -= row["amount"]

            statement, source = None, "manual"
            if statement_files:
                covering = [
                    s
                    for s in statement_files
                    if s.start_transaction_date
                    <= row["transaction_date"]
                    <= s.last_transaction_date
                ]
                # A subset land as statement-sourced; the rest stay manual —
                # a realistic mix, not "every transaction came from a file".
                if covering and random.random() < 0.7:
                    statement, source = random.choice(covering), "statement"

            transactions.append(
                Transaction.objects.create(
                    user=user,
                    account=account,
                    statement=statement,
                    transaction_date=row["transaction_date"],
                    merchant_raw=row["merchant_raw"],
                    merchant_normalized=row["merchant_normalized"],
                    category=self._categories[row["category"]],
                    amount=row["amount"],
                    currency=account.currency,
                    is_recurring=row["is_recurring"],
                    confidence_score=_money(0, 0.9, 0.99) if source == "statement" else None,
                    source=source,
                    balance=running_balance,
                    transaction_type=row["transaction_type"],
                )
            )
        return transactions

    def _seed_recurring_charges(self, user, transactions_by_account):
        for account_id, transactions in transactions_by_account.items():
            by_merchant = {}
            for txn in transactions:
                # Payroll is recurring but is not a *charge*: identified by its
                # transaction_type, not its category (this seed data still tags
                # payroll rows "other" rather than the newer "salary" category).
                if not txn.is_recurring or txn.transaction_type == "credit":
                    continue
                by_merchant.setdefault(txn.merchant_normalized, []).append(txn)
            for merchant_normalized, txns in by_merchant.items():
                if len(txns) < 2:
                    continue
                txns.sort(key=lambda t: t.transaction_date)
                avg_amount = sum((t.amount for t in txns), Decimal("0")) / len(txns)
                last_occurrence = txns[-1].transaction_date
                RecurringCharge.objects.create(
                    user=user,
                    account_id=account_id,
                    merchant_normalized=merchant_normalized,
                    frequency="monthly",
                    avg_amount=_money(avg_amount),
                    last_occurrence_date=last_occurrence,
                    next_expected_date=last_occurrence + timedelta(days=30),
                )

    def _seed_anomalies(self, all_transactions):
        debits = [t for t in all_transactions if t.transaction_type in ("debit", "fee")]
        if not debits:
            return
        outliers = sorted(debits, key=lambda t: t.amount, reverse=True)[:2]
        for txn in outliers:
            AnomalyFlag.objects.create(
                transaction=txn,
                reason=(
                    f"Amount of {txn.amount} {txn.currency} at "
                    f"{txn.merchant_normalized or txn.merchant_raw} is well above this "
                    "user's typical spend for the category."
                ),
                severity=random.choice(["medium", "high"]),
                resolved=random.random() < 0.3,
            )

    def _seed_pattern_insights(self, user, all_transactions):
        by_month = {}
        for txn in all_transactions:
            key = txn.transaction_date.replace(day=1)
            bucket = by_month.setdefault(key, {"inflow": Decimal("0"), "outflow": Decimal("0")})
            if txn.transaction_type == "credit":
                bucket["inflow"] += txn.amount
            else:
                bucket["outflow"] += txn.amount

        monthly_series = [
            {
                "month": month.strftime("%Y-%m"),
                "inflow": _json_money(v["inflow"]),
                "outflow": _json_money(v["outflow"]),
            }
            for month, v in sorted(by_month.items())
        ]
        SpendingPatternInsight.objects.create(
            user=user,
            insight_type="cash_flow",
            period="last_4_months",
            value_json={"monthly": monthly_series},
        )

        inflows = [v["inflow"] for _, v in sorted(by_month.items())]
        if len(inflows) >= 2:
            mean = sum(float(i) for i in inflows) / len(inflows)
            variance = sum((float(i) - mean) ** 2 for i in inflows) / len(inflows)
            cv = (variance**0.5 / mean) if mean else 1.0
            score = max(0.0, min(1.0, round(1 - cv, 2)))
            trend = (
                "stable" if cv < 0.15 else "declining" if inflows[-1] < inflows[0] else "improving"
            )
            SpendingPatternInsight.objects.create(
                user=user,
                insight_type="income_stability",
                period="last_6_months",
                value_json={"score": score, "trend": trend},
            )

    def _seed_monthly_summaries(self, user, transactions_by_account):
        for account_id, transactions in transactions_by_account.items():
            by_month = {}
            for txn in transactions:
                key = txn.transaction_date.replace(day=1)
                by_month.setdefault(key, []).append(txn)

            for month, txns in by_month.items():
                total_spend = sum(
                    (t.amount for t in txns if t.transaction_type in ("debit", "fee")), Decimal("0")
                )
                total_inflow = sum(
                    (t.amount for t in txns if t.transaction_type == "credit"), Decimal("0")
                )
                category_breakdown = {}
                for t in txns:
                    if t.category is None:
                        continue
                    category_breakdown[t.category.name] = (
                        category_breakdown.get(t.category.name, Decimal("0")) + t.amount
                    )
                merchant_totals = {}
                for t in txns:
                    name = t.merchant_normalized or t.merchant_raw
                    if name is None:
                        continue
                    merchant_totals[name] = merchant_totals.get(name, Decimal("0")) + t.amount
                top_merchants = sorted(merchant_totals.items(), key=lambda kv: kv[1], reverse=True)[
                    :5
                ]

                MonthlySummary.objects.create(
                    user=user,
                    account_id=account_id,
                    month=month,
                    total_spend=total_spend,
                    total_inflow=total_inflow,
                    category_breakdown_json={
                        k: _json_money(v) for k, v in category_breakdown.items()
                    },
                    top_merchants_json=[
                        {"merchant": name, "total": _json_money(total)}
                        for name, total in top_merchants
                    ],
                )

    def _seed_net_worth_snapshot(self, user, accounts, today):
        per_account = []
        total = Decimal("0")
        for account in accounts:
            latest = (
                Transaction.objects.filter(account=account)
                .order_by("-transaction_date", "-created_at")
                .first()
            )
            balance = latest.balance if latest and latest.balance is not None else Decimal("0")
            total += balance
            per_account.append(
                {
                    "account_id": str(account.id),
                    "bank_name": account.bank_name,
                    "balance": _json_money(balance),
                }
            )
        NetWorthSnapshot.objects.create(
            user=user,
            as_of_date=today,
            total_across_accounts=total,
            per_account_breakdown_json=per_account,
        )

    # ------------------------------------------------------------------
    # Budgets
    # ------------------------------------------------------------------

    def _seed_budget(self, user, goal_since=None):
        budget = Budget.objects.create(
            user=user,
            name="My Plan",
            period_type="monthly",
            status="active",
        )
        # Goal is its own entity now, one-to-one with User (PLAN.md
        # Checkpoint C) — created independently of the budget, not as
        # embedded fields on it.
        goal = Goal.objects.create(
            user=user,
            name="Emergency Fund",
            target_amount=_money(0, 20000, 100000),
            timeline_months=random.choice([6, 12, 18, 24]),
        )
        if goal_since is not None:
            # Backdated to the same period_start the transaction history
            # uses, via a queryset update (auto_now_add ignores any value
            # passed to .create()/.save()) — so a fresh seed_db run
            # immediately shows real, non-zero savings progress instead of
            # 0% until new transactions post after "right now." This is also
            # the concrete regression check for the savings-progress-
            # always-zero bug _goal_progress()'s docstring explains.
            goal_created_at = timezone.make_aware(datetime.combine(goal_since, datetime.min.time()))
            Goal.objects.filter(pk=goal.pk).update(created_at=goal_created_at)
        # One row per category, summing to 100 — the same six the starter templates
        # allocate across, so seeded spend actually shows up against the plan.
        allocation_plan = [
            ("housing", Decimal("30.00")),
            ("food", Decimal("20.00")),
            ("transport", Decimal("15.00")),
            ("savings", Decimal("15.00")),
            ("lifestyle", Decimal("12.00")),
            ("other", Decimal("8.00")),
        ]
        for category, percentage in allocation_plan:
            BudgetAllocation.objects.create(
                budget=budget,
                category=self._categories[category],
                allocated_percentage=percentage,
                allocated_amount=_money(user.monthly_income * percentage / Decimal("100")),
                currency="EGP",
            )

        # A prior-state snapshot so planned-vs-actual history isn't empty on
        # first look (Budgets domain versions the previous row before an edit).
        previous_allocations = [
            ("housing", Decimal("35.00")),
            ("food", Decimal("18.00")),
            ("transport", Decimal("12.00")),
            ("savings", Decimal("15.00")),
            ("lifestyle", Decimal("12.00")),
            ("other", Decimal("8.00")),
        ]
        BudgetHistory.objects.create(
            budget=budget,
            previous_values={
                "name": "My Plan",
                "allocations": [
                    {"category": c, "allocated_percentage": _json_money(p)}
                    for c, p in previous_allocations
                ],
            },
            changed_via=random.choice(["dashboard", "chat"]),
        )
        return budget

    # ------------------------------------------------------------------
    # Conversations
    # ------------------------------------------------------------------

    def _seed_conversations(self, user, all_transactions, statements_by_account):
        num_conversations = random.randint(1, 2)
        sample_transaction = random.choice(all_transactions) if all_transactions else None
        statement_files = [s for group in statements_by_account.values() for s in group]
        sample_statement = random.choice(statement_files) if statement_files else None

        for _ in range(num_conversations):
            conversation = Conversation.objects.create(user=user, status="active")
            exchanges = [
                ("user", "general", "How much did I spend on groceries last month?", None),
                (
                    "assistant",
                    "general",
                    "You spent about 1,450 EGP on groceries last month, mostly at Carrefour "
                    "and Spinneys.",
                    None,
                ),
                ("user", "budget_review", "Can you adjust my budget to save more?", None),
                (
                    "assistant",
                    "budget_review",
                    "Here's an adjusted allocation you can confirm or tweak.",
                    {
                        "type": "allocation_slider",
                        "allocations": [
                            {"category": "savings", "allocated_percentage": 20},
                            {"category": "lifestyle", "allocated_percentage": 5},
                        ],
                    },
                ),
            ]
            created_messages = []
            for sender, stage, content, widget_json in exchanges:
                message = Message.objects.create(
                    conversation=conversation,
                    sender=sender,
                    content=content,
                    stage=stage,
                    widget_json=widget_json,
                )
                created_messages.append(message)

            if sample_transaction is not None:
                created_messages[0].add_reference("transaction", sample_transaction.id)
            if sample_statement is not None:
                MessageReference.objects.create(
                    message=created_messages[-1],
                    target_type="statement",
                    target_id=sample_statement.id,
                )

    # ------------------------------------------------------------------
    # Feedback
    # ------------------------------------------------------------------

    def _seed_feedback(self, user, all_transactions):
        sample = random.sample(all_transactions, k=min(3, len(all_transactions)))
        for txn in sample:
            Reaction.objects.create(
                user=user,
                target_type="transaction",
                target_id=txn.id,
                rating=random.randint(1, 5),
                comment=random.choice(
                    [None, "Categorised correctly.", "Not sure this category is right."]
                ),
            )
        if random.random() < 0.3:
            ReportedIssue.objects.create(
                user=user,
                description="A transaction from my statement seems to be missing.",
                status="open",
            )

    # ------------------------------------------------------------------
    # Recommendation
    # ------------------------------------------------------------------

    def _seed_recommendation_logs(self, users_with_accounts, products):
        if not products:
            return
        for user, _accounts, _budget in users_with_accounts:
            for _ in range(random.randint(1, 3)):
                product = random.choice(products)
                RecommendationLog.objects.create(
                    user=user,
                    product=product,
                    matched_query="How can I grow my savings?",
                    similarity_score=_money(0, 0.55, 0.95).quantize(Decimal("0.0001")),
                )
