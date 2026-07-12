# Data Shapes & Query Params — Statements
**AI-Powered Personal Financial Advisor — Graduation Project (NBE)**

Per-endpoint spec for the Statements domain: document upload, OCR, and normalization results. See System Architecture §5 and Services and Background Tasks §3 for the pipeline these shapes travel through. Unless stated otherwise, every route requires a valid user JWT and is implicitly scoped to the authenticated caller.

---

## Statement Status & Retry Model

`statement_files.status` is one of `uploaded | extracted | normalized | approved` — each name is **the phase that has already completed**, not the one pending (that's what `is_processing`/`failure_reason` below are for). `uploaded` means the upload step is done, not "an upload is pending" — this also means there's no verb-vs-status ambiguity the way a status literally named `approval` would have against the `approve` action on `POST .../transactions` below. No `pending_` prefix either: baking "pending" into the status name would just repeat what `is_processing` already says. There is also no `record_created`/`stored`/`failed` status: a `StatementFile` row is only ever created once its raw file is successfully stored (see `POST /statements` below), so there is nothing to represent before that point, and a failed phase leaves `status` exactly where it was rather than moving to a dedicated failure state.

Three fields carry retry/liveness context instead:
- `is_processing` (`boolean`) — true only while a phase runner is actively executing. Distinguishes "a background process is working on this right now" from "this phase stopped and is sitting idle" — without it, `uploaded` with no error would be ambiguous between those two states once processing isn't fully synchronous within one request. Always `false` in any response today (nothing runs across separate requests yet — see Pipeline.md §2), but the field exists so the status model stays correct once it does.
- `failure_reason` (`string | null`) — set when the most recently attempted phase errored, cleared on the next successful phase.
- `failed_phase` (`"extraction" | "normalization" | null`) — which phase's *activity* `failure_reason` refers to (the OCR run or the LLM normalization run) — a different vocabulary from `status` on purpose, since this names a process, not a completed milestone.

`PATCH /statements/{statement_id}` is the **only** retry mechanism, and only to advance toward `extracted`/`normalized` — see below. It also doubles as a guard against two overlapping retries on the same statement: a `PATCH` while `is_processing` is `true` is rejected (`code: "already_processing"`) rather than starting redundant/concurrent work. A failure during file storage itself is not retryable at all: `POST /statements` returns an error and no row is persisted (nothing to retry against, nothing to `GET`); the client re-submits a fresh upload.

---

## File Metadata & Proposed Transactions

The Statements responses split into two tiers by weight, so a document list stays light while the review screen gets everything:

### File metadata — on **every** statement shape, including the `GET /statements` list

`file_size`, `file_type`, `bank_name`, `account_hint`, `model_used`, and `adjusted_at` travel on the list rows as well as the single-resource shapes. This is the "which bank, what file, when parsed" a documents screen shows per row, so it needs no follow-up detail call.

- **`file_size`** (`integer | null`) — raw file size in bytes, captured at upload.
- **`file_type`** (`string | null`) — file extension (`pdf` | `jpg` | `png`), captured at upload.
- **`bank_name`**, **`account_hint`**, **`model_used`**, **`adjusted_at`** — historical facts about the normalization run (the `statement_normalized` row). `file_size`/`file_type` exist from creation; these appear once a `statement_normalized` row exists (`normalized` or later) and **stay populated after `approved`**, since they describe the file, not the mutable batch. `null` before then.

### Transactions — on the single-resource shapes only (`POST /statements`, `GET`/`PATCH /statements/{statement_id}`), **not** the list

`transactions` (`array | null`) is the heavy part, kept off the paginated list on purpose — the detail route is where a client goes to review/approve or inspect the ledger rows. Same field name, two different sources depending on `status`, so the frontend never needs a second field to know what it's looking at:

- `status == "normalized"`: the **not-yet-committed proposed batch** from `normalized_json`, for the user to review/correct before calling `POST /statements/{statement_id}/transactions`. Row shape:
  ```json
  {
    "transaction_date": "date",
    "merchant_raw": "string",
    "category": "string | null",
    "amount": "number",
    "transaction_type": "string  // debit | credit | fee | transfer",
    "duplicate_of": "uuid | null  // advisory only — a point-in-time check from when normalization ran, re-checked for real at approval time"
  }
  ```
- `status == "approved"`: the **real ledger rows** this statement produced (`GET /transactions`'s item shape — `id`, `account_id`, `statement_id`, `merchant_normalized`, `confidence_score`, `balance`, `created_at`, etc. — see Data Shapes Aggregations), read live off `transactions` via `statement_id`. Not a second copy of the data — the ledger stays the single source of truth (Data Governance Specs §7); this is a read-through, not a duplicate write.
- `uploaded` / `extracted`: `null` — nothing to show yet.

---

## POST /statements

**Auth:** Required · **Scoping:** implicit self · **Query params:** none

**Request** — `multipart/form-data`
```
file:        binary, required (pdf | jpg | png)
status:      "extracted" | "normalized", optional, default "normalized"  // how far to auto-chain in this call
```
No `account_id` here — the Normalization Agent always infers/resolves the account from OCR output once extraction runs (`bank_name`/`account_hint` below); the client confirms or corrects it at approval time instead (`POST /statements/{statement_id}/transactions`'s optional `account_id`, below), not at upload time. `GET /accounts?masked_account_number=...&bank_name=...` lets the frontend check whether the inferred account already exists before the user confirms.

**Behavior:** Uploads and stores the file, then auto-chains the pipeline synchronously in the same call, up through `status` (or all the way to `normalized` — the original always-chain-to-the-end behavior — if omitted), stopping earlier if a phase fails. Pass `"extracted"` to stop right after extraction instead of running normalization too. Both this and `PATCH /statements/{statement_id}` drive the pipeline through the same underlying function, so the same rules apply to both — a target that isn't `extracted`/`normalized` is rejected the same way in either place.

**Response `202`** (async — API Design Guidelines §9)
```json
{
  "id": "uuid",
  "account_id": "uuid | null",
  "status": "string  // uploaded | extracted | normalized",
  "is_processing": "boolean  // always false here today — see 'Statement Status & Retry Model' above",
  "failure_reason": "string | null",
  "failed_phase": "string | null  // extraction | normalization | null",
  "file_size": "integer | null  // bytes",
  "file_type": "string | null  // pdf | jpg | png",
  "bank_name": "string | null",
  "account_hint": "string | null",
  "model_used": "string | null",
  "adjusted_at": "timestamp | null",
  "upload_date": "timestamp",
  "transactions": "array | null  // detail shape only — see 'File Metadata & Proposed Transactions' above"
}
```

**Response `422`** if the file itself could not be stored (`code: "storage_failed"`) — no row is created; see "Statement Status & Retry Model" above. Also `422` on a duplicate checksum (`code: "duplicate_statement"`).

**Rate limiting note:** subject to the upload rate limit enforced at the Django middleware layer (Services and Background Tasks §8) — the route in this domain most exposed to abuse (repeated large uploads).

---

## GET /statements

**Auth:** Required · **Scoping:** implicit self · **Query params:** `status` (optional filter: `uploaded`/`extracted`/`normalized`/`approved`), `account_id` (optional filter), `limit` (integer, optional, page size — frontend-controlled), `offset` (integer, optional, default 0) · **Pagination:** Offset — DRF `LimitOffsetPagination` (API Design Guidelines §5) · **Default sort:** `upload_date DESC` (most recent upload first, matches the Documents tab's reverse-chronological display — Design §6)

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
      "status": "string  // uploaded | extracted | normalized | approved",
      "is_processing": "boolean",
      "failure_reason": "string | null",
      "failed_phase": "string | null  // extraction | normalization | null",
      "file_size": "integer | null  // bytes",
      "file_type": "string | null  // pdf | jpg | png",
      "bank_name": "string | null",
      "account_hint": "string | null",
      "model_used": "string | null",
      "adjusted_at": "timestamp | null",
      "start_transaction_date": "date | null",
      "last_transaction_date": "date | null",
      "upload_date": "timestamp"
    }
  ]
}
```

The list carries the file metadata (`file_size`/`file_type`/`bank_name`/`account_hint`/`model_used`/`adjusted_at`) but **not** `transactions` — that's on the single-resource shapes only. See "File Metadata & Proposed Transactions" above.

---

## GET /statements/{statement_id}

**Auth:** Required · **Scoping:** implicit self; `404` if not owned by caller · **Query params:** none

**Response `200`** — same shape as the `POST /statements` response: the list item fields (including the file metadata) **plus** `transactions`, see "File Metadata & Proposed Transactions" above. Polled until `status` reaches `approved`, or until `failure_reason` is non-null and the client offers a retry via `PATCH` (API Design Guidelines §9).

---

## PATCH /statements/{statement_id}

**Auth:** Required · **Scoping:** implicit self; `404` if not owned by caller · **Query params:** none

**Request**
```json
{ "status": "extracted" | "normalized" }
```

**Behavior:** Resumes/retries the pipeline from the statement's current status toward the requested target — never a general field update. Requesting a target further out than the next phase cascades through the intermediate ones in the same call (e.g. `uploaded → normalized` runs extraction then normalization). Only forward targets are accepted; `uploaded` and `approved` are never valid request values — the former has no runner to retry into it (file storage isn't retryable, see above), the latter is only reachable via `POST /statements/{statement_id}/transactions`.

**Response `200`** — same shape as `GET /statements/{statement_id}`. If a phase fails partway through a cascade, the response reflects wherever it stopped, with `failure_reason`/`failed_phase` set — this is not itself an error response.

**Response `422`** if `status` isn't one of the two valid target values, if the target isn't ahead of the statement's current status (`code: "invalid_status_transition"`), if the statement is already `approved` (`code: "already_approved"`), or if a phase is already running on this statement (`code: "already_processing"` — guards against two overlapping retries).

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
  "artifact_url": "string  // GET /statements/{statement_id}/ocr-result/download — proxied by Django, not a signed SeaweedFS URL (SeaweedFS is never exposed publicly, System_Architecture.md §2/§10)"
}
```

**Response `404`** if OCR hasn't completed yet (`statement_files.status` still `uploaded`).

---

## GET /statements/{statement_id}/ocr-result/download

**Auth:** Required · **Scoping:** implicit self, via parent statement ownership · **Query params:** none

Streams the OCR artifact's `document.md` (the markdown representation, File_System_Structure.md §3) through Django — SeaweedFS is never reachable directly by a client, so this endpoint proxies the bytes rather than redirecting to a signed URL into it. The OCR bucket also holds `content.json`/`images/`/`tables/`, but those feed the normalization step, not something a user downloads directly.

**Response `200`**: the raw `document.md` bytes, `Content-Type: text/markdown`, `Content-Disposition: attachment`.

**Response `404`** if OCR hasn't completed yet, or the AI service hasn't written `document.md` for this statement.

---

## POST /statements/{statement_id}/transactions

**Auth:** Required · **Scoping:** implicit self; `404` if not owned by caller · **Query params:** none

Approves the whole proposed batch **atomically** — there is no per-transaction approval endpoint and no partial approval. Only valid while `status == "normalized"`. The `transactions` array must be the same length as the array returned inline by `GET`/`PATCH /statements/{statement_id}` (rows are matched by position, not by an id — there is nothing else to address a row by in this design); a length mismatch is rejected rather than treated as a partial submission.

This is also the one and only account-confirmation moment: `account_id`, if present, confirms or overrides the account the Normalization Agent inferred from OCR (`bank_name`/`account_hint` on `GET /statements/{statement_id}`) — the client never supplies an account at upload time (see `POST /statements` above).

**Request**
```json
{
  "account_id": "uuid, optional  // confirms/overrides the OCR-inferred account",
  "transactions": [
    {
      "transaction_date": "date",
      "merchant_raw": "string | null",
      "category": "string | null",
      "amount": "number",
      "transaction_type": "string | null"
    }
  ]
}
```
Any field on a `transactions` row overrides the corresponding value the user saw in the inline `transactions` array — this is how in-flight corrections (a fixed category, a corrected amount) reach the ledger. Each row is committed as-is from its own submitted data, so the array need not match the proposed one in length or order: a row may be edited, dropped (OCR invented it) or added (OCR missed it) during review.

**Contract change note:** this request body used to be the bare array directly (no wrapper) — wrapping it in `{"account_id", "transactions"}` was a deliberate change (PLAN.md Checkpoint A) to make room for account confirmation, not an oversight.

**Behavior:** For each row, the duplicate check (System Architecture §8) is re-run against the ledger at commit time. A duplicate is **skipped**, not treated as an error — it's reported back with `duplicate_of` set instead of `transaction_id`. Every other row is inserted into `transactions` with `source: "statement"` and `statement_id` set to this statement. Once every row is resolved, the statement advances straight to `status: "approved"` — this is the one endpoint that action names itself after, unlike the status names elsewhere in this pipeline.

**Response `200`**
```json
{
  "statement_status": "approved",
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

**Response `422`** if the statement isn't `normalized` (`code: "invalid_status_transition"` — covers both "not normalized yet" and "already approved").

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
