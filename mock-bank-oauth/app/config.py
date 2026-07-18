"""Environment-driven configuration for the mock bank OAuth service.

Plain `os.environ` reads (no pydantic-settings) per the service spec — this
is a small mock service and doesn't need a settings framework.
"""

import os


def _require(name: str) -> str:
    """Reads an env var, raising if it's unset — used for secrets that
    would otherwise fail silently deep inside a request handler."""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Required environment variable {name} is not set")
    return value


# OAuth2 client the main Django backend authenticates as when talking to
# this service's /authorize and /token endpoints.
OAUTH_CLIENT_ID = os.environ.get("MOCK_BANK_OAUTH_CLIENT_ID", "nbe-backend")
OAUTH_CLIENT_SECRET = _require("MOCK_BANK_OAUTH_CLIENT_SECRET")

# Optional allow-list of redirect_uris. Empty string/unset means "any
# non-empty redirect_uri is accepted" — see README for the rationale (this
# is a mock IdP; a real bank's OAuth service would enforce a strict
# allow-list registered per client, but hard-coding one here would just
# mean editing this env var every time the frontend's callback URL changes
# during development).
_allowed_redirect_uris_raw = os.environ.get("MOCK_BANK_OAUTH_ALLOWED_REDIRECT_URIS", "")
ALLOWED_REDIRECT_URIS = [
    uri.strip() for uri in _allowed_redirect_uris_raw.split(",") if uri.strip()
]

# Main Django backend, used to trigger the OTP notification email.
BACKEND_INTERNAL_URL = os.environ.get("BACKEND_INTERNAL_URL", "http://backend:8000")
MOCK_BANK_SERVICE_TOKEN = _require("MOCK_BANK_SERVICE_TOKEN")

# Sibling ledger service, which owns the actual mock bank customer directory.
MOCK_BANK_SYNC_SERVICE_URL = os.environ.get(
    "MOCK_BANK_SYNC_SERVICE_URL", "http://mock-bank-sync:8003"
)
MOCK_BANK_INTERNAL_SECRET = _require("MOCK_BANK_INTERNAL_SECRET")

# HS256 signing key for access tokens issued by this service. Shared only
# with mock-bank-sync, which verifies tokens independently (this service
# never validates its own tokens after issuing them).
MOCK_BANK_JWT_SECRET = _require("MOCK_BANK_JWT_SECRET")

# Origins allowed to iframe/embed this service's /authorize login page,
# e.g. "https://app.example.com,http://localhost:3000".
_frontend_allowed_origins_raw = os.environ.get("FRONTEND_ALLOWED_ORIGINS", "")
FRONTEND_ALLOWED_ORIGINS = [
    origin.strip() for origin in _frontend_allowed_origins_raw.split(",") if origin.strip()
]


def csp_frame_ancestors_header() -> str:
    """Build the Content-Security-Policy header value allowing configured
    origins to iframe this service's login pages.

    Shared by app.main's global middleware and routes_authorize's explicit
    header on /authorize.
    """
    if not FRONTEND_ALLOWED_ORIGINS:
        # No origins configured: deny framing outright rather than silently
        # allowing an unrestricted embed.
        return "frame-ancestors 'none'"
    return "frame-ancestors " + " ".join(FRONTEND_ALLOWED_ORIGINS)


# --- Tunable lifetimes (not env-driven; short by design, this is a mock) ---
CHALLENGE_TTL_SECONDS = 5 * 60  # login challenge (from /authorize to OTP verify)
OTP_TTL_SECONDS = 5 * 60  # OTP validity window
AUTH_CODE_TTL_SECONDS = 60  # authorization code validity window (RFC 6749 recommends short-lived)
ACCESS_TOKEN_TTL_SECONDS = 60 * 60  # 1 hour

# Exposes GET /debug/challenges/{challenge_id} (app/routes_debug.py) — reads
# a challenge's OTP directly, for integration tests to drive the login flow
# without needing a real email gateway. Defaults OFF: this must be
# explicitly opted into (docker-compose.yml sets it to 1 in dev), so the
# route 404s — indistinguishable from not existing at all — anywhere it
# wasn't deliberately turned on. There's no real-bank equivalent of this
# service to ever "forget" this flag on, but it costs nothing to default safe.
DEBUG_ENDPOINTS_ENABLED = os.environ.get("MOCK_BANK_OAUTH_ENABLE_DEBUG_ENDPOINTS", "0") == "1"
