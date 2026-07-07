# Data Shapes & Query Params — Statements
**AI-Powered Personal Financial Advisor — Graduation Project (NBE)**

Per-endpoint spec for the Statements domain: document upload, OCR, and normalization results. See System Architecture §5 and Services and Background Tasks §3 for the pipeline these shapes travel through. Unless stated otherwise, every route requires a valid user JWT and is implicitly scoped to the authenticated caller.

---

## POST /statements

**Auth:** Required · **Scoping:** implicit self · **Query params:** none

**Request** — `multipart/form-data`
```
file:        binary, required (pdf | jpg | png)
account_id:  uuid, optional  // if known upfront; the Normalization Agent may otherwise resolve/create one
```

**Response `202`** (async — API Design Guidelines §9)
```json
{
  "id": "uuid",
  "status": "pending",
  "upload_date": "timestamp"
}
```

**Rate limiting note:** subject to the upload rate limit enforced at the Django middleware layer (Services and Background Tasks §8) — the route in this domain most exposed to abuse (repeated large uploads).

---

## GET /statements

**Auth:** Required · **Scoping:** implicit self · **Query params:** `status` (optional filter: pending/processing/normalized/failed), `account_id` (optional filter), `limit` (integer, optional, page size — frontend-controlled), `offset` (integer, optional, default 0) · **Pagination:** Offset — DRF `LimitOffsetPagination` (API Design Guidelines §5) · **Default sort:** `upload_date DESC` (most recent upload first, matches the Documents tab's reverse-chronological display — Design §6)

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
      "status": "string  // pending | processing | normalized | failed",
      "start_transaction_date": "date | null",
      "last_transaction_date": "date | null",
      "upload_date": "timestamp",
      "failure_reason": "string | null  // present only when status = failed"
    }
  ]
}
```

---

## GET /statements/{statement_id}

**Auth:** Required · **Scoping:** implicit self; `404` if not owned by caller · **Query params:** none

**Response `200`** — same shape as one item above, polled until `status` reaches `normalized` or `failed` (API Design Guidelines §9).

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

**Response `404`** if OCR hasn't completed yet (`statement_files.status` still `pending`).

---

## GET /statements/{statement_id}/normalized

**Auth:** Required · **Scoping:** implicit self, via parent statement ownership · **Query params:** none

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
        "duplicate_of": "uuid | null  // set if the duplicate check matched an existing transaction"
      }
    ]
  }
}
```

**Response `404`** if normalization hasn't completed yet.

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
