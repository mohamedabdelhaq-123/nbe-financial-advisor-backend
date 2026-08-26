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
from html import escape

import requests
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import config
from app.db import get_db
from app.models import MockAccount, MockCustomer, MockTransaction
from app.routes_accounts import _serialize_account, _serialize_transaction

router = APIRouter(prefix="/simulate", tags=["simulate"])

_ACCOUNT_NUMBER_DIGITS = 17


def _generate_account_number() -> str:
    """A full account number for a simulated account, not a masked stub — the
    connector contract hands the real number through to the Django backend,
    so seeded customers need to exercise that shape."""
    return "".join(str(random.randint(0, 9)) for _ in range(_ACCOUNT_NUMBER_DIGITS))


_EXPENSE_MERCHANTS = [
    "Carrefour",
    "Talabat",
    "Uber",
    "Vodafone",
    "Amazon.eg",
    "Cairo Metro",
]
# Income-side merchant names, grouped by what they actually represent
# (payroll, freelance work, an incoming transfer) rather than drawn from the
# same pool as the expense merchants above — a random "Carrefour" credit
# reads as nonsense, and the Django side's categorizer
# (core/models/categories/merchant_keywords.py) would resolve it to an
# expense category (food) despite the money coming in. Picking the merchant
# from the pool that matches the chosen direction keeps every simulated
# transaction internally consistent.
_INCOME_MERCHANTS = [
    "ACME Corp Payroll",
    "Nile Software Payroll",
    "Cairo Consulting Salary",
    "Freelance Client Payment",
    "Upwork Freelance Payout",
    "Bank Transfer",
    "Family Transfer",
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
    account_number: str | None = None
    currency: str | None = None


class SimulateCustomerRequest(BaseModel):
    customer_bank_id: str
    email: str
    phone: str = Field(pattern=r"^\+[1-9]\d{7,14}$")
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

    # 2. Insert the new transaction into the mock ledger. Direction is
    # decided first so the merchant (when not caller-supplied) is drawn from
    # the pool that actually matches it — see _INCOME_MERCHANTS above.
    transaction_type = body.transaction_type or random.choice(_SAMPLE_TRANSACTION_TYPES)
    merchant_pool = _INCOME_MERCHANTS if transaction_type == "credit" else _EXPENSE_MERCHANTS
    transaction = MockTransaction(
        id=uuid.uuid4(),
        account_id=account.id,
        transaction_date=body.transaction_date or datetime.now(timezone.utc),
        merchant=body.merchant or random.choice(merchant_pool),
        amount=body.amount if body.amount is not None else _random_amount(),
        transaction_type=transaction_type,
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
        # Lets the backend discover-and-create a not-yet-known account
        # (opened at an already-linked bank after the initial sync) instead
        # of 404ing — see core/views/webhooks.py's BankSyncWebhookView.
        "external_customer_id": str(account.customer_id),
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


@router.get("/demo", response_class=HTMLResponse)
def demo_controls(db: Session = Depends(get_db)):
    """Presenter-friendly operator page for the real simulation endpoint.

    The page does not write to either database itself. Its form calls
    POST /simulate/transaction, which records the mock-bank ledger event and
    delivers the normal webhook to Django.
    """
    accounts = db.query(MockAccount).order_by(MockAccount.created_at, MockAccount.id).all()
    options = "".join(
        (
            f'<option value="{account.id}">'
            f"{escape(account.bank_name)} · "
            f"{escape(account.account_number or str(account.id))}</option>"
        )
        for account in accounts
    )
    empty_message = (
        ""
        if accounts
        else '<p class="warning">No mock accounts exist. Seed demo data before presenting.</p>'
    )
    submit_disabled = "" if accounts else " disabled"

    return HTMLResponse(f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Mock Bank Demo Controls</title>
  <style>
    body {{ font: 16px system-ui, sans-serif; max-width: 720px; margin: 3rem auto;
      padding: 0 1rem; color: #172033; }}
    form {{ display: grid; gap: 1rem; padding: 1.5rem;
      border: 1px solid #d7dce5; border-radius: 12px; }}
    label {{ display: grid; gap: .35rem; font-weight: 600; }}
    input, select, button {{ font: inherit; padding: .7rem; }}
    button {{ cursor: pointer; font-weight: 700; }}
    #result {{ margin-top: 1rem; padding: 1rem; border-radius: 8px;
      background: #f3f5f8; white-space: pre-wrap; }}
    .success {{ color: #087443; }} .failure, .warning {{ color: #a32929; }}
  </style>
</head>
<body>
  <h1>Mock Bank Demo Controls</h1>
  <p>Operator tool: this creates a real mock-ledger transaction and sends the
    normal webhook to the application.</p>
  {empty_message}
  <form id="transaction-form">
    <label>Account<select id="account-id" required>{options}</select></label>
    <label>Type<select id="transaction-type">
      <option value="debit">Expense</option>
      <option value="credit">Income</option>
    </select></label>
    <label>Merchant<input id="merchant" value="Carrefour Demo Purchase" required /></label>
    <label>Amount (EGP)<input id="amount" type="number" min="0.01" step="0.01"
      value="125.00" required /></label>
    <button id="submit" type="submit"{submit_disabled}>Send transaction</button>
  </form>
  <p>Reset: restore merchant to <code>Carrefour Demo Purchase</code> and amount
    to <code>125.00</code>. If webhook delivery fails, do not submit again:
    restore service health and reset/reseed the demo first, so a second ledger
    transaction is not created.</p>
  <div id="result" role="status" aria-live="polite">Ready.</div>
  <script>
    const form = document.getElementById("transaction-form");
    const button = document.getElementById("submit");
    const result = document.getElementById("result");
    form.addEventListener("submit", async (event) => {{
      event.preventDefault();
      button.disabled = true;
      result.className = "";
      result.textContent = "Sending through the mock-bank webhook path...";
      try {{
        const response = await fetch("/simulate/transaction", {{
          method: "POST",
          headers: {{ "Content-Type": "application/json" }},
          body: JSON.stringify({{
            account_id: document.getElementById("account-id").value,
            transaction_type: document.getElementById("transaction-type").value,
            merchant: document.getElementById("merchant").value,
            amount: document.getElementById("amount").value,
          }}),
        }});
        const payload = await response.json();
        if (!response.ok) {{
          throw new Error(payload.detail || `Request failed (${{response.status}})`);
        }}
        const delivered = payload.webhook_delivery && payload.webhook_delivery.success;
        result.className = delivered ? "success" : "failure";
        result.textContent = delivered
          ? `Delivered. Transaction ${{payload.external_transaction_id}}
             should now appear in the app.`
          : `Created in the mock ledger, but webhook delivery failed.
Do not submit again until service health and demo data are reset.\n${{
              JSON.stringify(payload.webhook_delivery, null, 2)
            }}`;
      }} catch (error) {{
        result.className = "failure";
        result.textContent = `Failed: ${{error instanceof Error ? error.message : String(error)}}`;
      }} finally {{
        button.disabled = false;
      }}
    }});
  </script>
</body>
</html>""")


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
        phone=body.phone,
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
            account_number=spec.account_number or _generate_account_number(),
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
        "phone": customer.phone,
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
