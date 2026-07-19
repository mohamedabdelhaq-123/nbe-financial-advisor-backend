# mock-bank-sync

`mock-bank-sync` is the mock bank's **ledger of record**. It owns the entire
fake bank's data: fake customers, fake accounts, fake transactions. It is
the single source of truth for "who is this bank customer" and "what
accounts/transactions do they have." Nothing else in this demo stack stores
that data.

It also doubles as the "make it look like a real-time bank event happened"
trigger for demos: `POST /simulate/transaction` creates a new fake
transaction in its own ledger and immediately pushes it to the Django
backend's inbound webhook, simulating a bank pushing a live transaction
update.

## How it fits with the other services

```text
                 login/OTP flow                 pull accounts/transactions
                 ─────────────────►             ◄───────────────────────────
   end user  ──► mock-bank-oauth                        Django backend
                       │                                      ▲
                       │ GET /internal/customers/lookup       │ POST /simulate/transaction
                       ▼ (X-Internal-Secret)                  │ pushes a webhook to
                 mock-bank-sync ────────────────────────────────┘
                 (this service — owns all
                  customers/accounts/transactions)
```

- **mock-bank-oauth** (sibling service, built separately) handles
  login/OTP/token issuance for the fake bank. It owns no data of its own —
  before it can send an OTP to a "bank customer," it calls this service's
  `GET /internal/customers/lookup` to resolve whether a given bank-login
  identifier corresponds to a real mock customer (and if so, what email to
  send the OTP to). Once the customer completes login, mock-bank-oauth
  issues an HS256 JWT (`sub` = this service's `MockCustomer.id`) that the
  Django backend then uses to call `GET /accounts` and
  `GET /accounts/{id}/transactions` here directly.
- **Django backend** calls `GET /accounts` / `GET /accounts/{id}/transactions`
  here (Bearer JWT auth) to pull a linked customer's account and transaction
  data during account linking / periodic sync. It also runs its own inbound
  webhook (`BACKEND_WEBHOOK_URL`, default `/webhooks/bank-sync/`) that this
  service pushes newly "created" transactions to when
  `POST /simulate/transaction` is called, simulating the bank pushing a
  live update rather than the backend having to poll for it.

## Endpoint contract

All response field names below are exact — other tracks (mock-bank-oauth,
the Django backend) are written against this contract.

### `GET /health`

No auth. Runs a trivial `SELECT 1` against Postgres (unlike mock-bank-oauth,
this service can't do anything useful without its DB, so health reflects
that).

- `200 {"status": "ok"}` — DB reachable.
- `503 {"status": "unavailable", "detail": "database unreachable"}` — DB down.

### `GET /internal/customers/lookup?customer_bank_id=<value>`

Internal-secret protected (`X-Internal-Secret` header, must equal
`MOCK_BANK_INTERNAL_SECRET`). Called only by mock-bank-oauth as the
identity-resolution step before it sends an OTP.

- `200 {"customer_id": "<uuid>", "email": "<email>"}`
- `401` — missing header. `403` — header present but wrong.
- `404 {"detail": "No mock customer found for customer_bank_id=...'"}` — no
  such customer.

### `GET /accounts`

Bearer JWT protected (`Authorization: Bearer <token>`, HS256, verified
against `MOCK_BANK_JWT_SECRET` — the same secret mock-bank-oauth signs
with). The JWT's `sub` claim is treated as the `MockCustomer.id` to scope
the query to.

Returns all accounts belonging to that customer:

```json
[
  {
    "external_account_id": "<uuid>",
    "bank_name": "Mock National Bank",
    "account_type": "checking",
    "masked_account_number": "****1234",
    "currency": "EGP"
  }
]
```

`external_account_id` (not `id`) is the field name deliberately — the
Django backend stores this value as its own `external_account_id` foreign
reference.

- `401` — missing/invalid/expired token.

### `GET /accounts/{account_id}/transactions?since=<ISO date, optional>`

Same Bearer JWT auth as `/accounts`. `account_id` must belong to the JWT's
customer — an account belonging to a different customer 404s exactly like a
nonexistent one, so this endpoint never leaks whether another customer's
account exists.

```json
[
  {
    "external_transaction_id": "<uuid>",
    "transaction_date": "2026-07-18T09:00:00+00:00",
    "merchant_raw": "Carrefour",
    "amount": "123.45",
    "transaction_type": "debit",
    "currency": "EGP",
    "balance": "4321.00"
  }
]
```

`external_transaction_id` and `merchant_raw` are the field names
deliberately — matching the Django-side `Transaction` model and
`BankConnector.fetch_transactions()` contract. `currency` is the owning
account's currency (transactions don't carry their own).

