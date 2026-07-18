"""GET /authorize — start of the OAuth2 authorization-code flow.

Serves the mock bank's "login" page: a single field for an opaque
`customer_bank_id` (not assumed to be an email — see routes_login.py). The
actual identity check happens over in mock-bank-sync via /login/start.
"""

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse

from app import store
from app.config import ALLOWED_REDIRECT_URIS, OAUTH_CLIENT_ID, csp_frame_ancestors_header

router = APIRouter()


def _error_page(message: str, status_code: int = 400) -> HTMLResponse:
    return HTMLResponse(
        f"<html><body><h1>Error</h1><p>{message}</p></body></html>",
        status_code=status_code,
    )


@router.get("/authorize")
def authorize(
    client_id: str = Query(...),
    redirect_uri: str = Query(...),
    response_type: str = Query(...),
    state: str = Query(default=None),
    scope: str = Query(default=None),
):
    """Starts the OAuth2 authorization-code flow: validates the request,
    creates a login challenge, and serves the customer-id entry form."""
    if client_id != OAUTH_CLIENT_ID:
        return _error_page("Unknown client_id.", status_code=400)
    if response_type != "code":
        return _error_page("Unsupported response_type; only 'code' is supported.", status_code=400)
    if not redirect_uri:
        return _error_page("redirect_uri is required.", status_code=400)
    if ALLOWED_REDIRECT_URIS and redirect_uri not in ALLOWED_REDIRECT_URIS:
        return _error_page("redirect_uri is not on the allow-list.", status_code=400)

    challenge = store.create_challenge(
        client_id=client_id, redirect_uri=redirect_uri, state=state, scope=scope
    )

    html = f"""
    <html>
    <head><title>Mock Bank Login</title></head>
    <body>
        <h1>Mock Bank Login</h1>
        <p>Enter your bank customer ID to continue.</p>
        <form method="post" action="/login/start">
            <input type="hidden" name="challenge_id" value="{challenge.challenge_id}" />
            <label for="customer_bank_id">Customer ID</label>
            <input type="text" id="customer_bank_id" name="customer_bank_id" required />
            <button type="submit">Continue</button>
        </form>
    </body>
    </html>
    """
    response = HTMLResponse(html)
    response.headers["Content-Security-Policy"] = csp_frame_ancestors_header()
    return response
