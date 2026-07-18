"""Mock bank OAuth service — FastAPI entrypoint.

See README.md for the full endpoint contract and role of this service
relative to mock-bank-sync and the main Django backend.
"""

from fastapi import FastAPI, Request

from app import routes_authorize, routes_debug, routes_login, routes_token
from app.config import csp_frame_ancestors_header

app = FastAPI(title="Mock Bank OAuth Service")

app.include_router(routes_authorize.router)
app.include_router(routes_login.router)
app.include_router(routes_token.router)
app.include_router(routes_debug.router)


@app.middleware("http")
async def add_frame_ancestors_csp(request: Request, call_next):
    """Set Content-Security-Policy: frame-ancestors globally.

    Lets the frontend embed this service's login pages in an iframe/modal
    (browsers block cross-origin framing by default without this header).
    Routes that already set their own CSP header (e.g. /authorize) are left
    untouched.
    """
    response = await call_next(request)
    response.headers.setdefault("Content-Security-Policy", csp_frame_ancestors_header())
    return response


@app.get("/health")
def health():
    """Liveness check — no dependencies to verify, this service has no DB."""
    return {"status": "ok"}
