# Running the full stack

Two compose files live in this directory and bring up **everything** — not
just the backend — assuming `nbe-financial-advisor-frontend` and
`nbe-financial-advisor-ai-service` are checked out as sibling directories
next to `nbe-financial-advisor-backend`:

```
some-parent-dir/
├── nbe-financial-advisor-frontend/
├── nbe-financial-advisor-backend/
│   └── deploy/   (this directory — run docker compose from here)
└── nbe-financial-advisor-ai-service/
```

| File                      | Purpose                                                                                                                                                              |
| ------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `docker-compose.dev.yml`  | Local development. Hot reload on backend, frontend, and ai-service. Every service's port is published to `localhost` for direct access/debugging.                    |
| `docker-compose.prod.yml` | Production-optimized. Multi-stage builds, no bind mounts, no dev servers. Only nginx is publicly reachable — everything else talks over the internal Docker network. |

Both need a real `.env` in each of the three app repos (`../.env` here,
`../../nbe-financial-advisor-frontend/.env`,
`../../nbe-financial-advisor-ai-service/.env`) — copy each repo's
`.env.example` first if you don't have one.

Dev's `backend` also seeds itself on every startup: `seed_onboarding_templates`
(budget starter templates, both stacks) and `seed_db` (synthetic demo users/
accounts/transactions, dev-only — DEBUG-guarded, re-seeded fresh each run).
Set `SEED_MOCK_BANK_CUSTOMER_EMAIL=you@example.com` (e.g. in `../.env`) to
also register a mock bank customer with starter transaction history on
startup, for exercising the real "Connect bank" OTP flow against that inbox
— leave it unset to skip.

## Commands

```bash
# Dev — hot reload, ports published for every service
docker compose -f docker-compose.dev.yml up -d --build
docker compose -f docker-compose.dev.yml logs -f <service>
docker compose -f docker-compose.dev.yml down            # stop, keep data
docker compose -f docker-compose.dev.yml down -v          # stop, wipe data too

# Prod — optimized builds, only nginx published. --env-file is required, see below.
docker compose -f docker-compose.prod.yml --env-file .env up -d --build
docker compose -f docker-compose.prod.yml --env-file .env logs -f <service>
docker compose -f docker-compose.prod.yml --env-file .env up -d --build <service>   # redeploy just one
docker compose -f docker-compose.prod.yml down
docker compose -f docker-compose.prod.yml down -v
```

## Which `.env` file is which

There are `.env` files in every app repo plus this directory's own, and they
serve two genuinely different purposes:

| File                                        | Loaded by                                                                                                                          | Purpose                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| ------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `nbe-financial-advisor-backend/.env`        | `env_file:` on `backend`/`celery-worker`/`mock-bank-oauth`/`mock-bank-sync` in dev                                                 | Real app config for the Django containers — `DJANGO_SECRET_KEY`, DB creds, feature toggles, `AI_SERVICE_TOKEN`, etc. Authoritative.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `nbe-financial-advisor-ai-service/.env`     | `env_file:` on `ai-service` in both stacks                                                                                         | Same idea, for the AI service. Authoritative.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `nbe-financial-advisor-frontend/.env`       | `env_file:` on `frontend` in dev; default build arg in `Dockerfile.prod`                                                           | Just `VITE_API_BASE_URL`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `deploy/.env` (this directory)              | **Not `env_file:`-loaded anywhere.** Pass explicitly via `--env-file .env` when running `docker-compose.prod.yml` (see Commands above). | Docker Compose auto-loads a plain `.env` from the compose project directory to resolve any `${VAR}` in the YAML that isn't backed by `env_file:` — `docker-compose.prod.yml` has several of these (postgres passwords, `SEAWEED_SECRET_KEY`, `MOCK_BANK_JWT_SECRET`, `AI_SERVICE_TOKEN`'s override on `backend`'s own `environment:` block, etc). This file is almost identical to `../​.env` (one extra key, `MOCK_BANK_OAUTH_PUBLIC_URL`) since it exists purely to serve this substitution role, not as a second independent config. |

`docker-compose.dev.yml` doesn't need `--env-file`: its equivalent
`${VAR:-default}` placeholders (postgres passwords) happen to default to
the same dev-safe values already in `../.env`, and `mock-bank-oauth`/
`mock-bank-sync` use real `env_file:` there instead of substitution.

After a normal code change:

