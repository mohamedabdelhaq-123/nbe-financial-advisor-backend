# System Architecture
**AI-Powered Personal Financial Advisor — Graduation Project (NBE)**

Clarifies the major components of the system at a high level: how the system is intended to work, the major constraints every team should work within (ownership, separation of concerns, redundancy avoidance), and the limitations the design deliberately accepts and how they are handled.

---

## 1. System Purpose

The platform turns a user's transaction history — from multiple banks, multiple accounts — into a single unified ledger, from which it derives budgeting, spending insights, and light product recommendations. Every account is one of two kinds:

- **Manual/user-managed accounts** — statement upload or direct manual entry, exactly as before. The user assumes liability for accuracy; every balance and category traces back to something they uploaded, typed, or confirmed.
- **Bank-integrated accounts** — explicitly linked by the user via a bank-like OAuth+OTP consent flow (see §5b), after which the account auto-syncs and is read-only to the end user. No internal bank systems or core-banking APIs are ever accessed without that explicit, per-account consent step — this isn't a background integration the user didn't opt into.

The system has two primary surfaces, and they are **not equals**:

- **Dashboard — the primary interface.** Metrics, plan, transactions, documents, goal progress. Every action available in chat is also available here, manually. Nothing depends on the chatbot existing.
- **AI Assistant (chat) — a secondary, optional interface.** A convenience layer for entering data conversationally and asking questions the dashboard can't pre-compute. It is one way to interact with the system, never the only way.

---

## 2. Major Components

| Component | Responsibility | Owner |
|---|---|---|
| **Frontend** (React) | All user-facing screens: splash/onboarding, dashboard, chat, documents & data, profile, admin views | Frontend |
| **Backend** (Django) | Owns the database, all business logic, all user-facing endpoints, auth, background job orchestration | Backend |
| **AI Service** (FastAPI) | Stateless* processing: OCR hand-off, normalization, chat (Maestro + sub-agents), embeddings, planning, recommendation matching | AI Specialist |
| **Postgres + pgvector** | Single database instance; Django and the AI service own **separate tables** in it | Shared (separate migrations) |
| **SeaweedFS** | Raw document storage, OCR artifacts, reference budget-limit templates | Backend / AI |
| **MinerU** | Deterministic OCR/layout extraction (not an agent — no reasoning) | AI Service, own container |
| **vLLM** | Model serving in staging/production (GPU) | AI Specialist |
| **Reverse proxy (Nginx)** | Single public entry point | DevOps |
| **Mock Bank OAuth+OTP service** (FastAPI) | Simulates a bank's identity/consent step for linking a bank-integrated account — login/OTP verification, standard OAuth2 authorization-code protocol on the outside. Owns no customer data itself (see §5b) | Backend, in-repo (`mock-bank-oauth/`) |
| **Mock Bank Sync service** (FastAPI) | Owns the mock bank's ledger (fake customers/accounts/transactions); the source of "a bank transaction happened" for the real-time sync demo | Backend, in-repo (`mock-bank-sync/`) |

\* The AI service keeps no in-memory state between requests, but it does own and write to its own database tables (embeddings, problem statements, recommendation logs) — "stateless" describes request handling, not data ownership.

---

## 3. Core Architectural Rule: Separation of Concerns

**The frontend never calls the AI service directly.** All frontend traffic goes through Django. Django is the only caller of the AI service, authenticated with a shared, DevOps-owned service-to-service token. This is a deliberate simplicity/security trade-off (see DevOps Planning Proposal §5.11, Pattern A): it costs one extra network hop internally, in exchange for the AI service never being reachable from the public internet and never needing to understand user auth at all.

**Django decides *when* to call the AI service; the AI service never initiates action on its own.** Background pipeline jobs (recurring-charge detection, anomaly detection, monthly summaries, insights) are triggered by Django/Celery on a schedule or event, and call the AI service's stateless endpoints — the AI service does not poll or watch the database itself.

**Ownership boundary inside Postgres:** Django manages its own tables via Django migrations; the AI service manages its own via Alembic. Neither writes to the other's tables. On a fresh deploy, Django migrates first, then the AI service. This keeps a single database instance (simpler ops, one backup) without blurring who owns what.

---

## 4. Redundancy-Avoidance Rules (binding on every team)

These are the recurring "don't build this twice" rules that keep the system consistent as it grows:

