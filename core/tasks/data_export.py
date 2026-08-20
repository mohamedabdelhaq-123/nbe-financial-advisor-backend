"""
"Request my account data" (profile page's Account Management section) —
gathers everything the product stores about one user into a single JSON
document and emails it as an attachment. Runs off-request via Celery (see
core/views/profile.py's MeDataExportView): walking the user's full
transaction history isn't bounded the way most request/response work here
is, so it shouldn't hold the HTTP request open.
"""

from celery import shared_task

from services import notification_service


def _gather_user_data(user) -> dict:
    """Everything the product stores that's reachable from `user` — every
    field here is either owned directly by the user or scoped to them via a
    FK, so no cross-user data can leak through this regardless of what's
    added to the schema later, as long as new querysets stay filtered the
    same way."""
    from core.models import Budget, ConsentRecord, Goal, Reaction, ReportedIssue, Transaction

    data = {
        "account": {
            "id": str(user.id),
            "name": user.name,
            "email": user.email,
            "phone": user.phone,
            "employment_status": user.employment_status,
            "monthly_income": (
                float(user.monthly_income) if user.monthly_income is not None else None
            ),
            "income_steadiness": user.income_steadiness,
            "dependents_count": user.dependents_count,
            "created_at": user.created_at.isoformat(),
        },
        "budget": None,
        "goal": None,
        "bank_accounts": [],
        "transactions": [],
        "consent_records": [],
        "reported_issues": [],
        "feedback": [],
    }

    budget = Budget.objects.filter(user=user).prefetch_related("allocations").first()
    if budget:
        data["budget"] = {
            "name": budget.name,
            "period_type": budget.period_type,
            "status": budget.status,
            "selected_template_key": budget.selected_template_key,
            "allocations": [
                {
                    "category": a.category.name,
                    "allocated_percentage": float(a.allocated_percentage),
                    "allocated_amount": float(a.allocated_amount),
                    "currency": a.currency,
                }
                for a in budget.allocations.all()
            ],
        }

    goal = Goal.objects.filter(user=user).first()
    if goal:
        data["goal"] = {
            "name": goal.name,
            "target_amount": float(goal.target_amount),
            "timeline_months": goal.timeline_months,
            "created_at": goal.created_at.isoformat(),
        }

    data["bank_accounts"] = [
        {
            "id": str(a.id),
            "bank_name": a.bank_name,
            "account_type": a.account_type,
            "account_number": a.account_number,
            "currency": a.currency,
            "is_active": a.is_active,
            "link_type": a.link_type,
            "created_at": a.created_at.isoformat(),
        }
        for a in user.bank_accounts.all()
    ]

    data["transactions"] = [
        {
            "id": str(t.id),
            "date": t.transaction_date.isoformat(),
            "merchant": t.merchant_normalized or t.merchant_raw,
            "category": t.category.name if t.category else None,
            "amount": float(t.amount),
            "currency": t.currency,
            "transaction_type": t.transaction_type,
            "source": t.source,
        }
        for t in Transaction.objects.filter(user=user)
        .select_related("category")
        .order_by("-transaction_date")
    ]

    data["consent_records"] = [
        {
            "consent_type": c.consent_type,
            "policy_version": c.policy_version,
            "granted_at": c.granted_at.isoformat() if c.granted_at else None,
            "revoked_at": c.revoked_at.isoformat() if c.revoked_at else None,
            "created_at": c.created_at.isoformat(),
        }
        for c in ConsentRecord.objects.filter(user=user).order_by("created_at")
    ]

    data["reported_issues"] = [
        {
            "description": i.description,
            "status": i.status,
            "created_at": i.created_at.isoformat(),
            "resolved_at": i.resolved_at.isoformat() if i.resolved_at else None,
        }
        for i in ReportedIssue.objects.filter(user=user).order_by("created_at")
    ]

    data["feedback"] = [
        {
            "target_type": r.target_type,
            "target_id": str(r.target_id),
            "rating": r.rating,
            "comment": r.comment,
            "created_at": r.created_at.isoformat(),
        }
        for r in Reaction.objects.filter(user=user).order_by("created_at")
    ]

    return data


@shared_task
def send_account_data_export(user_id) -> None:
    import json

    from core.models import User

    try:
        user = User.objects.prefetch_related("bank_accounts").get(id=user_id)
    except User.DoesNotExist:
        # The account was deleted (or never existed) between the request
        # that enqueued this task and the worker picking it up — nothing
        # left to export to nobody.
        return

    export_json = json.dumps(_gather_user_data(user), indent=2).encode()

    # notify() renders the same branded emails/notification.html card every
    # other notification in the product uses (budget changes, anomalies,
    # password reset's plain-text sibling templates aside) — best-effort
    # swallow of a send failure is handled there too, not repeated here.
    notification_service.notify(
        user,
        "Your account data export",
        "Attached is a full export of the data we hold on your account, "
        "as you requested. If you didn't request this, please contact "
        "support.",
        attachments=[("account-data-export.json", export_json, "application/json")],
    )
