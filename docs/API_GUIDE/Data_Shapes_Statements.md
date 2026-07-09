# Data Shapes & Query Params — Statements
**AI-Powered Personal Financial Advisor — Graduation Project (NBE)**

Per-endpoint spec for the Statements domain: document upload, OCR, and normalization results. See System Architecture §5 and Services and Background Tasks §3 for the pipeline these shapes travel through. Unless stated otherwise, every route requires a valid user JWT and is implicitly scoped to the authenticated caller.

---

## Statement Status & Retry Model

`statement_files.status` is one of `pending_extraction | pending_normalization | pending_approval | processed`, and reflects **the last successfully completed phase**, not a phase currently running or one that errored. There is no `record_created`/`stored`/`failed` status: a `StatementFile` row is only ever created once its raw file is successfully stored (see `POST /statements` below), so there is nothing to represent before that point, and a failed phase leaves `status` exactly where it was rather than moving to a dedicated failure state.

Two fields carry retry context instead:
- `failure_reason` (`string | null`) — set when the most recently attempted phase errored, cleared on the next successful phase.
- `failed_phase` (`"extraction" | "normalization" | null`) — which phase `failure_reason` refers to.

`PATCH /statements/{statement_id}` is the **only** retry mechanism, and only for the `extraction`/`normalization` phases — see below. A failure during file storage itself is not retryable at all: `POST /statements` returns an error and no row is persisted (nothing to retry against, nothing to `GET`); the client re-submits a fresh upload.

---

## POST /statements

**Auth:** Required · **Scoping:** implicit self · **Query params:** none

**Request** — `multipart/form-data`
```
file:        binary, required (pdf | jpg | png)
account_id:  uuid, optional  // if known upfront; the Normalization Agent may otherwise resolve/create one
```

**Behavior:** Uploads and stores the file, then auto-chains through extraction and normalization synchronously in the same call (the "one-shot" ingestion pipeline — Pipeline.md §2), stopping wherever a phase fails. The response always reflects however far the pipeline actually got.

**Response `202`** (async — API Design Guidelines §9)
```json
{
  "id": "uuid",
  "account_id": "uuid | null",
  "status": "string  // pending_extraction | pending_normalization | pending_approval",
  "failure_reason": "string | null",
  "failed_phase": "string | null  // extraction | normalization | null",
  "upload_date": "timestamp"
}
```

**Response `422`** if the file itself could not be stored (`code: "storage_failed"`) — no row is created; see "Statement Status & Retry Model" above. Also `422` on a duplicate checksum (`code: "duplicate_statement"`) or a malformed/missing `account_id`.

**Rate limiting note:** subject to the upload rate limit enforced at the Django middleware layer (Services and Background Tasks §8) — the route in this domain most exposed to abuse (repeated large uploads).

---

## GET /statements

**Auth:** Required · **Scoping:** implicit self · **Query params:** `status` (optional filter: `pending_extraction`/`pending_normalization`/`pending_approval`/`processed`), `account_id` (optional filter), `limit` (integer, optional, page size — frontend-controlled), `offset` (integer, optional, default 0) · **Pagination:** Offset — DRF `LimitOffsetPagination` (API Design Guidelines §5) · **Default sort:** `upload_date DESC` (most recent upload first, matches the Documents tab's reverse-chronological display — Design §6)

**Response `200`**
```json
{
  "count": "integer",
  "next": "string (url) | null",
  "previous": "string (url) | null",
  "results": [
    {
      "id": "uuid",
      "account_id": "uuid | null",
      "status": "string  // pending_extraction | pending_normalization | pending_approval | processed",
      "failure_reason": "string | null",
      "failed_phase": "string | null  // extraction | normalization | null",
      "start_transaction_date": "date | null",
      "last_transaction_date": "date | null",
      "upload_date": "timestamp"
    }
  ]
}
```

---

## GET /statements/{statement_id}

**Auth:** Required · **Scoping:** implicit self; `404` if not owned by caller · **Query params:** none

**Response `200`** — same shape as one item above, polled until `status` reaches `processed`, or until `failure_reason` is non-null and the client offers a retry via `PATCH` (API Design Guidelines §9).

---

## PATCH /statements/{statement_id}

**Auth:** Required · **Scoping:** implicit self; `404` if not owned by caller · **Query params:** none

**Request**
```json
{ "status": "pending_normalization" | "pending_approval" }
```

**Behavior:** Resumes/retries the pipeline from the statement's current status toward the requested target — never a general field update. Requesting a target further out than the next phase cascades through the intermediate ones in the same call (e.g. `pending_extraction → pending_approval` runs extraction then normalization). Only forward targets are accepted; `pending_extraction` and `processed` are never valid request values — the former has no runner to retry into it (file storage isn't retryable, see above), the latter is only reachable via `POST /statements/{statement_id}/transactions`.

