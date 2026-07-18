# mock-bank-oauth

A mock bank **identity provider**. It simulates the login/consent/OTP step
of a real bank's OAuth integration, standing in for an actual bank's
identity service until one is integrated. It speaks standard OAuth2
authorization-code protocol on the outside (so the main backend's
integration contract doesn't need to change when a real bank is wired in
later), even though the "login" step inside uses a mocked email OTP instead
of a real bank credential check.

## Role vs. `mock-bank-sync` and the main backend

This service owns **no** ledger/customer data of its own and is entirely
stateless/in-memory (OAuth challenges, OTPs, authorization codes, and
refresh tokens all live in process memory and reset on restart — that's
fine, they're all short-lived by design).

- **`mock-bank-sync`** owns the mock bank's actual customer/account/
  transaction database. This service calls its
  `GET /internal/customers/lookup` endpoint to resolve the opaque
  `customer_bank_id` a user types in to a real `customer_id`/`email`.
- **The main Django backend** owns email delivery. This service calls its
  `POST /internal/notifications/email/` endpoint to send the OTP — it does
  **not** send email itself.
- This service's only job is: run the OAuth2 authorization-code dance,
  verify an OTP as a stand-in for a real bank login, and issue a signed JWT
  access token (plus opaque refresh token) once verified.

```text
                         ┌───────────────────┐
  backend  ──authorize──▶│  mock-bank-oauth   │
                         │  (this service)    │
                         └─────────┬──────────┘
                                   │ lookup customer_bank_id
                                   ▼
                         ┌───────────────────┐
                         │   mock-bank-sync   │  (owns customer directory,
                         │                    │   accounts, transactions)
                         └───────────────────┘

                         ┌───────────────────┐
  mock-bank-oauth ──────▶│  Django backend    │  (owns email delivery)
  POST /internal/        │  /internal/        │
  notifications/email/   │  notifications/    │
                         │  email/             │
                         └───────────────────┘
```

## Authlib usage

Authlib's OAuth2 grant/token logic (`authlib.oauth2.rfc6749`) is
framework-agnostic, but Authlib only ships pre-built request/response
adapters for Flask and Django, not FastAPI/Starlette. After evaluating it,
wiring Authlib's full `AuthorizationServer`/grant-class machinery through a
hand-rolled FastAPI adapter was judged more fiddly than it was worth for a
mock service with a small, well-understood surface area (one grant type,
no dynamic client registration, no PKCE).

Instead, this service implements RFC 6749 authorization-code flow
**semantics directly** (see `app/oauth_server.py` and `app/routes_token.py`):
validating `client_id`/`client_secret`/`redirect_uri`/`response_type`,
generating short-lived codes, single-use enforcement, and the
redirect_uri-binding check — while still using Authlib's primitives for the
security-sensitive parts:

- `authlib.common.security.generate_token` for authorization codes and
  refresh tokens (`app/store.py`)
- `authlib.jose.jwt` for signing the HS256 access token (`app/oauth_server.py`)

This prioritizes a correct, working, testable flow over rigid adherence to
Authlib's class hierarchy, per the project's guidance.

## Endpoint contract

### `GET /health`
Trivial liveness check. No dependencies checked (no DB). Returns
`200 {"status": "ok"}`.

### `GET /authorize`
Query params: `client_id`, `redirect_uri`, `response_type` (must be
`code`), `state` (optional but should always be sent by a real client),
`scope` (optional).

Validates `client_id` against `MOCK_BANK_OAUTH_CLIENT_ID` and
`response_type == "code"`. Creates a short-lived in-memory "challenge"
(random `challenge_id`) recording `client_id`/`redirect_uri`/`state`/
`scope`. Serves an HTML form collecting one field, **`customer_bank_id`**
— an opaque bank-assigned identifier (customer number, username, etc.; it
is *not* assumed to be an email) — which POSTs to `/login/start` along with
the `challenge_id`.

Sets `Content-Security-Policy: frame-ancestors <FRONTEND_ALLOWED_ORIGINS>`
so the frontend can embed this page in an iframe/modal. The same header is
also set globally via middleware (`app/main.py`) for every response, as a
defense-in-depth default for any page/response added later. If
`FRONTEND_ALLOWED_ORIGINS` is unset, the header value is
`frame-ancestors 'none'` (deny framing) rather than silently allowing an
unrestricted embed.

### `POST /login/start`
Form/body: `challenge_id`, `customer_bank_id`.

1. Looks up the challenge; `404` if missing/expired.
2. Calls `GET {MOCK_BANK_SYNC_SERVICE_URL}/internal/customers/lookup?customer_bank_id=<value>`
   with header `X-Internal-Secret: <MOCK_BANK_INTERNAL_SECRET>`. Expects
   `{"customer_id": "...", "email": "..."}` on success. A `404` from
   mock-bank-sync surfaces as "customer not found" — no OTP is generated,
   no further steps run.
3. On success, generates a cryptographically random 6-digit OTP
   (`secrets.randbelow`), stores it against the challenge with a 5-minute
   expiry, and attaches the resolved `customer_id`/`email`.
4. Calls `POST {BACKEND_INTERNAL_URL}/internal/notifications/email/` with
   header `X-Service-Token: <MOCK_BANK_SERVICE_TOKEN>` and JSON body
   `{"to": email, "subject": "Your bank verification code", "body": "Your verification code is <otp>"}`.
   A failure here (network error or non-2xx) is surfaced as an explicit
   `502` error page — it is not swallowed, since a demo where the OTP
   silently never arrives is worse than a loud failure.
5. Serves an HTML form collecting `otp`, POSTing to `/login/verify` with
   the same `challenge_id`.

### `POST /login/verify`
Form/body: `challenge_id`, `otp`.

1. Looks up the challenge and validates the OTP matches and hasn't
   expired. Failure modes (unknown challenge, wrong OTP, expired OTP) all
   return the same generic `400` "Invalid or expired verification code."
   response — this is a mock, not a hardened auth system, but there's no
   reason to leak which failure occurred.
2. On success: generates a random, short-lived (60s) single-use
   authorization `code`, stored in-memory keyed by the code and associated
   with `client_id`, `redirect_uri`, and `customer_id`. The challenge is
   deleted (single-use).
3. Responds `302` to `{redirect_uri}?code=<code>&state=<state>` (state
   omitted from the query string if none was supplied at `/authorize`).

### `POST /token`
Standard RFC 6749 authorization_code grant, form-encoded:
`grant_type=authorization_code&code=...&redirect_uri=...&client_id=...&client_secret=...`

1. Validates `grant_type == "authorization_code"`, `client_id`/
   `client_secret` against configured values, that `code` exists/is
   unexpired/is unused, and that the `redirect_uri` (and `client_id`)
   match what was recorded when the code was issued — this binding is part
   of what prevents authorization-code-interception attacks in real
   OAuth2, kept here even in the mock.
2. Marks the code used (single-use — a replayed code is rejected).
3. Issues an access token as a signed JWT (HS256, `MOCK_BANK_JWT_SECRET`)
   with claims `{"sub": customer_id, "provider": "mock_bank", "iat": ..., "exp": ...}`
   (1 hour lifetime), plus an opaque random `refresh_token` (not a JWT,
   stored in-memory).
4. Returns `200 {"access_token": ..., "token_type": "bearer", "expires_in": 3600, "refresh_token": ..., "external_customer_id": ...}`.
   `external_customer_id` appears in two places for two different readers:
   embedded in `access_token`'s JWT `sub` claim (mock-bank-sync's reader),
   and here in plaintext (the Django backend's reader — it treats the
   access token as fully opaque, with no `MOCK_BANK_JWT_SECRET` to decode
   it, but still needs this id for its own `BankConnection.external_customer_id`
   bookkeeping.
5. On any validation failure, returns `400 {"error": "invalid_grant" | "invalid_client" | "invalid_request" | "unsupported_grant_type"}`
   (with an optional `error_description`), per RFC 6749 §5.2.

Note: this service does not expose a token-verification or refresh-token
grant endpoint. Access-token verification is done independently by
mock-bank-sync using the shared `MOCK_BANK_JWT_SECRET` (HS256, so any
holder of the secret can verify without calling back here). A
`refresh_token` grant can be added later if a consumer needs it; it isn't
exercised by the current integration.

### `GET /debug/challenges/{challenge_id}`

Dev/test-only: returns a challenge's current `{challenge_id, customer_id,
email, otp}` so an integration test can drive `/login/verify` without a
real email gateway. Disabled unless `MOCK_BANK_OAUTH_ENABLE_DEBUG_ENDPOINTS=1`
(the app defaults this off; `docker-compose.yml` turns it on for this dev
stack) — when disabled, this 404s exactly as if the route didn't exist at
all, rather than 403ing. There's no real-bank equivalent of this service to
ever forget this flag on in production, but it costs nothing to default safe.

- `200 {"challenge_id", "customer_id", "email", "otp"}` when enabled and found.
- `404` when disabled, or when the challenge doesn't exist/has expired.

## Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `MOCK_BANK_OAUTH_CLIENT_ID` | no | `nbe-backend` | Expected `client_id` on `/authorize` and `/token`. |
| `MOCK_BANK_OAUTH_CLIENT_SECRET` | **yes** | — | Expected `client_secret` on `/token`. |
| `MOCK_BANK_OAUTH_ALLOWED_REDIRECT_URIS` | no | (empty = any non-empty `redirect_uri` accepted) | Comma-separated allow-list. If set, `/authorize` rejects any `redirect_uri` not on the list. |
| `BACKEND_INTERNAL_URL` | no | `http://backend:8000` | Main Django backend, for the OTP email call. |
| `MOCK_BANK_SERVICE_TOKEN` | **yes** | — | Sent as `X-Service-Token` when calling the backend's `/internal/notifications/email/`. |
| `MOCK_BANK_SYNC_SERVICE_URL` | no | `http://mock-bank-sync:8003` | Sibling ledger service, for customer lookup. |
| `MOCK_BANK_INTERNAL_SECRET` | **yes** | — | Sent as `X-Internal-Secret` when calling mock-bank-sync's `/internal/customers/lookup`. |
| `MOCK_BANK_JWT_SECRET` | **yes** | — | HS256 key used to sign access tokens. Shared only with mock-bank-sync, which verifies tokens independently. |
| `MOCK_BANK_OAUTH_ENABLE_DEBUG_ENDPOINTS` | no | `0` (off) | Set to `1` to enable `GET /debug/challenges/{id}`. Dev/test only. |
| `FRONTEND_ALLOWED_ORIGINS` | no | (empty = `frame-ancestors 'none'`) | Comma-separated origins allowed to iframe `/authorize` and other pages via `Content-Security-Policy: frame-ancestors`. |

