"""Routes the Django backend calls (using the JWT mock-bank-oauth issued)
to pull a linked customer's accounts and transactions."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.auth import require_customer
from app.db import get_db
from app.models import MockAccount, MockTransaction

router = APIRouter(prefix="/accounts", tags=["accounts"])


def _parse_uuid(
    value: str, *, field: str, status_code: int = status.HTTP_401_UNAUTHORIZED
) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=status_code, detail=f"Invalid {field}")


def _serialize_account(account: MockAccount) -> dict:
    return {
        "external_account_id": str(account.id),
        "bank_name": account.bank_name,
        "account_type": account.account_type,
        "masked_account_number": account.masked_account_number,
        "currency": account.currency,
    }


def _serialize_transaction(transaction: MockTransaction, currency: str) -> dict:
    return {
        "external_transaction_id": str(transaction.id),
        # Date-only: the Django side's Transaction.transaction_date is a
        # DateField (no time component), and its webhook serializer
        # rejects a full datetime string — this mock ledger tracks a full
        # timestamp internally, but only the date crosses the wire.
        "transaction_date": transaction.transaction_date.date().isoformat(),
        "merchant_raw": transaction.merchant,
        "amount": str(transaction.amount),
        "transaction_type": transaction.transaction_type,
        "currency": currency,
        "balance": str(transaction.balance) if transaction.balance is not None else None,
    }


@router.get("")
def list_accounts(customer_id: str = Depends(require_customer), db: Session = Depends(get_db)):
    """Lists every mock account belonging to the Bearer token's customer."""
    customer_uuid = _parse_uuid(customer_id, field="token subject")
    accounts = db.query(MockAccount).filter(MockAccount.customer_id == customer_uuid).all()
    return [_serialize_account(account) for account in accounts]


@router.get("/{account_id}/transactions")
def list_transactions(
    account_id: str,
    since: datetime | None = Query(default=None),
    customer_id: str = Depends(require_customer),
    db: Session = Depends(get_db),
):
    """Lists an account's transactions, optionally filtered to since a given
    date. 404s if the account doesn't belong to the Bearer token's customer."""
    customer_uuid = _parse_uuid(customer_id, field="token subject")
    # 404, not 401: a malformed account_id is a bad request about a resource,
    # not an authentication problem — and matches the "no existence leak"
    # 404 already returned below for an account belonging to someone else.
    account_uuid = _parse_uuid(
        account_id, field="account_id", status_code=status.HTTP_404_NOT_FOUND
    )

    # Scope the lookup to the JWT's customer_id so an account belonging to a
    # different customer 404s exactly like a nonexistent account — no
    # existence leak across customers.
    account = (
        db.query(MockAccount)
        .filter(
            MockAccount.id == account_uuid,
            MockAccount.customer_id == customer_uuid,
        )
        .first()
    )
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    query = db.query(MockTransaction).filter(MockTransaction.account_id == account.id)
    if since is not None:
        query = query.filter(MockTransaction.transaction_date >= since)

    transactions = query.order_by(MockTransaction.transaction_date).all()
    return [_serialize_transaction(t, account.currency) for t in transactions]