- **Dev**: nothing to run for backend/frontend/ai-service — they auto-reload. Exception: `celery-worker` doesn't hot-reload; `docker compose -f docker-compose.dev.yml restart celery-worker` after editing backend `core/tasks/*.py`.
- **Prod**: rebuild the service you changed (`up -d --build <service>`) — there are no bind mounts, so nothing picks up a change without a rebuild.

## Services

The core application is **9 services**, present in both stacks:

| Service             | Why it's needed                                                                                                                                                                                                                                                                                                                               |
| ------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **postgres**        | The single source of truth — the Django backend's tables, plus two isolated logical databases on the same instance (`ai_appdb` for the AI service, `mock_bank_db` for the mock bank ledger), each behind its own least-privilege role so neither can touch the other's data.                                                                  |
| **redis**           | Backs two independent things: the Celery task queue (statement OCR, chat replies, notifications run as background jobs, not inline in a request) and the pub/sub event bus that powers Server-Sent Events (`services/event_bus.py`) — a chat token or anomaly alert published here is what a browser's open SSE connection actually receives. |
| **seaweedfs**       | S3-compatible object storage for uploaded bank statement PDFs and their OCR'd/normalized artifacts — self-hosted so the stack has zero external cloud dependency.                                                                                                                                                                             |
| **backend**         | The Django REST API — auth, transactions, budgets, dashboard, chat, statement pipeline. Everything the frontend talks to except the two things broken out below.                                                                                                                                                                              |
| **celery-worker**   | Runs the actual background jobs backend queues into redis — statement OCR/normalization, async chat reply generation, email notifications — off the request/response cycle so those don't block or time out an HTTP call.                                                                                                                     |
| **ai-service**      | The LLM-facing service — chat replies, statement normalization, budget plan generation, product recommendation matching. Kept as its own service (not in-process in backend) so it can hold its own DB role, its own dependency set (LangChain, embeddings), and be swapped to a real GPU/vLLM backend later without touching backend at all. |
| **mock-bank-oauth** | Stands in for a real bank's OAuth+OTP login screen during development/demo — without it there's no way to exercise the "connect your bank" flow without an actual bank integration.                                                                                                                                                           |
| **mock-bank-sync**  | Stands in for a real bank's transaction feed — owns its own ledger (mock_bank_db) and pushes transactions to backend via webhook, the same shape a real bank integration would use.                                                                                                                                                           |
| **frontend**        | The React/Vite SPA itself.                                                                                                                                                                                                                                                                                                                    |

Production adds **two infrastructure pieces** on top of those 9 — not
optional in prod, since without them the other 9 aren't actually reachable
or safe under load:

| Service            | Why it's needed                                                                                                                                                                                                                                                                                                                  |
| ------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **nginx**          | The one publicly reachable container (port 8080). Routes `/` → frontend, `/api/` → backend, `/api/events/` → backend-events, `/authorize`+`/login/` → mock-bank-oauth's browser-facing pages. Everything it fronts is otherwise internal-only.                                                                                   |
| **backend-events** | The same Django image as `backend`, but running only `GET /events/stream` (the app's single multiplexed SSE connection) on its own gunicorn worker pool. SSE connections are long-lived; without this split, a pile-up of open browser tabs would exhaust `backend`'s thread pool and stall every other API request behind them. |

Dev skips both: every service's port is published straight to `localhost`
instead, and the SSE route just runs on `backend` alongside everything else
— fine at dev traffic levels, which is why isolating it is a prod-only concern.

Both stacks also define an **optional 10th service, off by default**:

| Service            | Why it's off by default                                                                                                                                                                                                        |
| ------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **mineru-server**  | Real document-OCR engine (GPU-only, extended from `../../nbe-financial-advisor-ai-service/compose/mineru/docker-compose.yml`). `ai-service` stays in mocked-MinerU mode (`USE_MOCK_MINERU=1`) until this is started explicitly with `--profile api up mineru-server` and `AI_SERVICE_MINERU__USE_MOCK=0` is set. |

LLM observability (Langfuse) is vendored in the ai-service repo
(`compose/langfuse/docker-compose.yml`) but is **not currently wired into
either stack here** — see that repo's README for status.

## Data persistence

Both stacks use named volumes for postgres (`pgdata`) and seaweedfs
(`seaweed_data`) — `down` alone leaves your data intact across restarts;
only `down -v` wipes it. Dev additionally persists `node_modules` and each
Python service's `.venv` in named volumes so a container restart doesn't
force a fresh `pnpm install`/`uv sync`.
