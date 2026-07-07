# Data Governance Specs
**AI-Powered Personal Financial Advisor — Graduation Project (NBE)**

Specifies the different domains of data in the project, how each domain's entities are stored (relational vs. file vs. vector), the constraints enforced within each domain, and the connections between domains. This document governs *what belongs where and why* — the concrete field-by-field schema lives in the Database Schema document; this one is the rulebook that schema must obey.

---

## Overview

The platform operates strictly on data the user supplies directly — uploaded statements, chat input, manual dashboard entry. No internal bank systems, account-verification services, or real-time bank data feeds are accessed at any point. Every balance, category, and recommendation is derived exclusively from user-supplied documents and their extracted contents, or from values the user typed in themselves.

Data is organised into eight domains: **Profile, Statements, Conversations, Budgets, Feedback, Recommendation, Aggregations,** and **Administration.**

---

## 1. Profile

### Role
Single source of truth for user identity, linked bank accounts, and behavioural/display preferences. Every other domain scopes its data back to a user (and, where relevant, a specific bank account) defined here.

### Rules & Guidelines
- Holds only user-declared or safely-derived data — no bank-verified identity or balance data.
- A user may link zero or more bank accounts, across different, unrelated banks.
- Push/email/SMS notifications and real-time alerts are out of scope — the system has no internet-dependent delivery mechanism in its offline deployment.
- Consent records are append-only; consent history is never overwritten, so a full grant/revoke timeline is always reconstructable.
- Income, employment status, and household signals collected here (or refined via onboarding/chat) are the inputs the Budgets domain uses to select a starting template — Profile does not itself decide budget allocations.

### Contained Information
- User identity attributes (name, contact info, employment status, income bracket, monthly income, onboarding date, status)
- Linked bank accounts (bank name, account type, masked account number, currency, active status)
- Consent history (consent type, granted/revoked timestamps, policy version)
- User preferences (language, currency display format, date format, budget cycle start day, default view, raw-document retention choice)

### Connections
Referenced by every other domain via user identifier, and by Statements/Aggregations via the specific bank account identifier.

---

## 2. Statements

### Role
A **narrow, single-purpose** domain: custody of everything derived from a user-submitted document, from raw upload through OCR extraction to LLM-cleaned structured output, for exactly one reason — getting transactions out of the document and into the ledger (Aggregations). Once transactions are confirmed and written there, Statements' job is finished; it is not queried again for analytics.

### Rules & Guidelines
- Statements produces a clean, structured version of a submitted document — it does not compute insights, categorise transactions for analytics purposes, or generate budgets. Categorisation happens once, on the way into the transaction ledger.
- Three processing stages are kept as distinct, traceable artifacts: raw upload, OCR output, and LLM-adjusted/reformatted output — so any extraction error can be traced back to the stage that introduced it.
- Known bank layouts are stored as reference templates to speed up and validate parsing; a statement can still be processed without a template match by falling back to the full OCR + LLM pipeline, which then produces a new template for future reuse.
- Raw files are deleted after successful extraction unless the user has opted to retain them, per their Profile preference.
- Document upload and OCR are **not** a general document-management feature — there is no browsing/searching of statements as documents in their own right beyond what's needed to review an extraction.

### Contained Information
- Raw uploaded document (file)
- OCR-stage output (structured text/markdown/JSON, with parser confidence data)
- LLM-adjusted, normalised statement output
- Bank statement layout templates (column mapping, date format, layout signature) for recognised banks

### Connections
Belongs to a user and bank account (Profile). Feeds normalised output into Aggregations, subject to the duplicate check defined there. Referenced by Conversations when a user confirms or corrects extraction results.

---

## 3. Conversations (LLM Sessions)

### Role
The complete interaction log between the user and the assistant. LLM sessions are **one convenience path among several** for entering data or asking questions — never the only way to interact with the system. Every screen and action reachable via chat is also reachable directly on the dashboard.

### Rules & Guidelines
- A single conversation/message structure is used throughout; what differs between an advisory chat message and a review/confirmation message is a **stage** tag on the message itself (e.g. general, extraction review, budget review, categorisation review), not a separate parent entity.
- Sessions are **freely created** at any time — a user is not confined to one long-running thread. Because each session is scoped to its own history, the user is warned when opening or continuing an old session that stale context may confuse the assistant; old sessions are kept for reference, not treated as a continuously updated memory.
- If a message relates to another piece of data in the system (a statement, a transaction, a budget, a reported issue), that relationship is captured through a reference field on the message, backed by a dedicated references table — Conversations does not hold direct foreign keys into every other domain.
- Conversations does not mutate other domains' data directly. Any resulting action from a conversation (correcting a transaction's category, adjusting a budget allocation) is written back to the owning domain through the same validated path a dashboard edit would use — not stored or applied here.
- **The assistant's power to change data is deliberately limited.** It can propose changes (e.g. via an allocation-slider widget), but the write only happens once the user confirms inside that widget — never as a silent side effect of a text reply.
- Structured content (allocation sliders, product cards, charts) is rendered as an **interactive widget**, not described in prose with numbers — numbers a user might act on belong in a widget they can directly confirm or adjust, not buried in free text.
- The assistant is scoped to three functions only: analysis (answering questions against already-computed data), planning (running the plan questionnaire, grounded in reference budget-limit templates), and recommendations. It is not a data-entry engine in its own right — data entered via chat still lands through ordinary transaction/budget write paths.

