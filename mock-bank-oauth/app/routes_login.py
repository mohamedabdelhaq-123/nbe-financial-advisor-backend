"""POST /login/start and POST /login/verify — the mocked OTP login step.

/login/start resolves the opaque `customer_bank_id` the user typed into a
real customer identity by asking mock-bank-sync (which owns the customer
directory), then triggers an OTP email via the main Django backend's
internal notification endpoint.

/login/verify checks the submitted OTP and, on success, issues a short-lived
OAuth2 authorization code and redirects back to the caller's redirect_uri.

Route handlers here are plain `def` (not `async def`) because they make
blocking outbound calls via `requests`; FastAPI runs sync path operations in
a thread pool so this doesn't block the event loop.
"""

import html
from urllib.parse import urlencode

import requests
from fastapi import APIRouter, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from app import store
from app.config import (
    BACKEND_INTERNAL_URL,
    MOCK_BANK_INTERNAL_SECRET,
    MOCK_BANK_SERVICE_TOKEN,
    MOCK_BANK_SYNC_SERVICE_URL,
)

router = APIRouter()

_OUTBOUND_TIMEOUT_SECONDS = 10


def _error_page(message: str, status_code: int = 400) -> HTMLResponse:
    return HTMLResponse(
        f"<html><body><h1>Error</h1><p>{html.escape(message)}</p></body></html>",
        status_code=status_code,
    )


@router.post("/login/start")
def login_start(challenge_id: str = Form(...), customer_bank_id: str = Form(...)):
    """Resolves customer_bank_id via mock-bank-sync and, on success, emails
    an OTP through the main backend's notification endpoint."""
    challenge = store.get_challenge(challenge_id)
    if challenge is None:
        return _error_page("This login session is unknown or has expired.", status_code=404)

    if not customer_bank_id.strip():
        return _error_page("Customer ID is required.", status_code=400)

    # Resolve the opaque customer_bank_id to a real customer via mock-bank-sync,
    # the sibling service that owns the customer directory. This service
    # does not know who any customer is until mock-bank-sync tells it.
    try:
        lookup_response = requests.get(
            f"{MOCK_BANK_SYNC_SERVICE_URL}/internal/customers/lookup",
            params={"customer_bank_id": customer_bank_id},
            headers={"X-Internal-Secret": MOCK_BANK_INTERNAL_SECRET},
            timeout=_OUTBOUND_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        return _error_page(f"Could not reach the bank's customer directory: {exc}", status_code=502)

    if lookup_response.status_code == 404:
        return _error_page("No customer found with that ID.", status_code=404)
    if not lookup_response.ok:
        return _error_page(
            f"Customer directory lookup failed (status {lookup_response.status_code}).",
            status_code=502,
        )

    body = lookup_response.json()
    customer_id = body.get("customer_id")
    email = body.get("email")
    name = body.get("name")
    if not customer_id or not email:
        return _error_page("Customer directory returned an incomplete record.", status_code=502)

    otp = store.set_challenge_otp(challenge_id, customer_id=customer_id, email=email, name=name)

    # Send the OTP by email via the main backend. If this fails, surface it
    # explicitly rather than silently proceeding to a code the user has no
    # way to obtain — a demo where the email just never shows up is worse
    # than a loud, clear failure here.
    try:
        notify_response = requests.post(
            f"{BACKEND_INTERNAL_URL}/internal/notifications/email/",
            json={
                "to": email,
                "subject": "Your bank verification code",
                "body": f"Your verification code is {otp}",
            },
            headers={"X-Service-Token": MOCK_BANK_SERVICE_TOKEN},
            timeout=_OUTBOUND_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        return _error_page(f"Could not send the verification email: {exc}", status_code=502)

    if not notify_response.ok:
        return _error_page(
            f"Verification email failed to send (status {notify_response.status_code}).",
            status_code=502,
        )

    html = f"""
    <html>
    <head><title>Mock Bank Login - Verify</title></head>
    <body>
        <h1>Enter verification code</h1>
        <p>We sent a 6-digit code to your email on file.</p>
        <form method="post" action="/login/verify">
            <input type="hidden" name="challenge_id" value="{challenge_id}" />
            <label for="otp">Verification code</label>
            <input type="text" id="otp" name="otp" maxlength="6" required />
            <button type="submit">Verify</button>
        </form>
    </body>
    </html>
    """
    return HTMLResponse(html)


@router.post("/login/verify")
def login_verify(challenge_id: str = Form(...), otp: str = Form(...)):
    """Checks the submitted OTP and, on success, issues an authorization
    code and redirects back to the caller's redirect_uri."""
    challenge = store.get_challenge(challenge_id)
    if challenge is None:
        return _error_page("This login session is unknown or has expired.", status_code=400)

    # Deliberately vague: don't distinguish "wrong OTP" from "OTP expired"
    # from "no OTP was ever issued" beyond what's reasonable for a mock.
    if (
        challenge.otp is None
        or challenge.customer_id is None
        or challenge.otp_is_expired()
        or otp.strip() != challenge.otp
    ):
        return _error_page("Invalid or expired verification code.", status_code=400)

    code = store.create_authorization_code(
        client_id=challenge.client_id,
        redirect_uri=challenge.redirect_uri,
        customer_id=challenge.customer_id,
        email=challenge.email,
        name=challenge.name,
    )

    # Single-use challenge: invalidate it now that login is complete.
    store.pop_challenge(challenge_id)

    query_params = {"code": code.code}
    if challenge.state is not None:
        query_params["state"] = challenge.state
    separator = "&" if "?" in challenge.redirect_uri else "?"
    redirect_url = f"{challenge.redirect_uri}{separator}{urlencode(query_params)}"

    return RedirectResponse(url=redirect_url, status_code=302)
