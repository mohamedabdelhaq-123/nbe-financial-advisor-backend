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
`statement_files.status`: `extraction | normalization | approval | processed`. Status reflects **the last successfully completed phase** — not a phase in progress, not a dedicated `failed` value. `failure_reason` and `failed_phase` (`extraction | normalization | null`) carry retry context, cleared on every successful transition.

There is deliberately **no `record_created`/`stored` status**. A `StatementFile` row is only ever created after its file is successfully stored (`core/views/statements.py::create_statement_from_upload`) — a storage failure raises before any row is persisted, so there's nothing to represent and nothing to retry. This was the key simplification over the first draft, which had invented a `PUT /statements/{id}/file` retry endpoint for a case that, once the upload/creation step is atomic, never actually needs retrying via the API — the client just re-submits a fresh `POST`.

### Retry — PATCH only
`PATCH /statements/{id}` is the single retry mechanism, for the `extraction`/`normalization` phases only. It rejects backward or same-status targets and rejects `processed` as a target outright — approval is the only door to `processed`, closing off a bypass where a client could flip status without ever posting approved transactions. Requesting a target further out than the next phase cascades through the intermediate ones in one call (`core/views/statements.py::advance_statement_to`).

The first draft additionally put a retry-triggering mechanism on `POST /statements?status=xxx`. Dropped entirely: `POST` mints a new resource each call, so it structurally can't retry an existing row — it was redundant with `PATCH` at best and misleading at worst.

### Approval — atomic, whole-array, no partial state
`POST /statements/{id}/transactions` approves the **entire** proposed batch in one call. No per-transaction endpoint, no partial approval, no `partially_processed` status. Rows are matched to the proposed array by position (no per-row id exists in this design — deliberately, since single-transaction approval isn't supported, there's nothing else that would need one). The duplicate check is re-run at commit time rather than trusted from the normalize-time preview, since time may have passed between the two. A submission whose length doesn't match the proposed array is rejected outright rather than treated as a partial batch.

The first draft's single-transaction endpoint and `partially_processed` state are gone — approval is a single yes/no action on the whole reviewed batch, not a row-by-row workflow.

### Addendum — inlining the proposed transactions

Initially the proposed batch lived behind its own `GET /statements/{id}/normalized` route, separate from the statement's status. In practice that meant two calls to reach the same milestone: check status, then fetch the batch once it turned out to be `approval`. Retired that route and inlined a `transactions` field directly onto `POST`/`GET`/`PATCH /statements/{id}` instead — populated only at `approval`, `null` otherwise (and deliberately absent from the `GET /statements` list response, to avoid bloating a paginated payload with full transaction arrays no list screen needs). One response now carries both "where is this statement" and "what am I approving."

### Addendum — is_processing

The status model as shipped couldn't distinguish "this phase is actively running right now" from "this phase stopped and is sitting idle waiting for a retry" — both look identical (`status` unchanged, `failure_reason` null) from the outside. That's invisible today only because the pipeline is fully synchronous within one request; it becomes a real bug the moment any phase runs across a request boundary (a Celery worker, for instance) and a client polls mid-flight. Added `is_processing` (boolean) to close the gap now rather than retrofitting it later: set `true` at the start of each phase runner, `false` at the end regardless of outcome. It's always `false` in any response today, by construction, but the field is correct the moment that stops being true. It also does double duty as a concurrency guard — `PATCH /statements/{id}` now rejects a retry (`code: "already_processing"`) if a phase is already marked running on that statement, closing off the double-click-retry race.

### Addendum — file metadata alongside the proposed transactions

Inlining `transactions` (previous addendum) still left a gap: `bank_name`, `account_hint`, `model_used`, and `adjusted_at` — everything the old `GET /statements/{id}/normalized` route used to return besides the transaction array itself — never made it onto the new inline shape. Added as four more fields on `StatementDetailSerializer`, sourced from the same `statement_normalized` row `transactions` reads from. These describe the normalization *event*, not the mutable pending batch, so they stay populated after `processed` regardless of what `transactions` does.

### Addendum — transactions switches source once processed

Originally `transactions` just went back to `null` once a statement reached `processed`, on the reasoning that Statements' job is finished at that point (Data Governance Specs §2) and the ledger is the real source of truth. In practice that left no way to see "what did this statement actually produce" through this endpoint at all — `GET /transactions` has no `statement_id` filter, so there was no path back to it. `get_transactions()` now branches on status instead of just gating on it: `approval` still returns the proposed preview from `normalized_json`; `processed` now returns the real rows from `obj.transactions` (the `Transaction.statement` reverse relation), serialized with the existing `TransactionListSerializer` from the Aggregations domain rather than inventing a second shape. This isn't a second copy of ledger data — it's a live read through the same FK, so the single-source-of-truth rule still holds; only the *shape returned* switches, not where the data is kept.

### Addendum — dropping the `pending_` prefix

Once `is_processing` existed, `pending_extraction`/`pending_normalization`/`pending_approval` were saying the same thing twice: `is_processing=false` already means "not actively running," so a status *name* built around "pending" was redundant with a field that already carries that meaning precisely. Renamed the four values to `extraction | normalization | approval | processed` — the status names the phase the statement is at/working toward; `is_processing` says whether that phase is live right now. A migration (`0005_alter_statementfile_status`) renames the choices/default and includes a data migration for any existing rows, since this changes stored string values, not just a Python-side constant.

