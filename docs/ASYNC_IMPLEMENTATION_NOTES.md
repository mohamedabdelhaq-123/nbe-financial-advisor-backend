# Celery + SSE Implementation — Review Notes

Branch: `feat/celery-and-sse`, based off `30cf1f9`. Six commits, 27 files changed, +1129/-299 lines.

```
3fd2f02 feat(async): wire up Celery + Redis infrastructure
219f17e feat(async): add Redis pub/sub event bus and ticketed SSE stream endpoint
904b5f1 feat(async): run the statement OCR/normalization pipeline as a Celery task
f83b269 feat(async): run chat reply generation as a Celery task
5f1aa34 feat(async): production wiring for Celery worker and SSE proxying
405c94d test(async): add fakeredis-backed test suite for tasks, event bus, and SSE
```

Full diff: `git diff 30cf1f9..405c94d`

---

## 1. Architecture decisions (confirmed with you during planning)

- **Single multiplexed SSE connection per user** (`GET /events/stream/`), not two separate streams. Carries both `statement_status` (OCR/normalization) and `chat_token`/`chat_message` events, discriminated by real SSE `event:` type.
- **Both pipelines run as Celery tasks** — statement OCR/normalization (`core/tasks/statements.py`) and chat-reply generation (`core/tasks/conversations.py`) — publishing to the same per-user Redis pub/sub channel (`sse:user:{user_id}`). This is what makes the connection a genuine multiplex of two independent producers, not one Celery producer and one inline-request producer sharing a pipe by convention.
- **SSE auth via short-lived, single-use ticket**: `POST /events/ticket/` (normal JWT auth) mints a ~30s, single-redemption Redis-backed ticket (atomic `GETDEL`); `GET /events/stream/?ticket=...` redeems it. Needed because native `EventSource` can't set an `Authorization` header and this project's access token is never cookie-based.

## 2. Contract changes (breaking, intentional — flag to frontend)

| Endpoint | Before | After |
|---|---|---|
| `POST /statements/` | 202, sometimes had proposed transactions inline if the (fast mock) pipeline finished before the response was built | 202, **always** `status="uploaded"`, `transactions=null` — no synchronization between enqueue and response anymore |
| `PATCH /statements/{id}` | 200 | **202** — retry now runs in a Celery task, not inline |
| `POST /chat/conversations/{id}/messages` | `text/event-stream` streaming response (fake word-by-word chunking of an already-computed reply, ending in a `done` event) | **202 JSON** with just the user's own message. The assistant reply arrives later via `chat_token`/`chat_message` events on the shared `/events/stream/` connection, not in this response at all |

**SSE frame format also changed**: old `_sse_stream()` embedded `{"event": "...", "data": ...}` as the SSE `data:` payload. New format uses real SSE `event:` lines (`event: chat_message\ndata: {...}`), so clients can use `EventSource.addEventListener(type, cb)` per event type. This is a deliberate protocol improvement, not preserved for backward compatibility — needs frontend buy-in.

## 3. Assumptions made (not explicitly specified — worth double-checking)