## Running standalone

```bash
cd mock-bank-oauth
pip install -r requirements.txt

export MOCK_BANK_OAUTH_CLIENT_ID=nbe-backend
export MOCK_BANK_OAUTH_CLIENT_SECRET=dev-client-secret
export BACKEND_INTERNAL_URL=http://localhost:8000
export MOCK_BANK_SERVICE_TOKEN=dev-service-token
export MOCK_BANK_SYNC_SERVICE_URL=http://localhost:8003
export MOCK_BANK_INTERNAL_SECRET=dev-internal-secret
export MOCK_BANK_JWT_SECRET=dev-jwt-secret-change-me
export FRONTEND_ALLOWED_ORIGINS=http://localhost:3000

uvicorn app.main:app --reload --port 8002
```

Then visit, e.g.:

```text
http://localhost:8002/authorize?client_id=nbe-backend&redirect_uri=http://localhost:3000/callback&response_type=code&state=xyz
```

Note: `/login/start` and `/login/verify` call out to `mock-bank-sync` and
the main backend respectively. Without those running, `/login/start` will
return a `502` (customer directory unreachable) — that's expected until
those services exist / are running alongside this one.

## Building the container

```bash
docker build -t mock-bank-oauth mock-bank-oauth/
```

Exposes port `8002`; healthcheck hits `GET /health`.
