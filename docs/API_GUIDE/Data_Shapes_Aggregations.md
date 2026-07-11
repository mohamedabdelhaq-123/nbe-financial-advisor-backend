# Data Shapes & Query Params — Aggregations
**AI-Powered Personal Financial Advisor — Graduation Project (NBE)**

Per-endpoint spec for the Aggregations domain: Transactions (the single source of truth, Data Governance Specs §7) and the read-only Analytics endpoints computed from it. Unless stated otherwise, every route requires a valid user JWT and is implicitly scoped to the authenticated caller.

---

## GET /transactions

**Auth:** Required · **Scoping:** implicit self · **Query params:** `account_id`, `category`, `from` (date), `to` (date), `source` (statement/manual/chat), `is_recurring` (bool), `search` (matches `merchant_raw`/`merchant_normalized`, case-insensitive substring), `min_amount`, `max_amount`, `transaction_type` (debit/credit/fee/transfer), `limit` (integer, optional, page size — frontend-controlled), `offset` (integer, optional, default 0) · **Pagination:** **Offset** — DRF `LimitOffsetPagination` — bounded, filterable, randomly-accessed (API Design Guidelines §5, reference case) · **Default sort:** `-transaction_date` (most recent first); overridable via `?sort=` with `amount`, `-amount`, `transaction_date`, `-transaction_date`, `category`, `-category`, `merchant_normalized`, `-merchant_normalized`, `created_at`, `-created_at`

**Response `200`**
```json
{
  "count": "integer",
  "next": "string (url) | null",
  "previous": "string (url) | null",
  "results": [
    {
      "id": "uuid",
      "account_id": "uuid",
      "statement_id": "uuid | null",
      "transaction_date": "date",
      "merchant_raw": "string | null",
      "merchant_normalized": "string | null",
      "category": "string | null",
      "amount": "number",
      "currency": "string",
      "is_recurring": "boolean",
      "confidence_score": "number | null",
      "source": "string  // statement | manual | chat",
      "balance": "number | null",
      "transaction_type": "string | null  // debit | credit | fee | transfer",
      "created_at": "timestamp"
    }
  ]
}
```

---

## GET /transactions/{transaction_id}

**Auth:** Required · **Scoping:** implicit self; `404` if not owned by caller · **Query params:** none

**Response `200`** — same shape as one item above, plus:
```json
{ "extra_fields": "object | null  // bank-specific catch-all, rarely surfaced in UI" }
```

---

## POST /transactions

**Auth:** Required · **Scoping:** implicit self · **Query params:** none