- **Event payload field names**: `statement_status` → `{statement_id, status, is_processing, failure_reason, failed_phase}`; `chat_token` → `{conversation_id, data}`; `chat_message` → `{conversation_id, id, content, widget, references}` (mirrors old `MessageDoneEventSerializer`, with `conversation_id` added since the connection is now multiplexed across all of a user's conversations).
- **`POST /events/ticket/` is a POST**, not GET — treated as "mint/mutate state," not "read a capability."
- **SSE ticket TTL default: 30 seconds** (`SSE_TICKET_TTL_SECONDS`, env-overridable).
- **Heartbeat interval: 15 seconds** (`: heartbeat\n\n` comment lines) — keeps proxies from idling the connection out.
- **Gunicorn prod tuning**: `--worker-class gthread --workers 2 --threads 4 --timeout 0` — the `2`/`4` figures are untuned starting points, not load-tested. `--timeout 0` disables gunicorn's request timeout **globally** (not just for `/events/*`), since gunicorn's timeout isn't per-route. Flagged as a real thing to revisit under production load, not silently applied.
- **No Celery result backend** (`CELERY_TASK_IGNORE_RESULT = True`) — task completion is only ever communicated via persisted rows + the SSE event, nothing calls `.get()`/`.result` on a task. If future work needs task introspection (retries, Flower, etc.), a backend would need to be added.
- **No `celery beat`** service — nothing periodic is in scope for this phase.

## 4. Known limitations / explicitly deferred (not silently ignored)

- **No replay of missed SSE events.** Redis pub/sub has no history — an event published while a client is disconnected is simply lost. Mitigated by design: `StatementFile`/`Message` rows are always the source of truth, so a reconnect should be paired with a normal `GET` refetch. Not solved with Redis Streams at this stage.
- **Native `EventSource` auto-reconnect is incompatible with single-use tickets.** A browser's default reconnect re-requests the same (now-consumed) URL and will 401. The frontend needs its own wrapper that mints a fresh ticket and opens a new `EventSource` on `error`/`close` — this is a real frontend coordination item, not just a backend detail.
- **`deploy/docker-compose.yml` was never fully brought up end-to-end in this sandbox** — it builds `frontend` and `ai-service` from sibling repos (`../../nbe-financial-advisor-frontend`, `../../nbe-financial-advisor-ai-service`) that don't exist in this checkout. Verified via `docker compose -f deploy/docker-compose.yml config` (clean) and `nginx -t` (clean, using a network-alias trick to satisfy nginx's startup-time upstream DNS check) — but never actually run against real `frontend`/`ai-service` containers. Needs a real e2e pass wherever those sibling repos are available.
- **`match_recommendations()` (the third AI mock function) stays synchronous** — nothing currently depends on it being async; out of scope for this phase.
- **Gunicorn worker/thread counts (`--workers 2 --threads 4`) are unvalidated** against real concurrent-connection load — each open SSE connection now holds a thread for its lifetime.

## 5. Files touched (by area)

- **Celery core**: `config/celery.py` (new), `config/__init__.py`, `config/settings.py`
- **Task modules**: `core/tasks/__init__.py`, `core/tasks/statements.py` (new — moved/adapted from the old inline `_run_extraction`/`_run_normalization`/`advance_statement_to`), `core/tasks/conversations.py` (new — moved/adapted from the old inline `ai_service.chat()` call + `_sse_stream()`)
- **SSE infra**: `services/event_bus.py` (new), `services/sse_tickets.py` (new), `core/views/events.py` (new), `core/authentication.py` (+`SSETicketAuthentication`), `core/urls.py`
- **View changes**: `core/views/statements.py` (pipeline functions extracted out, `advance_statement_to` now enqueues instead of running inline), `core/views/conversations.py` (`_sse_stream()` removed, `POST .../messages` now enqueues + returns 202)
- **Docs/comments kept in sync**: `core/serializers/statements.py`, `core/serializers/conversations.py`, `core/views/profile.py` (stale references to moved functions updated)
- **Infra**: `docker-compose.yml`, `deploy/docker-compose.yml`, `deploy/nginx.conf`, `Dockerfile`
- **Tests**: `tests/conftest.py` (+`_celery_eager_mode`, `+fake_redis`), `tests/test_event_bus.py`, `tests/test_events.py`, `tests/test_statements_tasks.py`, `tests/test_conversations_tasks.py` — 29/29 passing as of the last run
- **Dev deps**: `requirements-dev.txt` (+`fakeredis==2.36.2`)

## 6. What was live-verified vs. statically verified only

**Live-verified** (against the real running `docker compose` stack, real Redis, real Celery worker, real curl/redis-cli):
- Full statement upload → async pipeline → `statement_status` SSE event → persisted final state
- Full chat message → async reply generation → `chat_token`/`chat_message` SSE events → persisted assistant message (including the `allocation_slider` widget path)
- SSE ticket mint/redeem/single-use/expiry, correct 401s
- `celery-worker` healthcheck (before and after the fix)
- Full pytest suite (29 passed)

**Statically verified only** (syntax/config validation, not live infra):
- `deploy/docker-compose.yml` (`docker compose config`)
- `deploy/nginx.conf` (`nginx -t`, with a faked upstream to get past DNS resolution)
- `Dockerfile`'s fallback `CMD` (not actually exercised by either compose file, which override it)
