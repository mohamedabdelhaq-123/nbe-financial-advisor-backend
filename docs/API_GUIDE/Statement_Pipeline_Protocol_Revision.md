# Statement Pipeline Protocol Revision
**AI-Powered Personal Financial Advisor — Graduation Project (NBE)**

Supersedes `fix (1).md`. Records why the statement ingestion/approval flow changed and what it changed into — the concrete request/response shapes live in `Data_Shapes_Statements.md`, the schema in `DB_Schema.md`; this document is the narrative, not the source of truth for either.

This is the second draft of this document. The first draft (over-built: a separate file-retry endpoint, single-transaction approval alongside batch, a `partially_processed` state, `record_created`/`stored` as real statuses) was rejected in favor of the simpler design below, which is what actually shipped (`PLAN.md`, Checkpoints 1–6).

---

## 1. The two problems this solves

1. **The pipeline wasn't granular.** `POST /statements` ran OCR → normalize → ledger-insert synchronously in one shot with no way to resume from a partial failure.
2. **There was no approval gate.** Normalization wrote transactions straight into the ledger — the frontend had nothing to show the user for review before the data was already committed.

---

## 2. Final Design

### Status model
`statement_files.status`: `pending_extraction | pending_normalization | pending_approval | processed`. Status reflects **the last successfully completed phase** — not a phase in progress, not a dedicated `failed` value. `failure_reason` and `failed_phase` (`extraction | normalization | null`) carry retry context, cleared on every successful transition.

There is deliberately **no `record_created`/`stored` status**. A `StatementFile` row is only ever created after its file is successfully stored (`core/views/statements.py::create_statement_from_upload`) — a storage failure raises before any row is persisted, so there's nothing to represent and nothing to retry. This was the key simplification over the first draft, which had invented a `PUT /statements/{id}/file` retry endpoint for a case that, once the upload/creation step is atomic, never actually needs retrying via the API — the client just re-submits a fresh `POST`.

### Retry — PATCH only
`PATCH /statements/{id}` is the single retry mechanism, for the `extraction`/`normalization` phases only. It rejects backward or same-status targets and rejects `processed` as a target outright — approval is the only door to `processed`, closing off a bypass where a client could flip status without ever posting approved transactions. Requesting a target further out than the next phase cascades through the intermediate ones in one call (`core/views/statements.py::advance_statement_to`).

The first draft additionally put a retry-triggering mechanism on `POST /statements?status=xxx`. Dropped entirely: `POST` mints a new resource each call, so it structurally can't retry an existing row — it was redundant with `PATCH` at best and misleading at worst.

### Approval — atomic, whole-array, no partial state
`POST /statements/{id}/transactions` approves the **entire** proposed batch in one call. No per-transaction endpoint, no partial approval, no `partially_processed` status. Rows are matched to the proposed array by position (no per-row id exists in this design — deliberately, since single-transaction approval isn't supported, there's nothing else that would need one). The duplicate check is re-run at commit time rather than trusted from the normalize-time preview, since time may have passed between the two. A submission whose length doesn't match the proposed array is rejected outright rather than treated as a partial batch.

The first draft's single-transaction endpoint and `partially_processed` state are gone — approval is a single yes/no action on the whole reviewed batch, not a row-by-row workflow.

### Addendum — inlining the proposed transactions

Initially the proposed batch lived behind its own `GET /statements/{id}/normalized` route, separate from the statement's status. In practice that meant two calls to reach the same milestone: check status, then fetch the batch once it turned out to be `pending_approval`. Retired that route and inlined a `transactions` field directly onto `POST`/`GET`/`PATCH /statements/{id}` instead — populated only at `pending_approval`, `null` otherwise (and deliberately absent from the `GET /statements` list response, to avoid bloating a paginated payload with full transaction arrays no list screen needs). One response now carries both "where is this statement" and "what am I approving."

### Addendum — is_processing

The status model as shipped couldn't distinguish "this phase is actively running right now" from "this phase stopped and is sitting idle waiting for a retry" — both look identical (`status` unchanged, `failure_reason` null) from the outside. That's invisible today only because the pipeline is fully synchronous within one request; it becomes a real bug the moment any phase runs across a request boundary (a Celery worker, for instance) and a client polls mid-flight. Added `is_processing` (boolean) to close the gap now rather than retrofitting it later: set `true` at the start of each phase runner, `false` at the end regardless of outcome. It's always `false` in any response today, by construction, but the field is correct the moment that stops being true. It also does double duty as a concurrency guard — `PATCH /statements/{id}` now rejects a retry (`code: "already_processing"`) if a phase is already marked running on that statement, closing off the double-click-retry race.

### Addendum — file metadata alongside the proposed transactions

Inlining `transactions` (previous addendum) still left a gap: `bank_name`, `account_hint`, `model_used`, and `adjusted_at` — everything the old `GET /statements/{id}/normalized` route used to return besides the transaction array itself — never made it onto the new inline shape. Added as four more fields on `StatementDetailSerializer`, sourced from the same `statement_normalized` row `transactions` reads from. The key difference: these describe the normalization *event*, not the mutable pending batch, so they stay populated after `processed` — only `transactions` goes back to `null` once approved.

---

## 3. What changed elsewhere

- `DB_Schema.md` — `statement_files.status` enum, plus `failure_reason`/`failed_phase` columns.
- `Data_Shapes_Statements.md` — full rewrite of the status model, `POST`/`GET`/`PATCH /statements/{id}`, and the new `POST /statements/{id}/transactions`.
- `API_Endpoints_1.md` §4 — added `PATCH /statements/{id}` and `POST /statements/{id}/transactions` to the route list.
- `Pipeline.md` §2 — the ingestion diagram now shows the user approval gate sitting between normalization and ledger insert, with a note clarifying this doesn't contradict the pipeline's "no back-and-forth mid-flow" rule (that rule is about the agentic processing itself, not the ordinary REST review step after it finishes).
- `core/views/statements.py` — `_run_mock_pipeline` split into `_run_extraction`/`_run_normalization`/`advance_statement_to`; ledger writes moved into the new `StatementTransactionApprovalView`.
- `core/serializers/statements.py` — `failure_reason` stopped being a hardcoded-null placeholder; added `failed_phase`, `is_processing`, `StatementPatchSerializer`, and the transaction-approval request/response serializers.
