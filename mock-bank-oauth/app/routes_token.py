"""POST /token — RFC 6749 §4.1.3 authorization_code grant.

Form-encoded per spec: grant_type=authorization_code&code=...&redirect_uri=
...&client_id=...&client_secret=...
"""

from fastapi import APIRouter, Form
from fastapi.responses import JSONResponse

from app import store
from app.config import (
    ACCESS_TOKEN_TTL_SECONDS,
    OAUTH_CLIENT_SECRET,
)
from app.oauth_server import OAuthError, issue_access_token, validate_client

router = APIRouter()


def _oauth_error_response(error: str, description: str | None = None) -> JSONResponse:
    body = {"error": error}
    if description:
        body["error_description"] = description
    return JSONResponse(body, status_code=400)


@router.post("/token")
def token(
    grant_type: str = Form(...),
    code: str = Form(...),
    redirect_uri: str = Form(...),
    client_id: str = Form(...),
    client_secret: str = Form(...),
):
    """RFC 6749 §4.1.3 authorization_code grant: redeems a single-use code
    for a signed JWT access token plus an opaque refresh token."""
    if grant_type != "authorization_code":
        return _oauth_error_response(
            "unsupported_grant_type", "Only authorization_code is supported."
        )

    try:
        validate_client(client_id, client_secret, OAUTH_CLIENT_SECRET)
    except OAuthError as exc:
        return _oauth_error_response(exc.error, exc.description)

    record = store.consume_authorization_code(code)
    if record is None:
        return _oauth_error_response(
            "invalid_grant", "The authorization code is invalid, expired, or already used."
        )

    # Binding the code to the redirect_uri it was issued with guards
    # against authorization-code-interception attacks (RFC 6749 §4.1.3).
    # The code is already consumed at this point (single-use, checked above)
    # even if this specific check then fails — a mismatched redirect_uri/
    # client_id is still a legitimate reason to burn the code, not retry it.
    if record.redirect_uri != redirect_uri or record.client_id != client_id:
        return _oauth_error_response(
            "invalid_grant", "redirect_uri or client_id does not match the authorization request."
        )

    access_token = issue_access_token(record.customer_id)
    refresh_token = store.create_refresh_token(client_id, record.customer_id)

    return JSONResponse(
        {
            "access_token": access_token,
            "token_type": "bearer",
            "expires_in": ACCESS_TOKEN_TTL_SECONDS,
            "refresh_token": refresh_token,
            # Also embedded in access_token's JWT `sub` claim (mock-bank-sync
            # verifies and extracts it from there) — returned here too in
            # plaintext because the Django backend treats the token itself as
            # fully opaque (it has no MOCK_BANK_JWT_SECRET to decode it with)
            # but still needs this id for BankConnection.external_customer_id
            # bookkeeping. See services/bank_connectors/mock_bank.py.
            "external_customer_id": record.customer_id,
        }
    )
