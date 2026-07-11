# Data Shapes & Query Params — Budgets
**AI-Powered Personal Financial Advisor — Graduation Project (NBE)**

Per-endpoint spec for the Budgets domain: the single active plan (`budgets`, 1:1 with a user), its allocations, history, and the Dashboard aggregate that reads from it. Follows the percentage + derived-amount convention and the goal object convention (API Design Guidelines §3–4) throughout. Unless stated otherwise, every route requires a valid user JWT and is implicitly scoped to the authenticated caller.

---

## GET /budget

**Auth:** Required · **Scoping:** implicit self (one row per user — no listing needed) · **Query params:** none

**Response `200`**
```json
{
  "id": "uuid",
  "name": "string",
  "period_type": "string  // monthly",
  "status": "string  // active",
  "selected_template_key": "string | null",
  "allocations": [
    {
      "category": "string",
      "allocated_percentage": "number",
      "allocated_amount": "number",
      "currency": "string"
    }
  ],
  "created_at": "timestamp",
  "updated_at": "timestamp"
}
```
No `goal` key here — Goal is its own entity (see `GET/POST/PATCH/DELETE /goal` below), independent of whether a budget plan exists. Reach it via `GET /goal` or `GET /dashboard`.

**Response `404`** if the user hasn't created a plan yet (Design §3 — a real, designed empty state, not an error state, in the frontend's handling of it).

---

## POST /budget

**Auth:** Required · **Scoping:** implicit self · **Query params:** none

Creates the initial plan (onboarding step 5, or if no plan currently exists). Rejected `409` if a `budgets` row already exists for the user — use `PATCH /budget` instead (Data Governance Specs §4: one plan per user, no parallel rows).

**Request**
```json
{
  "name": "string, optional, defaults to \"My Plan\"",
  "selected_template_key": "string, optional",
  "allocations": [
    { "category": "string, required", "allocated_percentage": "number, required" }
  ]
}
```
No `goal` here — set/update a goal separately via `POST`/`PATCH /goal` (or the `PATCH /dashboard/goal` convenience alias), independent of budget creation.

Percentages across `allocations` must sum to 100 (`422` otherwise, API Design Guidelines §3). `allocated_amount` is never sent by the client — computed server-side from `users.monthly_income`.

**Response `201`** — same shape as `GET /budget`.

---

## PATCH /budget

**Auth:** Required · **Scoping:** implicit self · **Query params:** none

Updates allocations and/or the goal on the existing plan — the single write path for both the dashboard edit and the chat HITL confirm (Data Governance Specs §4). A `budget_history` snapshot is written server-side before applying the change.

**Request** — any subset:
```json
{
  "name": "string",
  "allocations": [
    { "category": "string, required", "allocated_percentage": "number, required" }
  ],
  "changed_via": "string, optional  // dashboard | chat_hitl — informational, defaults to dashboard"
}
```
No `goal` here — see `POST /budget`'s note above. If `allocations` is included, it replaces the full set (not a partial merge) and must sum to 100.

**Response `200`** — same shape as `GET /budget`.

---

## GET /budget/history

**Auth:** Required · **Scoping:** implicit self, via the owning `budget_id` · **Query params:** `from` (date, optional), `to` (date, optional), `limit` (integer, optional, page size — frontend-controlled), `offset` (integer, optional, default 0) · **Pagination:** Offset — DRF `LimitOffsetPagination` — bounded but can grow over a long account lifetime; offset chosen over cursor since a user may want to jump to "changes around a specific date," a randomly-accessed pattern (API Design Guidelines §5) · **Default sort:** `changed_at DESC` (most recent change first)

