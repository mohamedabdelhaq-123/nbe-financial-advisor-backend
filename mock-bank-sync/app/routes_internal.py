"""Internal, service-to-service routes — called only by mock-bank-oauth.

Protected by a shared secret (X-Internal-Secret), not the customer JWT: at
the point mock-bank-oauth calls this, no customer session/token exists yet
— it's still resolving *whether* a given bank-login identifier is a real
customer, as the step before it can send an OTP.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import require_internal_secret
from app.db import get_db
from app.models import MockCustomer

router = APIRouter(prefix="/internal", tags=["internal"])


@router.get("/customers/lookup", dependencies=[Depends(require_internal_secret)])
def lookup_customer(customer_bank_id: str, db: Session = Depends(get_db)):
    """Resolves an opaque bank-login identifier to a customer id, email, and
    name — the email is where mock-bank-oauth sends the OTP; both email and
    name flow on through to the Django backend for provisioning a user on a
    first-time bank login."""
    customer = (
        db.query(MockCustomer).filter(MockCustomer.customer_bank_id == customer_bank_id).first()
    )
    if customer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No mock customer found for customer_bank_id={customer_bank_id!r}",
        )

    return {"customer_id": str(customer.id), "email": customer.email, "name": customer.name}