### Contained Information
- Conversation thread metadata (participants, start/last-activity timestamps, status)
- Messages, each tagged with sender, content, timestamp, and processing stage
- References — a lightweight table linking a message to an entity elsewhere in the system (target type and target identifier)

### Connections
May reference Statements, Aggregations, Budgets, or Feedback through the references table; ownership of referenced data remains with those domains.

---

## 4. Budgets

### Role
Holds the user's **single, current** target spending plan — the yardstick against which actual data in Aggregations is compared for categorisation and insight generation. Named `budgets` in the schema: **one row per user (1:1 relationship)**, always representing the most recent plan.

### Rules & Guidelines
- **One plan per user, no parallel versions.** There is exactly one live plan at any time; there is no separate "draft" or "suggested" plan row — a template suggestion shown during onboarding or chat is not persisted until the user confirms it.
- Editing **replaces** the live row rather than creating a new active one, but the prior state is versioned/snapshotted first — so "planned vs. actual" history remains reconstructable without ambiguity over which row is current.
- **Two equally valid edit paths, one write path.** A plan may be customised (a) directly on the dashboard, or (b) via the chat HITL modal (allocation-slider widget) after the assistant proposes an adjustment. Both go through the identical backend validation and land in the same `budgets` row — neither path is more authoritative than the other, and neither bypasses the other's rules (e.g. allocations must still sum to 100%).
- The starting point at onboarding is one of a small set of reference templates (income/goal/time-period driven); after that, the plan is the user's own, editable data, not a copy of the template.
- Reference budget-limit templates (category maximums by income bracket/household type, used to keep the Planner grounded and non-hallucinatory) are a **separate, file-based reference dataset** — not part of a user's own `budgets` row.

### Contained Information
- Plan metadata (name, period type, status, creation date, savings goal name, goal target amount, goal timeline in months)
- Per-category budget allocations (category, allocated percentage, allocated amount, currency)
- Version history of prior plan states

### Connections
Compared against actual spending in Aggregations. Referenced by Conversations when a user discusses or edits their budget. Reads reference budget-limit templates (file-based, see Data Shapes) when a plan is first generated or re-generated.

---

## 5. Feedback

### Role
Captures two independent kinds of user input: reactions to system-generated output, and standalone reported problems. These are two distinct concerns, not stages of one pipeline. This domain is also the primary data source for the Administration domain (see §8).

### Rules & Guidelines
- There is no persisted "final report" entity — reporting-style output (e.g. a monthly insight summary) is generated on demand from Aggregations rather than stored as its own artifact in this domain.
- Reactions (likes, ratings, comments) and reported issues are kept as separate, independent structures with no automatic escalation between them. A comment does not become a reported issue; a user who wants to flag a problem creates a reported issue directly.

### Contained Information
- Reactions: a rating/like/comment tied to a target entity elsewhere in the system (target type and target identifier)
- Reported issues: a user-submitted description of a problem, with a status field for tracking resolution

### Connections
Reactions may target a transaction, a recommendation, a conversation message, or a budget, via target type/identifier — Feedback does not own the referenced data. Read (not owned) by Administration for review purposes.

---

## 6. Recommendation

### Role
Matches a user's query or profile signal to a relevant bank product (e.g. certificates, loans) from a locally-held, hand-authored product catalog, using local (offline) embeddings and similarity search — entirely independent of any internet-connected service or internal bank system.

### Rules & Guidelines
- Because no internal bank data is available, any match is a soft suggestion only, never a verified pre-approval or eligibility guarantee — this constraint should be reflected in how results are presented to the user.
- The product catalog is currently small and hand-authored (mock data); product information is kept as a single, flat, display-ready structure rather than a heavily normalised multi-table schema.
- Fallback behaviour for low-confidence matches (e.g. below a similarity threshold) is implemented in application/business logic, not as a persisted decision-tree table.
- Since products must also be displayed to users directly (not only matched via search), a minimal separation of display fields is kept: title, description, categories, tags, features, and an external link where applicable.

### Contained Information
- Product catalog entries: title, description, categories, tags, features, external link, active status
- Searchable problem/query text per product, with its corresponding embedding vector, for RAG-based matching
- A log of which product was shown to which user, for which query, with what match confidence

