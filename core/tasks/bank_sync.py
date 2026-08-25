"""
Landing point for every synced (source="synced") transaction, whether it
arrived via the initial post-link backfill (BankConnectionCallbackView) or
an ongoing sync push (BankSyncWebhookView) — same "one way to change data,
one place data lands" shape as core/tasks/statements.py's
process_statement_pipeline being the only place statement-sourced rows
reach the ledger.
"""

from datetime import date

from celery import shared_task

from core.models import AnomalyFlag, BankAccount, Transaction
from core.models.categories.merchant_keywords import guess_category_from_merchant
from core.models.categories.resolution import resolve_category
from services import ai_service, event_bus, notification_service


@shared_task
def ingest_synced_transactions(bank_account_id: str, transactions: list[dict]) -> None:
    """
    `transactions` — a list of {"external_transaction_id", "transaction_date",
    "merchant_raw", "amount", "transaction_type", "currency", "balance"}, the
    shape services/bank_connectors/mock_bank.py's fetch_transactions() and
    core/serializers/bank_connections.py's BankSyncWebhookSerializer both
    produce.

    Re-fetches the account by id (task args must be JSON-serializable) —
    mirrors process_statement_pipeline's shape: do the work, then always
    publish an SSE event in `finally`, whether or not anything was created.
    """
    try:
        account = BankAccount.objects.select_related("user").get(id=bank_account_id)
    except BankAccount.DoesNotExist:
        return

    created = []
    for txn in transactions:
        # transaction_date arrives as an ISO string (JSON payload / task args
        # must be JSON-serializable) — parsed once here so the in-memory
        # Transaction instances below hold a real date, not a str. Django's
        # DateField only coerces on the way to/from the DB, not on the
        # instance itself, so affected_months' .strftime() below would
        # otherwise fail on a freshly-created (not re-fetched) instance.
        transaction_date = (
            txn["transaction_date"]
            if isinstance(txn["transaction_date"], date)
            else date.fromisoformat(txn["transaction_date"][:10])
        )
        if Transaction.is_duplicate(
            account.user_id, account.id, transaction_date, txn["amount"], txn.get("merchant_raw")
        ):
            continue
        merchant_raw = txn.get("merchant_raw")
        transaction_type = txn.get("transaction_type")
        created.append(
            Transaction.objects.create(
                user=account.user,
                account=account,
                source="synced",
                currency=txn.get("currency") or account.currency,
                transaction_date=transaction_date,
                merchant_raw=merchant_raw,
                # No LLM pass runs over synced transactions the way it does
                # for statement ones — guess_category_from_merchant() is this
                # path's substitute source of a raw category guess, resolved
                # the same way an LLM's guess would be.
                category=resolve_category(
                    guess_category_from_merchant(merchant_raw, transaction_type),
                    transaction_type,
                ),
                amount=txn["amount"],
                transaction_type=transaction_type,
                balance=txn.get("balance"),
            )
        )

    # The ledger rows are committed at this point, so notify the browser now.
    # Post-ingestion AI analysis below is optional and can take several
    # seconds; making the transaction event wait for it leaves the visible
    # transaction list stale even though the new rows are already readable.
    event_bus.publish_user_event(
        account.user_id,
        "transaction_synced",
        {
            "account_id": str(account.id),
            "count": len(created),
            "transaction_ids": [str(t.id) for t in created],
        },
    )

    if not created:
        return

    anomalies_found = []
    try:
        affected_months = {t.transaction_date.strftime("%Y-%m") for t in created}
        client = ai_service.get_client()
        for month in affected_months:
            try:
                result = client.run_post_ingestion_analysis(
                    str(account.user_id), str(account.id), month
                )
            except ai_service.AIServiceError:
                # A missed analytics sweep isn't worth failing the whole
                # ingestion over — the transactions themselves are already
                # committed; this is best-effort enrichment on top.
                continue
            for anomaly in result.get("anomalies", []):
                anomaly_flag = AnomalyFlag.objects.create(
                    user=account.user,
                    account=account,
                    category=anomaly.get("category"),
                    month=f"{anomaly['month']}-01",
                    amount=anomaly.get("amount"),
                    reason=anomaly.get("reason", ""),
                    # The AI service's anomaly shape has no severity field
                    # (services/ai_service.py's mock doesn't return one) —
                    # "medium" is a placeholder default, not a real
                    # heuristic. Revisit once the real service defines one.
                    severity="medium",
                )
                anomalies_found.append(anomaly_flag)
                notification_service.notify(
                    account.user,
                    "Unusual activity detected",
                    f"We noticed unusual activity in '{anomaly_flag.category}' "
                    f"({anomaly_flag.amount} {account.currency}) on {account.bank_name}: "
                    f"{anomaly_flag.reason}",
                )

        notification_service.notify(
            account.user,
            "New transactions synced",
            f"{len(created)} new transaction(s) were synced from {account.bank_name}.",
        )
    finally:
        if anomalies_found:
            event_bus.publish_user_event(
                account.user_id,
                "anomaly_detected",
                {
                    "account_id": str(account.id),
                    "anomaly_ids": [str(a.id) for a in anomalies_found],
                },
            )