### Addendum — file metadata onto the list, split from transactions

The inline fields ended up split across the two response tiers by weight. The **file metadata** — `file_size`/`file_type` (new columns, captured from the raw file at upload) plus the normalization facts `bank_name`/`account_hint`/`model_used`/`adjusted_at` — moved up onto the base `StatementFileSerializer`, so it rides on `GET /statements` (the list) too, not just the detail shape: a documents screen wants "which bank / what file / when parsed" per row without a follow-up call. The heavy **`transactions`** array stays on `StatementDetailSerializer` only — a paginated list has no reason to drag a full transaction array per row, and the detail route is "for the transactions." Since the metadata getters now run per list row, `latest_normalized_record` became a prefetch-friendly `cached_property` and the list queryset gained `select_related("account").prefetch_related("normalized_records")`, keeping the list at a constant handful of queries rather than N+1.

### Addendum — `GET /budget/starter-templates` made public

Onboarding renders the starter templates before the user has a token, so the route moved to `AllowAny`. The templates are reference data, not user-scoped, so nothing leaks; the only user-dependent bit — the `is_suggested` flag — falls back to flagging `balanced` when there's no authenticated user (or no qualifying income/dependents signals), and still tailors to `aggressive_savings` for a signed-in steady-income, no-dependents user.

### Addendum — one guarded entry point for both POST and PATCH

`create_statement_from_upload()` (POST's initial chain) and `StatementDetailView.patch()` (retry) had each grown their own copy of "is this transition allowed" — POST just called `_run_extraction`/`_run_normalization` inline and checked the resulting status by hand; PATCH had the already-processed/already-processing/forward-only checks written out before calling `advance_statement_to()`. Moved all three guards *into* `advance_statement_to()` itself, so it's not just the cascade loop that's shared — it's the whole "is this move legal" decision. POST now calls it too, instead of duplicating the two-line cascade a second time.

That unification made an actual feature easy to add cheaply: `POST /statements` now takes an optional `status` field (`normalization` | `approval`, same choices `PATCH` accepts, default `approval` — the original always-chain-to-the-end behavior), so a client can stop the initial upload right after extraction instead of always running normalization too. Both serializers' `status` `ChoiceField`s now read from one shared `_ADVANCE_TARGET_CHOICES` constant rather than two separately-typed-out lists.

### Addendum — PATCH was missing `@extend_schema` (same bug as POST, found later)

`StatementDetailView.patch()` had the identical gap the first Swagger-UI fix addressed on `POST /statements`: no `@extend_schema`, so drf-spectacular fell back to `StatementDetailSerializer` (fully read-only) as PATCH's request body too — `status` never appeared as an editable field in Swagger UI. Same fix: `@extend_schema(request=StatementPatchSerializer, responses={200: StatementDetailSerializer})`. Worth noting for next time: any hand-written `def post`/`patch`/`put` on a generic view needs this checked explicitly — drf-spectacular does not warn when it silently substitutes the wrong serializer, it just produces a technically-valid but useless schema.

### Addendum — status values renamed to "already completed" naming

The `extraction | normalization | approval | processed` set (from the `pending_` removal addendum above) still had a naming problem: `approval` named a *pending* state (awaiting the user's decision) while everything else in this document had settled on "what's already done" as the convention — and `approval` specifically collided with the `approve` action on `POST .../transactions`, making "status is approval" and "the user approved it" read as if they might mean the same thing when they didn't. Renamed to `uploaded | extracted | normalized | approved` — **not a positional swap**:

```
extraction    -> uploaded     (upload step done, extraction not yet run)
normalization -> extracted    (extraction done, normalization not yet run)
approval      -> normalized   (normalization done, awaiting the user's approval decision)
processed     -> approved     (terminal — transactions approved AND committed)
```

`approved` now reads unambiguously as "the approve action has happened," and it's the only place in the status vocabulary that shares a root with an action name — deliberately, since it's the one status an action (`POST .../transactions`) actually produces. `failed_phase`'s `extraction`/`normalization` values are unchanged on purpose — they name the *activity* that failed (the OCR run, the LLM run), a different axis from `status`'s "phase already completed," so they were never part of the ambiguity this rename fixes. Same migration pattern as before: `0007_alter_statementfile_status` renames both the column's choices/default and any existing rows via `RunPython`.

---

## 3. What changed elsewhere

- `DB_Schema.md` — `statement_files.status` enum, plus `failure_reason`/`failed_phase` columns.
- `Data_Shapes_Statements.md` — full rewrite of the status model, `POST`/`GET`/`PATCH /statements/{id}`, and the new `POST /statements/{id}/transactions`.
- `API_Endpoints_1.md` §4 — added `PATCH /statements/{id}` and `POST /statements/{id}/transactions` to the route list.
- `Pipeline.md` §2 — the ingestion diagram now shows the user approval gate sitting between normalization and ledger insert, with a note clarifying this doesn't contradict the pipeline's "no back-and-forth mid-flow" rule (that rule is about the agentic processing itself, not the ordinary REST review step after it finishes).
- `core/views/statements.py` — `_run_mock_pipeline` split into `_run_extraction`/`_run_normalization`/`advance_statement_to`; ledger writes moved into the new `StatementTransactionApprovalView`.
- `core/serializers/statements.py` — `failure_reason` stopped being a hardcoded-null placeholder; added `failed_phase`, `is_processing`, `StatementPatchSerializer`, and the transaction-approval request/response serializers.