### Connections
Reads contextual signals from Profile and Budgets when matching is profile-driven rather than query-driven. Recommendation log entries may be targeted by Feedback (a user liking or dismissing a shown recommendation). Catalog entries are also managed via Administration.

---

## 7. Aggregations

### Role
The single source of truth for the normalised **transaction ledger** and everything computed from it. This is the domain every insight, budget comparison, and recommendation signal ultimately traces back to — and the only domain permitted to hold transaction-level records at all.

### Rules & Guidelines
- **Transactions are the single source of truth for every monetary fact in the system.** No other domain is allowed to keep its own copy of a transaction or derive a figure independently of this ledger; monthly summaries, recurring-charge detection, anomaly flags, and pattern insights are all *computed from* the ledger, never stored as an independent parallel record of it.
- A transaction may originate from a processed statement, from direct manual entry on the dashboard, or from manual entry via chat — all three land in the same table through the same write path and are subject to the same rules.
- **Duplicate prevention is enforced at the transaction level**, regardless of origin: a composite check (user, account, transaction date, amount, raw merchant text) runs before any insert — whether from a bulk statement import or a single manual entry — so re-uploading an overlapping statement period, or re-entering the same manual transaction, cannot create duplicate ledger rows.
- Aggregated insights are best-effort, not guaranteed-complete: a given computed insight (e.g. income stability) may not be derivable for every user or period due to insufficient or inconsistent statement data. The structure must tolerate partial or missing aggregation results without breaking downstream consumers.
- Designed for easy extension: new categories of computed insight should be addable without requiring a new dedicated table each time, favouring a generic, typed insight structure where practical.

### Contained Information
- Transaction ledger (date, merchant raw/normalised name, category, amount, currency, recurrence flag, extraction confidence, source — statement/manual)
- Monthly summaries (total spend, total inflow, category breakdown, top merchants)
- Recurring charge detection (merchant, frequency, average amount, last/next expected occurrence)
- Anomaly flags tied to specific transactions (reason, severity, resolution status)
- Generic spending-pattern insights (extensible type/value structure), covering patterns such as: cash flow (inflow vs. outflow), merchant frequency ranking, day-of-week/time-of-month spending patterns, category volatility, income stability, and debt-service ratio proxy
- Net worth snapshots aggregated across all of a user's linked bank accounts
- Goal progress (computed monthly against the active `budgets` goal fields — feeds the dashboard progress bar)

### Connections
Sourced from the normalised output of Statements and from direct manual entry. Compared against Budgets. Read by Recommendation for profile-based matching signals. Referenced by Conversations and Feedback when discussing specific transactions or insights.

---

## 8. Administration

### Role
A narrow, internal-facing domain: reviewing what users have reported. It is **not** a general operations console and does not hold its own primary data — it reads from Feedback (reactions and reported issues) and, secondarily, manages the Recommendation product catalog, since both are small enough not to warrant a bespoke admin surface of their own.

### Rules & Guidelines
- Administration owns no transactional user data — it is a read/manage surface over Feedback and the Recommendation catalog only.
- Access is restricted to internal staff roles, never exposed on the same auth surface as end-user accounts.

### Contained Information
- No domain-specific tables of its own beyond role/permission scoping for internal staff.

### Connections
Reads Feedback (reactions, reported issues) for review. Reads/writes Recommendation's product catalog entries.

---

## Data Shapes

Data across the platform takes one of three underlying shapes. Each domain above is composed of one or more of these.

### Table (relational, structured records in PostgreSQL)
Used for all structured, queryable data with defined fields and relationships. Applies to:
- Profile (user identity, accounts, consent, preferences)
- Statements' metadata (per-stage processing records, templates)
- Conversations (thread and message metadata, references)
- Budgets (plan metadata, allocations, version history — one row per user)
- Feedback (reactions, reported issues)
- Recommendation (product catalog fields, recommendation log)
- Aggregations (transactions, summaries, recurring charges, anomaly flags, pattern insights, net worth snapshots)

### File (unstructured binary/document objects in SeaweedFS)
Used for raw or large content not suited to relational storage. Applies to:
- Statements' raw uploaded documents (PDF/image/scan)
- Statements' intermediate OCR output, where stored as a raw structured document rather than a table row
- Reference budget-limit templates (category maximums by income bracket/household type) used by the Planner — deliberately file-based so they can be updated without redeployment as economic conditions change
- Conversation message content, where stored as file-based logs rather than individual rows (implementation choice per volume/retention needs)

### Vector (embeddings stored via pgvector, co-located in PostgreSQL)
Used wherever semantic similarity search is required. Applies to:
- Aggregations RAG: transaction-level and monthly-summary chunks embedded for retrieval during advisory queries
- Recommendation RAG: product/problem-statement text embedded for local, offline similarity matching against user queries


