# Data Shapes & Query Params — Conversations
**AI-Powered Personal Financial Advisor — Graduation Project (NBE)**

Per-endpoint spec for the Conversations domain (LLM Sessions). See Pipeline §3 for how these messages are produced, and Architectural Guidelines §7 for how structured `widget` payloads are rendered on the frontend. Unless stated otherwise, every route requires a valid user JWT and is implicitly scoped to the authenticated caller.

---

## POST /chat/conversations

**Auth:** Required · **Scoping:** implicit self · **Query params:** none

**Request** — empty body (a session needs no initial data; freely created per Data Governance Specs §3).

**Response `201`**
```json
{
  "id": "uuid",
  "started_at": "timestamp",
  "last_message_at": "timestamp",
  "status": "active"
}
```

---

## GET /chat/conversations

**Auth:** Required · **Scoping:** implicit self · **Query params:** `status` (active/closed, optional filter), `limit` (integer, optional, page size — frontend-controlled), `offset` (integer, optional, default 0) · **Pagination:** Offset — DRF `LimitOffsetPagination` — bounded, and a user may want to jump into an older session directly, matching the offset case (API Design Guidelines §5) · **Default sort:** `last_message_at DESC` (most recently active first)

**Response `200`**
```json
{
  "count": "integer",
  "next": "string (url) | null",
  "previous": "string (url) | null",
  "results": [
    {
      "id": "uuid",
      "started_at": "timestamp",
      "last_message_at": "timestamp",
      "status": "string  // active | closed",
      "preview": "string | null  // first ~80 chars of the most recent message, for a session list UI"
    }
  ]
}
```

---

## GET /chat/conversations/{conversation_id}/messages

**Auth:** Required · **Scoping:** implicit self, via the owning `conversation_id` · **Query params:** `stage` (optional filter), `cursor` (string, optional) · **Pagination:** **Cursor** — DRF `CursorPagination` (response `next`/`previous` cursor URLs) — unbounded, append-only, chronologically scrolled (API Design Guidelines §5, reference case). Deliberately **not** `LimitOffsetPagination` even though that would also give the frontend page-size control, because offset-based paging is unreliable on a collection actively being appended to (new messages shift what a fixed offset points at) · **Sort:** always chronological ascending (`created_at ASC`), since cursor pagination assumes one fixed scroll direction (oldest→newest, matching a chat transcript); no overridable `sort` param

**Response `200`**
```json
{
  "results": [
    {
      "id": "uuid",
      "sender": "string  // user | assistant",
      "content": "string",
      "stage": "string  // general | extraction_review | budget_review | categorisation_review | planning | analysis",
      "widget": {
        "type": "string | null  // allocation_slider | product_card | chart | null (no widget)",
        "payload": "object | null  // shape depends on type, see Architectural Guidelines §7"
      },
      "references": [
        { "target_type": "string  // transaction | budget | anomaly | recommendation | statement", "target_id": "uuid" }
      ],
      "created_at": "timestamp"
    }
  ],
  "next": "string (url) | null  // DRF CursorPagination's next-page link, encodes the cursor",
  "previous": "string (url) | null"
}
```

**Stale-session note:** reopening a conversation via this route does not implicitly refresh `last_message_at` — that only updates on `POST .../messages` — so the "old session, may be stale" warning (Design §5) can be shown based on `last_message_at` age at read time without a race against the read itself.

---

## POST /chat/conversations/{conversation_id}/messages

**Auth:** Required · **Scoping:** implicit self · **Query params:** none

**Request**
```json
{ "content": "string, required" }
```

**Response `200`** — Server-Sent Events stream (API Design Guidelines §9), not a single JSON body. Each event:
```json
{ "event": "token", "data": "string" }
```
followed by a terminal event once the Maestro's reply and any `message_references`/`widget` are finalized:
```json
{
  "event": "done",
  "data": {
    "id": "uuid",
    "content": "string",
    "widget": { "type": "string | null", "payload": "object | null" },
    "references": [{ "target_type": "string", "target_id": "uuid" }]
  }
}
```

---

## POST /chat/conversations/{conversation_id}/attachments

**Auth:** Required · **Scoping:** implicit self · **Query params:** none

Shortcut into the Statements pipeline (Design §5) — same underlying processing as `POST /statements`, tagged with the originating conversation.

**Request** — `multipart/form-data`
```
file: binary, required
```

**Response `202`**
```json
{
  "statement_id": "uuid",
  "status": "pending",
  "message_id": "uuid  // the system message created in this conversation referencing the upload"
}
```

---

## DELETE /chat/conversations/{conversation_id}

**Auth:** Required · **Scoping:** implicit self; `404` if not owned by caller · **Query params:** none

**Response `204`** — no body.

---

## Roles (domain-wide note)

End-user only. No admin variant exists for reading another user's conversation content — even Administration's Feedback review (Data Governance Specs §8) only sees a `reactions` row referencing a `message_id`, never the conversation transcript itself, preserving the same privacy boundary as the rest of the system's financial data.