**Response `200`**
```json
{
  "count": "integer",
  "next": "string (url) | null",
  "previous": "string (url) | null",
  "results": [
    {
      "id": "uuid",
      "previous_values": {
        "allocations": [{ "category": "string", "allocated_percentage": "number", "allocated_amount": "number" }]
      },
      "changed_via": "string  // dashboard | chat_hitl | onboarding",
      "changed_at": "timestamp"
    }
  ]
}
```
No `goal` in `previous_values` anymore — Goal is its own entity with no history/versioning of its own (this only tracks Budget's own fields).

---

## GET /budget/progress

**Auth:** Required · **Scoping:** implicit self · **Query params:** `period` (optional, defaults to current period) · **Pagination:** none

**Response `200`**
```json
{
  "period": "string  // e.g. \"2026-07\"",
  "categories": [
    {
      "category": "string",
      "allocated_amount": "number",
      "actual_amount": "number",
      "percentage_used": "number",
      "status": "string  // on_track | approaching_limit | over_budget"
    }
  ]
}
```

---

## GET /budget/savings-progress

**Auth:** Required · **Scoping:** implicit self · **Query params:** none

No longer requires a budget plan to exist — only a `Goal` (its own entity, independent of `Budget`; see `GET /goal` below). `404` if the user has no goal set.

**Response `200`**
```json
{
  "goal": { "name": "string", "target_amount": "number", "months_remaining": "integer" },
  "saved_so_far": "number",
  "percentage_complete": "number",
  "projected_completion_date": "date | null",
  "on_track": "boolean"
}
```
Progress is tracked from the goal's own creation date (`Goal.created_at`), not the budget plan's — fixes a prior bug where progress was always computed as of "whenever the plan was first created," which could badly undercount saved amounts for a goal added well after the plan.

---

## GET/POST/PATCH/DELETE /goal

**Auth:** Required · **Scoping:** implicit self (one row per user, `OneToOneField` — no listing needed) · **Query params:** none

The user's single savings goal — its own entity, independent of `Budget` (whether or not a budget plan exists). "Optional" means no row exists at all when unset, not a budget with null-ish goal fields.

**GET — Response `200`**
```json
{ "name": "string", "target_amount": "number", "months_remaining": "integer", "percentage_complete": "number" }
```
**Response `404`** if no goal is set yet.

**POST — Request** (all fields required)
```json
{ "name": "string, required", "target_amount": "number, required", "target_months": "integer, required" }
```
**Response `201`** — same shape as `GET /goal`. **Response `409`** if a goal already exists (`PATCH /goal` to update it instead).

**PATCH — Request** (any subset)
```json
{ "name": "string", "target_amount": "number", "target_months": "integer" }
```
**Response `200`** — same shape as `GET /goal`. **Response `404`** if no goal exists yet (`POST /goal` to create one).

**DELETE — Response `204`.** Goes back to "no goal" — `GET /goal`/`GET /dashboard`'s `goal` key return `404`/`null` respectively afterward.

---

## GET /budget/starter-templates

**Auth:** Public (`AllowAny`) — the frontend renders these during onboarding, before the user has an account/token. The templates are reference data, not user-scoped (Data Governance Specs §4; File System Structure §4), so nothing is leaked by serving them unauthenticated. The `is_suggested` flag is the only user-dependent part: when a valid token *is* present it's tailored to that user's income/dependents signals; unauthenticated (or for a user with no qualifying signals) it falls back to flagging `balanced`. · **Scoping:** none · **Query params:** none

**Response `200`** — array, one flagged `is_suggested: true` (tailored to the user's income/goal inputs when authenticated, else `balanced`):
```json
[
  {
    "template_key": "string",
    "name": "string  // e.g. \"Balanced\"",
    "description": "string",
    "is_suggested": "boolean",
    "allocations": [{ "category": "string", "allocated_percentage": "number" }]
  }
]
```

---

## GET /dashboard

**Auth:** Required · **Scoping:** implicit self · **Query params:** none

Aggregate endpoint (API Design Guidelines §7) — plan, goal, metrics, and net worth in one call.

**Response `200`**
```json
{
  "budget": { "id": "uuid", "name": "string", "status": "string" },
  "goal": { "name": "string", "target_amount": "number", "months_remaining": "integer", "percentage_complete": "number" },
  "allocations_summary": [{ "category": "string", "allocated_percentage": "number", "percentage_used": "number" }],
  "metrics": {
    "income_stability_score": "number | null",
    "current_month_spend": "number",
    "current_month_inflow": "number"
  },
  "net_worth": { "total_across_accounts": "number", "as_of_date": "date" },
  "has_plan": "boolean  // false triggers the empty-state design, Design §3"
}
```

---

## PATCH /dashboard/goal

**Auth:** Required · **Scoping:** implicit self · **Query params:** none

Convenience **upsert** alias for the user's `Goal` (creates it if it doesn't exist yet, updates it if it does) — operates on the standalone `Goal` entity, same as `POST`/`PATCH /goal` above, not nested `Budget` fields.

**Request**
```json
{ "goal": { "name": "string", "target_amount": "number", "target_months": "integer" } }
```

**Response `200`** — same `goal` shape as `GET /dashboard`.

---

## Roles (domain-wide note)

End-user only. No admin variant — an internal reviewer never sees a specific user's plan or goal through this API (Data Governance Specs §8).
