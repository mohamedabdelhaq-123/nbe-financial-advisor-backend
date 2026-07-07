# File System Structure
**AI-Powered Personal Financial Advisor — Graduation Project (NBE)**

Specifies the folder/object-key structure inside the file-based storage solution (SeaweedFS), accessed through its S3-compatible Filer gateway. Anything that is a *table row* elsewhere in the schema is not duplicated here — this document only covers the three categories of data the Data Governance Specs assign to file storage: raw/OCR statement artifacts, reference budget-limit templates, and (optionally) conversation message logs.

---

## 1. Access Pattern

The application never talks to SeaweedFS's native API directly. Django (via `django-storages` + `boto3`, pointed at SeaweedFS's Filer S3 gateway) and the AI service both read/write through the same S3-compatible interface, so the folder layout below is expressed as **object key prefixes** (bucket + path), not literal OS directories — though SeaweedFS's Filer does expose them as a browsable tree for debugging.

One bucket is used per logical concern, to keep lifecycle/retention rules (below) simple to apply per-bucket rather than per-object:

```
pfm-statements-raw          # raw uploaded documents (short-lived, user-controlled retention)
pfm-statements-artifacts    # OCR + normalization intermediate outputs
pfm-reference-data          # budget-limit templates, bank layout references (long-lived, versioned)
pfm-conversation-logs       # optional: file-based message logs (if this implementation path is taken)
```

---

## 2. `pfm-statements-raw` — raw uploaded documents

```
pfm-statements-raw/
  {user_id}/
    {statement_id}/
      original.{ext}          # ext = pdf | jpg | png, exactly as uploaded
```

**Rules:**
- One object per `statement_id` — the statement record in Postgres (`statement_files.seaweed_file_id`) points here.
- Deleted automatically once extraction succeeds and is confirmed, **unless** `user_preferences.retain_raw_documents = true` for that user (Data Governance Specs §1, §2).
- Never browsed or listed as a "documents" feature in its own right — retrieval is always by the specific `statement_id` referenced from a DB row, never a folder listing shown to the user.

---

## 3. `pfm-statements-artifacts` — OCR + normalization outputs

```
pfm-statements-artifacts/
  {user_id}/
    {statement_id}/
      ocr/
        content.json           # MinerU's structured text/table/metadata extraction
        document.md             # Markdown representation
        images/
          {page_n}_{img_n}.png    # extracted embedded images, if any
        tables/
          {page_n}_{table_n}.json # extracted table structures
      normalized/
        normalized.json          # LLM-adjusted, pre-DB-insert structured output
```

**Rules:**
- `statement_ocr_results.seaweed_file_id` points at the `ocr/` folder for that statement; `statement_normalized.normalized_json` in Postgres is the queryable copy, this file is the traceable raw artifact matching the "three distinct, traceable stages" rule in Data Governance Specs §2.
- Retained per the same policy as raw uploads (tied to `retain_raw_documents`), since these are only useful for tracing an extraction error back to its source stage — once a statement's transactions are confirmed in the ledger, these artifacts are not read again in normal operation.
- Never holds transaction-level records itself — extraction output here is turned into rows in `transactions` (Aggregations); this bucket is not a secondary source of truth.

---

## 4. `pfm-reference-data` — long-lived, versioned reference files

Deliberately separate from user data: **not tied to any user_id**, updatable by the team without a redeploy, and read by the Planner Agent / onboarding template step.

```
pfm-reference-data/
  budget-templates/
    {template_id}.json          # e.g. EGP_18000_single.json — category_limits by income bracket/household type
  onboarding-templates/
    {template_key}.json         # e.g. balanced.json — the 3-5 starter templates shown at onboarding step 4
```

**Rules:**
- `budget-templates/*.json` matches the structure defined in the AI/PFM Architecture doc §7 (`category_limits`, `source`, `last_updated`) — these ground the Planner Agent's suggestions and prevent hallucinated allocations.
- `onboarding-templates/*.json` backs `GET /budget-templates/suggestions` — kept separate from the Planner's post-onboarding category-limit templates because they serve different steps (initial template pick vs. ongoing plan generation grounding).
- Versioned by convention (`last_updated` field inside each file, plus object versioning if enabled at the bucket level) rather than by a database table, since these are edited directly by whoever owns them (see System Architecture / Open Questions — named owner still to be confirmed) without going through the app's write paths.
- Read-only from the application's perspective at request time; writes happen out-of-band (a team member uploads a new/updated file).

---

## 5. `pfm-conversation-logs` — optional file-based message storage

Data Governance Specs §3 leaves the storage shape of conversation message *content* as an implementation choice (file-based logs vs. individual DB rows), depending on volume/retention needs. If the team takes the file-based path rather than storing full message content directly in the `messages` table:

```
pfm-conversation-logs/
  {user_id}/
    {conversation_id}/
      messages.jsonl             # append-only, one JSON object per line: {message_id, sender, content, stage, created_at}
```

**Rules:**
- If this path is used, `messages` in Postgres still holds the row per message (sender, stage, timestamps, id) for indexing/joins — only the raw `content` body would be offloaded here, referenced by `message_id`.
- Purely additive/optional: nothing elsewhere in the system depends on this bucket existing. Default assumption, unless volume becomes a real concern, is that message content stays directly in Postgres and this bucket is unused.

---

## 6. Naming & Retention Summary

| Bucket | Keyed by | Retention |
|---|---|---|
| `pfm-statements-raw` | `user_id` / `statement_id` | Deleted post-extraction unless user opts to retain |
| `pfm-statements-artifacts` | `user_id` / `statement_id` | Same policy as raw; kept for traceability while raw is kept |
| `pfm-reference-data` | template key/id (no user scoping) | Long-lived, manually versioned, never auto-deleted |
| `pfm-conversation-logs` | `user_id` / `conversation_id` | Optional; if used, retained for the life of the conversation record |

**Object key rule:** every user-scoped key starts with `{user_id}/`, so a user-deletion request (Functional Requirements #23 — delete stored data at any time) can be fulfilled by removing that single prefix across the relevant buckets, in addition to the corresponding DB rows.

---

## 7. Container & Volume Notes (see DevOps documents for full detail)

- SeaweedFS runs in its own container (DevOps Planning Proposal §5.1), reached only by Django and the AI service over the internal network — never exposed publicly.
- A named volume backs SeaweedFS's data directory so contents survive container restarts (§5.12 — the "#1 beginner trap" to avoid).
- Backups: SeaweedFS snapshots are taken alongside the scheduled Postgres `pg_dump`, and a restore is rehearsed on staging (§5.9) — file storage is not treated as less critical than the database just because it holds "files" rather than "rows."
