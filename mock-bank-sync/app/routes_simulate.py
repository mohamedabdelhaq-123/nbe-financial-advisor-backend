"""Dev/demo trigger routes: make it look like a real bank event happened.

No auth is enforced here on purpose — these exist purely to drive demos and
local dev seeding (spinning up test bank customers, firing a "new
transaction just landed" webhook at the Django backend on demand). They are
not part of the real bank-linking flow a customer would ever hit, so they
don't carry the customer JWT, and there's no per-customer data to protect
behind the internal secret either. If this service is ever exposed outside
a trusted dev/demo network, gate these behind MOCK_BANK_INTERNAL_SECRET too.
"""

import random
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import requests
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import config
from app.db import get_db
from app.models import MockAccount, MockCustomer, MockTransaction
from app.routes_accounts import _serialize_account, _serialize_transaction

router = APIRouter(prefix="/simulate", tags=["simulate"])

_SAMPLE_MERCHANTS = [
    "Carrefour",
    "Talabat",
    "Uber",
    "Vodafone",
    "Amazon.eg",
    "Cairo Metro",
]
_SAMPLE_TRANSACTION_TYPES = ["debit", "credit"]


class SimulateTransactionRequest(BaseModel):
    account_id: str | None = None
    amount: Decimal | None = None
    merchant: str | None = None
    transaction_type: str | None = None
    transaction_date: datetime | None = None


class SimulateAccountRequest(BaseModel):
    bank_name: str | None = None
    account_type: str | None = None
    masked_account_number: str | None = None
    currency: str | None = None


class SimulateCustomerRequest(BaseModel):
    customer_bank_id: str
    email: str
    name: str | None = None
    accounts: list[SimulateAccountRequest] | None = None


def _random_amount() -> Decimal:
    return Decimal(str(round(random.uniform(5, 2500), 2)))


@router.post("/transaction", status_code=status.HTTP_201_CREATED)
def simulate_transaction(body: SimulateTransactionRequest, db: Session = Depends(get_db)):
    """Dev/demo trigger: records a new mock transaction and pushes it to
    the Django backend's webhook, simulating a live bank event."""
    # 1. Resolve the target account.
    if body.account_id:
        try:
            account_uuid = uuid.UUID(body.account_id)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
        account = db.query(MockAccount).filter(MockAccount.id == account_uuid).first()
        if account is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    else:
        candidates = db.query(MockAccount).all()
        if not candidates:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No mock accounts exist yet — seed one via POST /simulate/customer first",
            )
        account = random.choice(candidates)

    # 2. Insert the new transaction into the mock ledger.
    transaction = MockTransaction(
        id=uuid.uuid4(),
        account_id=account.id,
        transaction_date=body.transaction_date or datetime.now(timezone.utc),
        merchant=body.merchant or random.choice(_SAMPLE_MERCHANTS),
        amount=body.amount if body.amount is not None else _random_amount(),
        transaction_type=body.transaction_type or random.choice(_SAMPLE_TRANSACTION_TYPES),
        balance=None,
    )
    db.add(transaction)
    db.commit()
    db.refresh(transaction)

    transaction_payload = _serialize_transaction(transaction, account.currency)

    # 3. Build and 4. push the webhook payload to the Django backend. A
    # failed push does NOT fail this request — the mock-side write already
    # happened; the caller just needs to know delivery status.
    webhook_payload = {
        "provider_slug": "mock_bank",
        "external_account_id": str(account.id),
        "transactions": [transaction_payload],
    }
    webhook_delivery = _deliver_webhook(webhook_payload)

    return {**transaction_payload, "webhook_delivery": webhook_delivery}


def _deliver_webhook(payload: dict) -> dict:
    try:
        response = requests.post(
            config.BACKEND_WEBHOOK_URL,
            json=payload,
            headers={"X-Webhook-Secret": config.webhook_secret()},
            timeout=5,
        )
        return {
            "success": 200 <= response.status_code < 300,
            "status_code": response.status_code,
            "error": None if response.ok else response.text[:500],
        }
    except requests.RequestException as exc:
        return {"success": False, "status_code": None, "error": str(exc)}


@router.post("/customer", status_code=status.HTTP_201_CREATED)
def simulate_customer(body: SimulateCustomerRequest, db: Session = Depends(get_db)):
    """Dev/demo trigger: seeds a new mock bank customer and starter
    account(s), for exercising the linking flow without touching the DB directly."""
    existing = (
        db.query(MockCustomer)
        .filter(MockCustomer.customer_bank_id == body.customer_bank_id)
        .first()
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"customer_bank_id={body.customer_bank_id!r} already exists",
        )

    customer = MockCustomer(
        id=uuid.uuid4(),
        customer_bank_id=body.customer_bank_id,
        email=body.email,
        name=body.name,
    )
    db.add(customer)
    db.flush()  # assign customer.id to FKs below without a full commit yet

    account_specs = body.accounts or [SimulateAccountRequest()]
    accounts = []
    for spec in account_specs:
        account = MockAccount(
            id=uuid.uuid4(),
            customer_id=customer.id,
            bank_name=spec.bank_name or "Mock National Bank",
            account_type=spec.account_type or "checking",
            masked_account_number=spec.masked_account_number or "****0000",
            currency=spec.currency or "EGP",
        )
        db.add(account)
        accounts.append(account)

    db.commit()
    for account in accounts:
        db.refresh(account)

    return {
        "customer_id": str(customer.id),
        "customer_bank_id": customer.customer_bank_id,
        "email": customer.email,
        "name": customer.name,
        "accounts": [_serialize_account(account) for account in accounts],
    }


@router.delete("/customer/{customer_bank_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_simulated_customer(customer_bank_id: str, db: Session = Depends(get_db)):
    """Dev/demo cleanup: removes a customer and its accounts/transactions
    (SQLAlchemy cascade="all, delete-orphan" on both relationships in
    app/models.py handles the accounts/transactions automatically) —
    lets a test that seeds via POST /simulate/customer clean up after
    itself instead of leaving data other tests' account-count assumptions
    would otherwise see."""
    customer = (
        db.query(MockCustomer).filter(MockCustomer.customer_bank_id == customer_bank_id).first()
    )
    if customer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found")
    db.delete(customer)
    db.commit()
