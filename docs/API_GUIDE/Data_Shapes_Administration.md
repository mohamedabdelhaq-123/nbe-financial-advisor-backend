# Data Shapes & Query Params — Administration
**AI-Powered Personal Financial Advisor — Graduation Project (NBE)**

Per-endpoint spec for the Administration domain (internal-only — Data Governance Specs §8): reviewing Feedback, and managing the Recommendation product catalog. Every route here is `[admin]` per API Endpoints §12 — a completely separate credential space from end-user auth (API Design Guidelines §8), and the one deliberate exception to the implicit-self-scoping rule that governs every other domain (API Design Guidelines §6).

---

## POST /admin/auth/login

**Auth:** None required (pre-auth) · **Scoping:** n/a · **Query params:** none

**Request**
```json
{ "email": "string, required", "password": "string, required" }
```

**Response `200`**
```json
{ "access_token": "string", "refresh_token": "string", "admin_id": "uuid", "role": "string  // reviewer | super_admin" }
```
This token is never interchangeable with an end-user token — the AI service and every end-user route reject it outright.

---

## GET /admin/feedback

**Auth:** Admin token required — role: `reviewer` or `super_admin` · **Scoping:** cross-user by design — no ownership filter · **Query params:** `target_type` (optional filter), `rating` (optional filter), `from`/`to` (date range), `limit` (integer, optional, page size — frontend-controlled), `offset` (integer, optional, default 0) · **Pagination:** Offset — DRF `LimitOffsetPagination` · **Default sort:** `created_at DESC` (newest first, matching a reviewer's "what's new" workflow)

Reads across all users' `reactions` rows — the one place in the system this is legitimately cross-user.

**Response `200`**
```json
{
  "count": "integer",
  "next": "string (url) | null",
  "previous": "string (url) | null",
  "results": [
    {
      "id": "uuid",
      "user_id": "uuid  // opaque reference, not expanded to full profile — Administration owns no user profile data itself",
      "target_type": "string  // transaction | recommendation | message | budget",
      "target_id": "uuid",
      "rating": "integer | null",
      "comment": "string | null",
      "created_at": "timestamp"
    }
  ]
}
```

---

## GET /admin/issues

**Auth:** Admin token — `reviewer` or `super_admin` · **Scoping:** cross-user by design · **Query params:** `status` (optional filter), `limit` (integer, optional, page size — frontend-controlled), `offset` (integer, optional, default 0) · **Pagination:** Offset — DRF `LimitOffsetPagination` · **Default sort:** `created_at DESC`

**Response `200`**
```json
{
  "count": "integer",
  "next": "string (url) | null",
  "previous": "string (url) | null",
  "results": [
    {
      "id": "uuid",
      "user_id": "uuid",
      "description": "string",
      "status": "string  // open | in_review | resolved | dismissed",
      "created_at": "timestamp",
      "resolved_at": "timestamp | null"
    }
  ]
}
```

---

## PATCH /admin/issues/{issue_id}

**Auth:** Admin token — `reviewer` or `super_admin` · **Scoping:** cross-user by design · **Query params:** none

**Request**
```json
{ "status": "string, required  // open | in_review | resolved | dismissed" }
```
Setting `status` to `resolved` or `dismissed` sets `resolved_at` server-side; moving back to `open`/`in_review` clears it.

**Response `200`** — same shape as one item in `GET /admin/issues`.

---

## GET /admin/products

**Auth:** Admin token — any role · **Scoping:** not user-scoped (catalog is global, not per-user) · **Query params:** `is_active` (optional filter), `category` (optional filter), `limit` (integer, optional, page size — frontend-controlled), `offset` (integer, optional, default 0) · **Pagination:** Offset — DRF `LimitOffsetPagination` — catalog is small (Data Governance Specs §6), but kept consistent with the rest of the API rather than special-cased to "no pagination" · **Default sort:** `created_at ASC` (catalog order, stable for editing)

**Response `200`** — includes inactive products (unlike the user-facing `GET /recommendations`, which only ever surfaces active, matched ones):
```json
{
  "count": "integer",
  "next": "string (url) | null",
  "previous": "string (url) | null",
  "results": [
    {
      "id": "uuid",
      "title": "string",
      "description": "string",
      "categories": ["string"],
      "tags": ["string"],
      "features": "object",
      "external_link": "string | null",
      "is_active": "boolean",
      "created_at": "timestamp"
    }
  ]
}
```

---

## POST /admin/products

**Auth:** Admin token — `super_admin` only · **Scoping:** not user-scoped · **Query params:** none

**Request**
```json
{
  "title": "string, required",
  "description": "string, required",
  "categories": ["string"],
  "tags": ["string"],
  "features": "object, optional",
  "external_link": "string, optional",
  "is_active": "boolean, optional, default true",
  "problem_statements": ["string, optional  // seed text(s) for embedding generation — see AI service /internal/embed"]
}
```

**Response `201`** — same shape as one item in `GET /admin/products`. Embedding generation for `problem_statements` happens asynchronously; the product is usable for direct display immediately, but not yet matchable via semantic search until embeddings finish.

---

## PATCH /admin/products/{product_id}

**Auth:** Admin token — `super_admin` only · **Scoping:** not user-scoped · **Query params:** none

**Request** — any subset of the writable fields in `POST /admin/products`.
**Response `200`** — same shape as one item in `GET /admin/products`.

---

## DELETE /admin/products/{product_id}

**Auth:** Admin token — `super_admin` only · **Scoping:** not user-scoped · **Query params:** none

Hard delete, cascades to `problem_statements` and `recommendation_logs` for this product (Database Schema — `ON DELETE CASCADE`). Prefer `PATCH { "is_active": false }` over deletion where the product might be reinstated later, since deletion also erases historical `recommendation_logs` context for any users it was previously shown to.

**Response `204`** — no body.

---

## Roles & Boundaries (domain-wide note)

**Role split:** `reviewer` can read Feedback/Issues and update issue status, but cannot create, edit, or delete catalog products — that write power is reserved for `super_admin`, since a bad catalog edit is visible to every end user immediately (via `GET /recommendations`), unlike a feedback review action which has no user-facing side effect.

**Hard boundary:** no route in this domain, regardless of role, can read a specific end user's `transactions`, `budgets`, or conversation content — Administration's data access is limited to exactly what's listed here (Data Governance Specs §8). Any future need to go further than this must be treated as a new, explicitly-scoped feature, not an extension of an existing admin route's filters.
