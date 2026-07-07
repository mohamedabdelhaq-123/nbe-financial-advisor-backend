# API Design Guidelines
**AI-Powered Personal Financial Advisor — Graduation Project (NBE)**

A project-agnostic set of rules for how APIs are structured: parameter conventions, payload shapes, pagination, filtering/sorting/scoping, error format, and versioning. This document does not describe any specific endpoint — it is the guideline every endpoint (existing or future) is expected to follow, so the API stays consistent as the system grows and new routes get added by different people. The concrete route list lives in the API Endpoints document; the concrete request/response bodies live in the Data Shapes documents.

---

## 1. General Principles

- **Resource-oriented REST.** URLs name resources (`/transactions`, `/budget`), not actions (`/getTransactions`). The HTTP method carries the verb: `GET` read, `POST` create, `PATCH` partial update, `PUT` full replace, `DELETE` remove.
- **JSON only.** Every request body and response body is JSON (`Content-Type: application/json`), except file uploads, which use `multipart/form-data`.
- **One backend, one write path.** Regardless of which surface triggers a change (dashboard, chat, document upload), it goes through the same endpoint and the same validation — there is no separate "chat-only" write route that skips rules a dashboard write would enforce.
- **The frontend never calls the AI service.** Every frontend request goes to Django. Django is the only caller of the internal AI service (see System Architecture §3) — this guideline governs the Django-facing (public) API only. Internal Django↔AI-service calls follow their own, simpler internal contract (see Services and Background Tasks document) and are not expected to match these public conventions.

---

## 2. Identifiers & Data Types

- **IDs are UUIDs**, returned and accepted as lowercase hyphenated strings (`"3fa85f64-5717-4562-b3fc-2c963f66afa6"`). No sequential integer IDs are exposed publicly.
- **Timestamps are ISO 8601**, always UTC, always with a trailing `Z` (`"2026-07-06T10:00:00Z"`). Clients localize for display; the API never returns a localized timestamp.
- **Dates without a time component** (e.g. a transaction date) use `YYYY-MM-DD`.
- **Monetary amounts** are numbers (not strings), up to 7 digits total, in the smallest sensible unit for the currency being used (EGP as whole/decimal currency, not cents) — matching the wireframe spec's "amounts up to 7 digits" rule. A `currency` field always travels alongside an amount field; no endpoint returns a bare number without stating its currency.
- **Percentages** (budget allocations) are authored and returned as numbers out of 100, not fractions (`20`, not `0.20`).

---

## 3. The Percentage + Derived-Amount Convention

Any endpoint dealing with budget allocations follows one fixed rule, established once so it's never re-litigated per endpoint: the **client sends percentages**, the **backend derives and stores the amount**, and **every response includes both**.

- Request bodies for allocations contain only `allocated_percentage` per category.
- The backend validates that percentages across a plan sum to 100, rejecting with `422` otherwise.
- The backend computes `allocated_amount = monthly_income × allocated_percentage` at write time and stores both columns (not computed live on every read), so historical plans stay correct even if `monthly_income` changes later.
- Every response — creation, update, or read — includes `allocated_percentage`, `allocated_amount`, and `currency` together. The frontend never re-derives this math itself.

Any new endpoint touching allocations must follow this same shape rather than inventing a percentage-only or amount-only variant.

---

## 4. The Goal Object Convention

Wherever a savings goal appears (onboarding, budget, dashboard), it is the same shape everywhere:

```json
{ "name": "car", "target_amount": 100000, "target_months": 14 }
```

On read-oriented endpoints (e.g. dashboard, current plan), `target_months` is replaced with `months_remaining`, computed server-side — the field name signals which direction the number reads, so a client can't confuse "total plan length" with "time left." A goal is never returned unnamed — the name is collected at the point of goal creation, never left nullable and patched in later as an afterthought.

---

## 5. Pagination

Two pagination strategies are used, chosen by the shape of the underlying data — not left to each endpoint's author to decide ad hoc:

- **Offset pagination** — implemented with **DRF's `LimitOffsetPagination`**, deliberately chosen over `PageNumberPagination` so the frontend controls page size directly rather than being locked to a fixed, backend-defined `page_size`. Request params are `limit` (how many rows to return, client-chosen up to a backend-enforced max) and `offset` (how many rows to skip). Response shape follows DRF's default for this class:
  ```json
  { "count": "integer", "next": "string (url) | null", "previous": "string (url) | null", "results": [ /* ... */ ] }
  ```
  Used for **bounded, filterable, randomly-accessed** collections where a user might jump to an arbitrary offset or filter by date range. Transactions is the reference case.
- **Cursor pagination** — implemented with DRF's `CursorPagination` (`cursor` query param, response includes `next_cursor`/`previous_cursor` per DRF's cursor shape) — used for **unbounded, append-only, chronologically-scrolled** collections, where jumping to an arbitrary offset isn't a meaningful action and new items keep arriving at one end. Conversation messages is the reference case. Not switched to `LimitOffsetPagination` even though it also grants frontend control, because offset-based paging is unreliable on a collection that's actively being appended to (rows shift under a fixed offset) — cursor pagination avoids that class of bug entirely.