**Request**
```json
{
  "account_id": "uuid, required",
  "transaction_date": "date, required",
  "merchant_raw": "string, required",
  "category": "string, optional",
  "amount": "number, required",
  "currency": "string, optional, default matches account currency",
  "transaction_type": "string, required  // debit | credit | fee | transfer"
}
```
`source` is set server-side to `manual` (or `chat` if the request originates from the assistant's confirmable widget path — Architectural Guidelines §7) — never client-supplied. `is_recurring` and `confidence_score` are backend-computed, not accepted on write.

**Response `201`** — same shape as `GET /transactions/{transaction_id}`.
**Response `422`** on duplicate match — standard error shape, `code: "duplicate_transaction"`, referencing the existing `transaction_id` in `fields`.

---

## PATCH /transactions/{transaction_id}

**Auth:** Required · **Scoping:** implicit self; `404` if not owned by caller · **Query params:** none

**Request** — any subset: `{ "category", "merchant_raw", "amount", "transaction_date", "transaction_type" }`. `account_id` and `source` are not patchable (would misrepresent the transaction's origin).

**Response `200`** — same shape as `GET /transactions/{transaction_id}`.

---

## DELETE /transactions/{transaction_id}

**Auth:** Required · **Scoping:** implicit self; `404` if not owned by caller · **Query params:** none

**Response `204`** — no body. Triggers the same re-aggregation background tasks as an edit (Services and Background Tasks §5).

---

## GET /analytics/monthly-summaries

**Auth:** Required · **Scoping:** implicit self · **Query params:** `account_id` (optional; omit for all-accounts combined), `from` (month), `to` (month) · **Pagination:** none — small, bounded result set per user (one row per month) · **Default sort:** `month DESC`

**Response `200`** — array:
```json
[
  {
    "month": "date  // first day of month",
    "account_id": "uuid | null  // null = all accounts combined",
    "total_spend": "number | null",
    "total_inflow": "number | null",
    "category_breakdown": { "<category>": "number" },
    "top_merchants": [{ "merchant": "string", "total": "number" }]
  }
]
```

---

## GET /analytics/category-breakdown

**Auth:** Required · **Scoping:** implicit self · **Query params:** `period` (required, e.g. `2026-07`), `account_id` (optional) · **Pagination:** none

**Response `200`**
```json
{
  "period": "string  // e.g. \"2026-07\"",
  "breakdown": [{ "category": "string", "amount": "number", "percentage_of_total": "number" }]
}
```

---

## GET /analytics/recurring-charges

**Auth:** Required · **Scoping:** implicit self · **Query params:** `account_id` (optional) · **Pagination:** none

**Response `200`** — array:
```json
[
  {
    "id": "uuid",
    "merchant_normalized": "string",
    "frequency": "string  // monthly | weekly | yearly",
    "avg_amount": "number | null",
    "last_occurrence_date": "date | null",
    "next_expected_date": "date | null"
  }
]
```

---

## GET /analytics/anomalies

**Auth:** Required · **Scoping:** implicit self · **Query params:** `severity` (low/medium/high), `resolved` (bool) · **Pagination:** none — best-effort/bounded set per user

**Response `200`** — array:
```json
[
  {
    "id": "uuid",
    "transaction_id": "uuid",
    "reason": "string",
    "severity": "string  // low | medium | high",
    "resolved": "boolean",
    "detected_at": "timestamp"
  }
]
```

---

## PATCH /analytics/anomalies/{anomaly_id}

**Auth:** Required · **Scoping:** implicit self via the underlying transaction's ownership; `404` otherwise · **Query params:** none

**Request**
```json
{ "resolved": "boolean, required" }
```
This is one of the few endpoints where an optimistic UI update is acceptable (Architectural Guidelines §5).

**Response `200`** — same shape as one item in `GET /analytics/anomalies`.

---

## GET /analytics/spending-insights

**Auth:** Required · **Scoping:** implicit self · **Query params:** `insight_type` (optional filter), `period` (optional filter) · **Pagination:** none

**Response `200`** — array (extensible, typed structure — Data Governance Specs §7):
```json
[
  {
    "insight_type": "string  // income_stability | savings_rate_trend | category_overspend | overdraft_risk | cash_flow | merchant_frequency | time_of_month_pattern | category_volatility | debt_service_ratio_proxy",
    "period": "string | null",
    "value": "object  // shape varies by insight_type, see below",
    "created_at": "timestamp"
  }
]
```
`value` shape examples: `income_stability` → `{ "score": number (0-1), "trend": "improving|stable|declining" }`; `category_overspend` → `{ "category": string, "over_by_amount": number, "over_by_percentage": number }`.

---

## GET /analytics/net-worth

**Auth:** Required · **Scoping:** implicit self · **Query params:** `as_of` (date, optional — defaults to latest snapshot) · **Pagination:** none

**Response `200`**
```json
{
  "as_of_date": "date",
  "total_across_accounts": "number",
  "per_account_breakdown": [{ "account_id": "uuid", "bank_name": "string", "balance": "number" }]
}
```

---

## GET /analytics/stability-score

**Auth:** Required · **Scoping:** implicit self · **Query params:** `period` (optional) · **Pagination:** none

**Response `200`**
```json
{
  "score": "number  // 0-100",
  "label": "string  // e.g. \"stable\", \"variable\"",
  "computed_for_period": "string"
}
```

---

## Roles (domain-wide note)

All end-user only, implicitly self-scoped. No admin cross-user variant exists for Aggregations — financial transaction data is the most sensitive domain in the system and is never exposed through an admin-scoped route (Data Governance Specs §8 explicitly limits Administration to Feedback and the Recommendation catalog).