**Response `200`** — same shape as `GET /statements/{statement_id}`. If a phase fails partway through a cascade, the response reflects wherever it stopped, with `failure_reason`/`failed_phase` set — this is not itself an error response.

**Response `422`** if `status` isn't one of the two valid target values, if the target isn't ahead of the statement's current status (`code: "invalid_status_transition"`), or if the statement is already `processed` (`code: "already_processed"`).

---

## DELETE /statements/{statement_id}

**Auth:** Required · **Scoping:** implicit self; `404` if not owned by caller · **Query params:** none

Removes the `statement_files` row and its raw/artifact files (subject to `retain_raw_documents`, File System Structure §2–3). **Does not** delete transactions already committed to the ledger from this statement — those are edited/removed individually via the Aggregations domain, since transactions are the single source of truth independent of their originating statement (Data Governance Specs §7).

**Response `204`** — no body.

---

## GET /statements/{statement_id}/ocr-result

**Auth:** Required · **Scoping:** implicit self, via parent statement ownership · **Query params:** none

**Response `200`**
```json
{
  "statement_id": "uuid",
  "ocr_engine": "string",
  "confidence_score": "number | null  // 0.000–1.000",
  "processed_at": "timestamp",
  "artifact_url": "string  // signed URL into the ocr/ folder, File System Structure §3"
}
```

**Response `404`** if OCR hasn't completed yet (`statement_files.status` still `pending_extraction`).

---

## GET /statements/{statement_id}/normalized

**Auth:** Required · **Scoping:** implicit self, via parent statement ownership · **Query params:** none

The proposed, **not-yet-committed** transaction batch for the user to review — nothing here is in the ledger until `POST /statements/{statement_id}/transactions` is called (see below). `duplicate_of` is a point-in-time check computed when normalization ran; it's advisory for display only and is re-checked for real at approval time, since another statement could have inserted a colliding row since.

**Response `200`**
```json
{
  "statement_id": "uuid",
  "model_used": "string | null",
  "adjusted_at": "timestamp",
  "transaction_count": "integer",
  "normalized_json": {
    "bank_name": "string",
    "account_hint": "string",
    "transactions": [
      {
        "transaction_date": "date",
        "merchant_raw": "string",
        "category": "string | null",
        "amount": "number",
        "transaction_type": "string  // debit | credit | fee | transfer",
        "duplicate_of": "uuid | null  // advisory only, see above"
      }
    ]
  }
}
```

**Response `404`** if normalization hasn't completed yet (`statement_files.status` not yet `pending_approval` or later).

---

## POST /statements/{statement_id}/transactions

**Auth:** Required · **Scoping:** implicit self; `404` if not owned by caller · **Query params:** none

Approves the whole proposed batch **atomically** — there is no per-transaction approval endpoint and no partial approval. Only valid while `status == "pending_approval"`. The submitted array must be the same length as `normalized_json.transactions` (rows are matched by position, not by an id — there is nothing else to address a row by in this design); a length mismatch is rejected rather than treated as a partial submission.

**Request**
```json
[
  {
    "transaction_date": "date",
    "merchant_raw": "string | null",
    "category": "string | null",
    "amount": "number",
    "transaction_type": "string | null"
  }
]
```
Any field here overrides the corresponding value the user saw in `GET .../normalized` — this is how in-flight corrections (a fixed category, a corrected amount) reach the ledger.

**Behavior:** For each row, the duplicate check (System Architecture §8) is re-run against the ledger at commit time. A duplicate is **skipped**, not treated as an error — it's reported back with `duplicate_of` set instead of `transaction_id`. Every other row is inserted into `transactions` with `source: "statement"` and `statement_id` set to this statement. Once every row is resolved, the statement advances straight to `status: "processed"`.

**Response `200`**
```json
{
  "statement_status": "processed",
  "resolved": [
    {
      "transaction_date": "date",
      "merchant_raw": "string | null",
      "amount": "number",
      "transaction_id": "uuid | null",
      "duplicate_of": "uuid | null"
    }
  ]
}
```

**Response `422`** if the statement isn't `pending_approval` (`code: "invalid_status_transition"` — covers both "not normalized yet" and "already processed") or if the submitted array's length doesn't match the proposed one (`code: "transaction_count_mismatch"`).

---

## Reference: Bank Statement Template (internal, no dedicated public route)

Included for completeness since it's read/written as part of this domain's pipeline:

```json
{
  "id": "uuid",
  "bank_name": "string",
  "layout_signature": "string",
  "column_mapping_json": { "<bank_column_name>": "<canonical_field_name>" },
  "date_format": "string",
  "created_at": "timestamp"
}
```

---

## Roles (domain-wide note)

End-user only. No admin variant — statements are never browsed cross-user through this API (Data Governance Specs §2: Statements is not a general document-management surface; Administration's remit is limited to Feedback and the Recommendation catalog, Data Governance Specs §8).