- `401` — missing/invalid/expired token.
- `404` — account doesn't exist, or belongs to a different customer.

### `POST /simulate/transaction`

No auth (dev/demo trigger — see rationale in `app/routes_simulate.py`; if
this service is ever exposed outside a trusted dev network, gate this
behind `MOCK_BANK_INTERNAL_SECRET` too).

Request body (all fields optional):

```json
{
  "account_id": "<uuid, optional — random existing account if omitted>",
  "amount": "<decimal, optional — random plausible value if omitted>",
  "merchant": "<string, optional — random sample merchant if omitted>",
  "transaction_type": "<string, optional — random debit/credit if omitted>",
  "transaction_date": "<ISO datetime, optional — now() if omitted>"
}
```

Behavior:
1. Resolves the account (random pick across all mock accounts if
   `account_id` omitted; `404` if no accounts exist at all, or if the given
   `account_id` doesn't exist).
2. Inserts a new `MockTransaction` row into this service's own DB.
3. POSTs a webhook to `BACKEND_WEBHOOK_URL`
   (`http://backend:8000/webhooks/bank-sync/` by default) with header
   `X-Webhook-Secret: <BANK_SYNC_WEBHOOK_SECRET>`:

   ```json
   {
     "provider_slug": "mock_bank",
     "external_account_id": "<uuid>",
     "transactions": [
       {
         "external_transaction_id": "<uuid>",
         "transaction_date": "...",
         "merchant_raw": "...",
         "amount": "...",
         "transaction_type": "...",
         "currency": "...",
         "balance": "..."
       }
     ]
   }
   ```
4. If the webhook push fails (backend unreachable, non-2xx) the request
   still succeeds — the transaction was already recorded in the mock
   ledger. The response includes a `webhook_delivery` field so the caller
   can tell whether delivery succeeded:

   ```json
   {
     "external_transaction_id": "<uuid>",
     "transaction_date": "...",
     "merchant_raw": "...",
     "amount": "...",
     "transaction_type": "...",
     "currency": "...",
     "balance": null,
     "webhook_delivery": {"success": true, "status_code": 200, "error": null}
   }
   ```

- `201` on success. `404` if no accounts exist / given `account_id` not found.

### `POST /simulate/customer`

No auth, same rationale as above. Seeds a new test bank customer (and
starter account(s)) without touching the DB directly — useful for exercising
multi-bank/multiple-account linking scenarios in demos.

Request body:

```json
{
  "customer_bank_id": "cust-001",
  "email": "test@example.com",
  "name": "Test Customer",
  "accounts": [
    {
      "bank_name": "Mock National Bank",
      "account_type": "checking",
      "masked_account_number": "****1234",
      "currency": "EGP"
    }
  ]
}
```

`accounts` defaults to a single plausible starter checking account if
omitted entirely.

Response:

```json
{
  "customer_id": "<uuid>",
  "customer_bank_id": "cust-001",
  "email": "test@example.com",
  "name": "Test Customer",
  "accounts": [
    {
      "external_account_id": "<uuid>",
      "bank_name": "Mock National Bank",
      "account_type": "checking",
      "masked_account_number": "****1234",
      "currency": "EGP"
    }
  ]
}
```

- `201` on success. `409` if `customer_bank_id` already exists.

### `DELETE /simulate/customer/{customer_bank_id}`

No auth, same rationale as the other `/simulate/*` routes. Removes a
customer and cascades to their accounts/transactions (SQLAlchemy
`cascade="all, delete-orphan"` in `app/models.py`). Exists so a test that
seeds a customer via `POST /simulate/customer` can clean up after itself —
this service's ledger is otherwise a real, persistent database shared
across every process that talks to it, not one that resets per test the
way `tests/conftest.py`'s transaction-rollback fixture does for this
service's *own* test suite.

- `204` on success. `404` if the customer doesn't exist.

## Data model

- `MockCustomer` — `id` (UUID PK), `customer_bank_id` (string, unique,
  not null — opaque bank-login identifier: customer number, username,
  whatever; not assumed to be an email), `email` (string, not null — used
  for OTP delivery by mock-bank-oauth), `name` (string, nullable).
- `MockAccount` — `id` (UUID PK), `customer_id` (FK -> `MockCustomer.id`),
  `bank_name` (string, default `"Mock National Bank"`), `account_type`
  (string, nullable, e.g. checking/savings/credit_card),
  `masked_account_number` (string), `currency` (string, default `"EGP"`).
- `MockTransaction` — `id` (UUID PK), `account_id` (FK ->
  `MockAccount.id`), `transaction_date` (datetime), `merchant` (string,
  nullable), `amount` (numeric), `transaction_type` (string, nullable),
  `balance` (numeric, nullable).

See `app/models.py` for the SQLAlchemy ORM definitions and
`alembic/versions/0001_initial.py` for the migration that creates these
three tables (`mock_customers`, `mock_accounts`, `mock_transactions`).

## Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `POSTGRES_HOST` | no | `postgres` | Shared Postgres container host (same var name the Django `backend` service uses) |
| `POSTGRES_PORT` | no | `5432` | Shared Postgres container port |
| `MOCK_BANK_DB_NAME` | no | `mock_bank_db` | This service's own logical database on that container |
| `MOCK_BANK_DB_USER` | no | `mock_bank_user` | Least-privilege role owning `mock_bank_db` |
| `MOCK_BANK_DB_PASSWORD` | **yes** | — | Password for `MOCK_BANK_DB_USER` (also consumed by `deploy/initdb/20-mock-bank-roles.sh` to provision the role) |
| `MOCK_BANK_INTERNAL_SECRET` | **yes** | — | Shared secret checked on `X-Internal-Secret` for `/internal/customers/lookup` |
| `MOCK_BANK_JWT_SECRET` | **yes** | — | HS256 secret used to verify Bearer JWTs on `/accounts*` — must match what mock-bank-oauth signs with |
| `BACKEND_WEBHOOK_URL` | no | `http://backend:8000/webhooks/bank-sync/` | Where `/simulate/transaction` pushes its webhook |
| `BANK_SYNC_WEBHOOK_SECRET` | **yes** | — | Sent as `X-Webhook-Secret` on the outbound webhook push |

## Running standalone

Requires a reachable Postgres with `deploy/initdb/20-mock-bank-roles.sh`
already having run against it (it provisions the `mock_bank_user` role and
`mock_bank_db` database this service connects to — that script is picked up
automatically by the existing `./deploy/initdb` volume mount on the shared
`postgres` compose service; on a pre-existing Postgres volume where init
scripts already ran once, run it by hand instead — see the comment at the
top of that script).

```bash
cd mock-bank-sync
pip install -r requirements.txt

export POSTGRES_HOST=localhost
export POSTGRES_PORT=5432
export MOCK_BANK_DB_PASSWORD=mock_bank_db_pass
export MOCK_BANK_INTERNAL_SECRET=dev-internal-secret
export MOCK_BANK_JWT_SECRET=dev-jwt-secret       # must match mock-bank-oauth's value
export BANK_SYNC_WEBHOOK_SECRET=dev-webhook-secret
# export BACKEND_WEBHOOK_URL=http://localhost:8000/webhooks/bank-sync/  # if needed

alembic upgrade head
uvicorn app.main:app --reload --port 8003
```

## Seeding test data

Once the service is up, seed a test bank customer (and a starter account)
with:

```bash
curl -X POST http://localhost:8003/simulate/customer \
  -H "Content-Type: application/json" \
  -d '{"customer_bank_id": "cust-001", "email": "test@example.com", "name": "Test Customer"}'
```

Then fire a simulated live transaction against one of that customer's
accounts (pushes to the Django backend's webhook):

```bash
curl -X POST http://localhost:8003/simulate/transaction \
  -H "Content-Type: application/json" \
  -d '{"account_id": "<uuid from the customer response above>"}'
```

For a one-shot setup covering both customers not yet linked to any Django
user and demo users fully registered in both systems (driven through the
real OAuth+OTP flow), run the Django backend's own seed command instead:
`python manage.py seed_bank_demo_data --help`.
