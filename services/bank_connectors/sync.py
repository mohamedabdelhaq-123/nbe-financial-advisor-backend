"""
Applies a connector's fetch_accounts() result to a BankConnection — the one
shared step behind every path that lands synced accounts: the authenticated
"link a bank" flow (core/views/bank_connections.py), bank login
(core/views/auth.py), and the inbound sync webhook's new-account discovery
(core/views/webhooks.py). Centralizing it here means the manual-data cleanup
below behaves identically no matter which of those three call sites
established or refreshed the connection.
"""

from django.db import transaction

from core.models import BankAccount
from core.tasks.bank_sync import ingest_synced_transactions
from services.bank_connectors.base import BankConnectorError


def apply_synced_accounts(connection, accounts: list[dict], connector) -> list[BankAccount]:
    """
    Given a BankConnection (already carrying a valid access_token) and the
    accounts list connector.fetch_accounts() just returned, clears out any
    manual account shadowing the same bank and lands the real, synced
    accounts in its place, then kicks off a best-effort transaction backfill
    for each.

    All accounts from one connector call are assumed to share one
    real-world bank (mock-bank-sync's own MockAccount.bank_name is fixed
    per customer) — the first account's bank_name is treated as that
    connection's canonical identity for the manual-data cleanup below.
    """
    with transaction.atomic():
        if accounts:
            bank_name = accounts[0]["bank_name"]
            # The bank is now the verified source of truth for its own
            # name — a pre-existing manual account under the same name
            # (and its transactions, via the model's existing
            # on_delete=CASCADE) is superseded, not left alongside.
            BankAccount.objects.filter(
                user=connection.user,
                link_type=BankAccount.LINK_TYPE_MANUAL,
                bank_name__iexact=bank_name,
            ).delete()

        created_accounts = []
        for acct in accounts:
            bank_account, _ = BankAccount.objects.update_or_create(
                connection=connection,
                external_account_id=acct["external_account_id"],
                defaults={
                    "user": connection.user,
                    "link_type": BankAccount.LINK_TYPE_SYNCED,
                    "bank_name": acct["bank_name"],
                    "account_type": acct.get("account_type"),
                    "masked_account_number": acct["masked_account_number"],
                    "currency": acct.get("currency", "EGP"),
                },
            )
            created_accounts.append(bank_account)

    for bank_account, acct in zip(created_accounts, accounts):
        # Best-effort: the account is already correctly persisted above
        # regardless of whether this backfill succeeds — ongoing sync
        # pushes (BankSyncWebhookView) populate it either way.
        try:
            transactions = connector.fetch_transactions(
                connection.access_token, acct["external_account_id"]
            )
        except BankConnectorError:
            continue
        if transactions:
            ingest_synced_transactions.delay(str(bank_account.id), transactions)

    return created_accounts