1. **One validation layer.** DRF serializers are the only schema-validation layer on the backend. No second one (pydantic/marshmallow) alongside it — this would create two sources of truth for the same payload shape.
2. **One plan per user.** Exactly one active budget record per user at any time (see §6). No parallel "draft" or "suggested" plan tables — a suggestion is presented, not persisted, until the user confirms it.
3. **Transactions are the single source of truth.** No other table is allowed to hold its own copy of a monetary event. Aggregations, insights, and monthly summaries are all *derived from* the transaction ledger, never an independent record of it. If a number can be computed from transactions, it is computed, not duplicated.
4. **One way to change data, one place data lands.** Whether a transaction, budget edit, or goal update comes from the dashboard, the chatbot, or a document upload, it goes through the same backend write path and lands in the same table. The chatbot is never allowed a private/alternate persistence path — see §7.
5. **No project generators.** Repos are built and understood by the team, not scaffolded by a black-box tool that quietly bundles unused dependencies (see DevOps Proposal §5.2).
6. **No caching layer until proven necessary.** The AI service is request-stateless with no cache; add one only if profiling shows a real bottleneck.

---

## 5. Data Flow — Ingestion (documents are a separate, narrow functionality)

Document upload and OCR exist for **one purpose**: getting transactions out of a statement and into the ledger. It is not a general-purpose document store or a data-analysis surface in its own right.

```
User uploads PDF/image (dashboard or chat shortcut — same endpoint either way)
   → Django stores raw file in SeaweedFS, creates a statement record (pending)
   → MinerU (own container) extracts content → OCR result stored, confidence recorded
   → Normalization Agent maps columns (known bank template, or LLM-inferred mapping saved for reuse)
   → duplicate check runs (see §8) before insert
   → transactions bulk-inserted into the ledger, embeddings generated
   → background jobs re-run: recurring charges, anomalies, monthly summary, insights
   → dashboard reflects the new data
```

Once transactions are in the ledger, the statement's job is done — Statements/OCR data is not queried again for analytics; everything downstream reads from the transaction ledger.

---

## 5b. Data Flow — Bank-Integrated Sync

A second, parallel ingestion path for accounts the user has explicitly linked, standing in for what a real bank integration would look like — the mock services exist so this can be built and demoed without any real bank or external OAuth provider.

**Linking a new account:**
```
User initiates a link (dashboard) → Django creates a BankConnection(status=pending_otp)
   → Django returns an authorize_url; frontend sends the browser there
   → Mock Bank OAuth+OTP service: user enters a bank-assigned customer id
     → resolves it against Mock Bank Sync's customer directory
     → emails a one-time code (via Django's own notification client — the
       mock never sends email itself)
     → user enters the code, OAuth+OTP service issues a short-lived
       authorization code, redirects back to the frontend
   → frontend hands the code to Django, which exchanges it for an access
     token, marks the connection linked, and pulls the account list + an
     initial transaction backfill from Mock Bank Sync
   → new BankAccount rows are created read-only (see below)
```

**Ongoing sync (real-time pipeline):** Mock Bank Sync's `/simulate/transaction` endpoint (the demo's "a bank transaction just happened" trigger) pushes a webhook to Django, which lands the transaction in the ledger, triggers the same post-ingestion analysis pass documents get, and pushes an SSE event to the frontend — the same "one way to change data, one place data lands" rule from §4 applies here too: the sync webhook and the initial backfill both land through the identical ingestion path, never a separate one each.

**Read-only enforcement:** once an account is bank-integrated, its metadata and its transactions are immutable via every existing manual-edit path (account edit/delete, manual transaction entry, transaction edit/delete) — enforced at the point each write would otherwise happen, not by hiding the controls client-side.

**Notifications:** this is also the one place the platform sends anything outside itself — an OTP delivery during linking, and a sync-event notification — via a real (not mocked) email gateway. See §11 for how this interacts with the offline-deployment constraint.

---

## 6. Budgets — One Plan, Two Editing Paths

There is exactly one active plan per user (a **`budgets`** table, one-to-one with the user — see Database Schema doc for the concrete shape). "Editing" always means replacing the live values, with the prior state snapshotted for history — there is no parallel-version ambiguity.

A plan can be reached and adjusted in **two equally valid ways**, both writing to the same table through the same backend logic:

- **Onboarding → Dashboard path:** user picks a starter template (income, goal, time period) during onboarding; the dashboard lets them edit allocations, goal, and time period directly at any time.
- **Chat (HITL) path:** the user asks the assistant to adjust the plan; the assistant opens an interactive **widget** (allocation sliders) rather than describing numbers in prose; the user confirms inside the widget; the confirmed values are written back exactly like a dashboard edit.

