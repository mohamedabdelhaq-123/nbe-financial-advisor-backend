# Data Shapes & Query Params — Feedback
**AI-Powered Personal Financial Advisor — Graduation Project (NBE)**

Per-endpoint spec for the Feedback domain: general reactions and reported issues (Data Governance Specs §5). Recommendation-specific feedback uses its own endpoint (`POST /recommendations/{id}/feedback` — see Data Shapes & Query Params — Recommendation) since it's tied to a specific shown instance; this document covers the general-purpose routes. Unless stated otherwise, every route requires a valid user JWT and is implicitly scoped to the authenticated caller.

---

## POST /feedback

**Auth:** Required · **Scoping:** implicit self, plus ownership check on `target_id` · **Query params:** none

Creates a `reactions` row against any referenceable entity in the system.

**Request**
```json
{
  "target_type": "string, required  // transaction | message | budget",
  "target_id": "uuid, required",
  "rating": "integer, optional, 1-5",
  "comment": "string, optional"
}
```
At least one of `rating` or `comment` is required. `target_id` must belong to the requesting user (or, for `message`, belong to a conversation owned by the requesting user) — `404` otherwise, not `403`, per API Design Guidelines §10's existence-leak avoidance rule.

**Response `201`**
```json
{
  "id": "uuid",
  "target_type": "string",
  "target_id": "uuid",
  "rating": "integer | null",
  "comment": "string | null",
  "created_at": "timestamp"
}
```

---

## POST /issues

**Auth:** Required · **Scoping:** implicit self · **Query params:** none

Creates a `reported_issues` row — a standalone problem report, independent of the reactions above (Data Governance Specs §5: no automatic escalation between the two).

**Request**
```json
{ "description": "string, required, min 10 chars" }
```

**Response `201`**
```json
{
  "id": "uuid",
  "description": "string",
  "status": "open",
  "created_at": "timestamp",
  "resolved_at": null
}
```

---

## GET /issues

**Auth:** Required · **Scoping:** implicit self · **Query params:** `status` (optional filter: open/in_review/resolved/dismissed), `limit` (integer, optional, page size — frontend-controlled), `offset` (integer, optional, default 0) · **Pagination:** Offset — DRF `LimitOffsetPagination` — bounded, filterable · **Default sort:** `created_at DESC` (most recent first)

Lists the requesting user's own reported issues (to let them track resolution status — the user-facing counterpart to the admin-facing `GET /admin/issues`).

**Response `200`**
```json
{
  "count": "integer",
  "next": "string (url) | null",
  "previous": "string (url) | null",
  "results": [
    {
      "id": "uuid",
      "description": "string",
      "status": "string  // open | in_review | resolved | dismissed",
      "created_at": "timestamp",
      "resolved_at": "timestamp | null"
    }
  ]
}
```

---

## Roles (domain-wide note)

End-user only for the three routes above. The cross-user counterparts — `GET /admin/feedback` (all reactions) and `GET/PATCH /admin/issues` (all reported issues, with status updates) — live under Administration's separate credential space (see Data Shapes & Query Params — Administration); a user can only ever see and manage their own feedback/issues through these routes, never anyone else's.