A new list endpoint picks whichever of these two matches its data's actual access pattern; a third pagination style is not introduced without a documented reason. Every endpoint documented as "Pagination: Offset" in the Data Shapes & Query Params documents uses `LimitOffsetPagination` with the `limit`/`offset`/response shape above — this is restated per-endpoint there rather than only here, so each endpoint's request/response spec is self-contained.

---

## 6. Filtering, Sorting, and Scoping

- **Filters are query parameters named after the field they filter**, not a generic `filter` blob: `?account_id=&category=&from=&to=`. Range filters use `from`/`to` suffixes on the underlying field name.
- **Every list endpoint is implicitly scoped to the authenticated user.** There is no `user_id` query parameter on user-facing endpoints — scoping comes from the auth token, never from a client-supplied value, so one user can never query another's data by changing a parameter.
- **Sorting**, where an endpoint needs it, uses a `sort` parameter with an optional `-` prefix for descending (`?sort=-transaction_date`). Default sort order is always specified in that endpoint's own documentation (Query Params, Scoping and Authentication document) rather than left implicit.
- **Admin endpoints** (Administration domain) are the one exception to implicit self-scoping — they operate across users by design, and are gated by role rather than by ownership (see §8).

---

## 7. Aggregate Endpoints

Where a single screen would otherwise require several sequential calls to assemble (e.g. the dashboard needing plan + goal + metrics + net worth), a dedicated aggregate endpoint is provided instead of leaving the frontend to stitch calls together (`GET /dashboard` is the reference case). This is a deliberate exception to strict one-endpoint-per-resource purity, justified only when a specific screen's load performance depends on it — it is not a general license to build ad hoc combined endpoints for convenience.

---

## 8. Authentication & Authorization

- **JWT bearer tokens** (access + refresh pair), issued at login/signup, sent as `Authorization: Bearer <token>` on every authenticated request.
- **Refresh flow** is a dedicated endpoint (`POST /auth/refresh`), never silently handled by re-sending credentials.
- **Role separation is structural, not a flag.** Admin/internal-staff auth (Administration domain) is a completely separate credential space from end-user auth — an admin token and a user token are never interchangeable, and no endpoint accepts either kind of token depending on convenience.
- **The assistant never gets elevated permissions.** Any write the AI service triggers on a user's behalf still passes through the same user-scoped, user-authenticated write path as a dashboard action — the AI service does not hold a standing credential that can write directly to a user's data outside of what the user has actively confirmed.

---

## 9. Synchronous vs. Asynchronous Operations

- **Fast, deterministic operations are synchronous**: normal CRUD, budget edits, manual transaction entry.
- **Slow or non-deterministic operations return `202 Accepted`** immediately with a status-tracking identifier, and the client polls (or, where implemented, listens over a stream) for completion. Document upload/OCR/normalization is the reference case: `POST /statements` returns `202` with a `statement_id` and `status: pending`; `GET /statements/{id}` is polled until `status` reaches a terminal state.
- **Streaming responses** (chat) use Server-Sent Events over an async view, not polling — the one deliberate exception to the request/response pattern elsewhere in the API, justified by the UX cost of a delayed, non-incremental chat reply. This requires the backend to run under ASGI for that route (see Services and Background Tasks document).

---

## 10. Validation & Error Format

- All request bodies are validated by the same serializer layer that defines the resource (DRF serializers) — no second, parallel validation library is introduced (see Tech Stack document; this avoids two sources of truth for one payload shape).
- **`422 Unprocessable Entity`** for semantic/business-rule validation failures (allocations not summing to 100, malformed goal timeline). **`400 Bad Request`** is reserved for malformed request syntax (invalid JSON, missing required field). **`404`** for a resource that doesn't exist or doesn't belong to the requesting user — the API does not distinguish "doesn't exist" from "exists but isn't yours," to avoid leaking existence of other users' data.
- Error responses share one shape across the whole API:
```json
{
  "error": {
    "code": "validation_error",
    "message": "Allocations must sum to 100.",
    "fields": { "allocations": "Sum was 95, expected 100." }
  }
}
```

---

## 11. Documentation Generation

The OpenAPI schema is **generated directly from the DRF serializers/viewsets** (drf-spectacular) on every build, not hand-maintained separately — this is a hard requirement, not a nice-to-have, because a hand-written spec drifts from the real endpoints the moment someone changes a serializer without remembering to update a separate document. Any new endpoint is only "done" once it appears correctly in the generated schema.

---

## 12. Versioning

The API is unversioned in the URL for the graduation-project timeframe (single deployed frontend/backend pair, no external third-party consumers to protect from breaking changes). If external consumers are ever introduced, versioning would be added as a URL prefix (`/v1/...`) at that time — this is a forward note, not a current requirement, and should not be implemented speculatively before it's needed.