Neither path is "more authoritative" — the dashboard is not a read-only mirror of chat-driven changes, and chat is not a shortcut that bypasses dashboard validation. Both go through the same write endpoint.

---

## 7. The Chatbot's Role — Deliberately Limited

The assistant is **not the data engine**. Its job is bounded to three things:

- **Analysis** — answering questions against already-computed data (spending patterns, anomalies, trends).
- **Planning** — running the onboarding-style questionnaire when a user chooses to build/rebuild a plan conversationally, and proposing allocations grounded in reference budget-limit templates (never invented figures).
- **Recommendations** — surfacing relevant products from the local catalog based on a detected pattern or a direct question.

**The assistant's power to change data is intentionally narrow.** It cannot silently rewrite a transaction, override a budget, or invent a figure — every numeric claim it makes must trace back to a real entity via a message reference, and every write it triggers (a budget change, a manual transaction entered conversationally) goes through the identical validated backend path a dashboard edit would use, surfaced to the user as a confirmable widget rather than applied invisibly mid-conversation.

**Chat sessions are freely created, not a single continuous thread.** A user may start a new session at any time. Because each session's context is scoped to its own history, the user is warned when opening or continuing an old session that stale context may confuse the assistant's answers — old sessions are kept for reference, not treated as a live, continuously-updated memory.

**Widgets are the default output for anything structured**, not prose-with-numbers: allocation sliders for plan edits, cards for recommended products, inline charts for trend answers. Free text is reserved for explanation, not for numbers a user might act on.

---

## 8. Guardrails

- **Duplicate transaction prevention** — a composite check (user, account, date, amount, raw merchant text) runs before any bulk insert from a new statement, and applies equally to manual entry, so re-uploading an overlapping statement period or double-entering a manual transaction cannot create duplicate ledger rows. A secondary file-level checksum check rejects a byte-identical re-upload before OCR even runs.
- **Consent gate** — the ingestion pipeline checks for an active, unrevoked consent record before processing any document. Consent history is append-only, so a full grant/revoke timeline is always reconstructable.
- **PII scrubbing** — full account/card numbers are masked before any text reaches the LLM.
- **Topic lock** — the assistant is system-prompted to finance-related queries only.
- **Numeric traceability** — any figure the assistant states must be attributable to a real DB entity (transaction, summary, budget line) via a message reference; it cannot cite a number it computed ad hoc outside that trail.

---

## 9. Administration

A lightweight admin surface (Django admin, extended as needed) exists primarily to **review submitted feedback and reported issues** — not as a general operations console. Product-catalog management (the small, hand-authored Recommendation catalog) also lives here, since Django admin already gives that for free without a bespoke UI.

---

## 10. Deployment Shape (summary — full detail in DevOps documents)

- Each app (frontend, backend, AI service) is one repo, one Dockerfile, one built image; third-party services (Postgres, vLLM, SeaweedFS, MinerU, proxy) are pulled, not built.
- One container per independent lifecycle — never a database bundled into an app image, never two independently-scaling services sharing one image.
- Docker Compose (not Kubernetes) orchestrates a single on-prem, offline machine.
- The AI service is never publicly reachable; the reverse proxy is the only public entry point.
- Development substitutes an online OpenAI-compatible provider for vLLM (no local GPU requirement); staging and production run the real vLLM on GPU. Application code does not change between these — only configuration.

---

## 11. Known Limitations (accepted, and how they're handled)

| Limitation | Handling |
|---|---|
| No real-time bank connectivity for manual accounts | By design — manually-managed accounts stay user-supplied; recommendations are explicitly framed as soft suggestions, never verified eligibility. Bank-integrated accounts (§5b), linked via explicit OAuth+OTP consent, do receive a real-time sync feed — currently a mock standing in for an actual bank's own push feed |
| AI output is non-deterministic | Testing splits into blocking deterministic checks (boot/shape/retrieval) and non-blocking golden-dataset quality scoring |
| Production is fully offline | CI runs online; built images are exported, carried across the air-gap, and deployed via a human-gated step against an on-prem registry |
| Transactional notifications need internet | Deliberate, narrow exception to the offline-only rule: OTP delivery (§5b) and sync-event notifications go out via a real email gateway. Nothing else in the system makes an outbound call past the local deployment |
| Aggregated insights may be incomplete for a given user/period | The insight structure tolerates partial results — a missing insight does not break downstream consumers |
| Product recommendation catalog is small/hand-authored | Kept as flat, display-ready records rather than an over-normalized schema, since it is not the system's primary data domain |
