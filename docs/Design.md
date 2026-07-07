# Design
**AI-Powered Personal Financial Advisor — Graduation Project (NBE)**

Specifies the distinct mental spaces the user moves through, the pages that make up each, what each page is responsible for, and which APIs it depends on. This is the bridge between the wireframe spec (screen-level detail) and the API Endpoints document (route-level detail) — it explains *why* the screens are grouped the way they are and what job each one is doing for the user.

---

## Mental Spaces Overview

The product has one internal-only space (Administration) and six user-facing spaces, each answering a different question the user has in mind:

| Space | User's question | Pages |
|---|---|---|
| **Entry & Trust** | "Can I trust this with my financial data?" | Splash, Consent |
| **Setup** | "Tell it about me, once." | Onboarding (5 steps) |
| **Command** | "How am I doing right now?" | Dashboard |
| **Plan** | "What's my target, and can I change it?" | Savings Plan, HITL modal |
| **Conversation** | "Let me just ask it something." | Chatbot |
| **Records** | "Show me the actual data." | Documents & Data (Data / Transactions / Documents tabs) |
| **Identity & Control** | "My account, my rules." | Profile & Settings |
| *(internal)* **Review** | "What are users reporting?" | Admin — Feedback & Issues |

The flow between the user-facing spaces: `Splash → Consent → Onboarding → Dashboard`, then Dashboard is the hub every other space is reached from and returned to (`Dashboard ↔ Plan`, `Dashboard ↔ Conversation`, `Dashboard ↔ Records`, `Dashboard ↔ Identity & Control`). Signing in from Splash skips straight to Dashboard.

---

## 1. Entry & Trust Space

**Pages:** Splash, Consent & Privacy.

**Responsibility:** first impression and legal gate. Splash establishes language and offers the two doors (new user vs. returning). Consent is a hard gate — nothing is collected or created until the user has scrolled the policy and explicitly approved; "Approve" stays disabled until then.

**APIs:** none on Splash itself (static + locale list). Consent calls `POST /users/me/consent` on approve, `DELETE /users/me/consent/{consent_id}` on decline.

**Reused components:** `LanguageDropdown` (C6).

**Design note:** this space intentionally has almost no product functionality — its only job is trust and legal clarity before anything else happens. It should never be asked to do double duty as a marketing/upsell surface.

---

## 2. Setup Space

**Pages:** Onboarding — a single 5-step modal shell (Account → Income & steadiness → Savings goal & duration → Choose a template → Review) over a neutral background.

**Responsibility:** the one-time questionnaire that seeds both the user's Profile and their first Budget. This is the only place a user is walked through a fixed sequence — everywhere else in the product, order of operations is the user's choice.

**APIs:** `POST /auth/signup` (step 1), `PATCH /users/me` (steps 2–3, income/goal fields), `GET /budget/starter-templates` (step 4), `POST /budget` (step 5, "Create plan" — writes the seed `budgets` row and its allocations).

**Reused components:** `StepModal` (C11), `TemplateCard` (C5), `DataRow` (C4, review step).

**Design note:** step 5's "Create plan" is a one-way door into the Command space — once created, editing happens through the Plan space's two paths (§4), never by re-running onboarding. There is no "redo onboarding" flow; if a user wants to start over, that's a Plan-space edit, not a Setup-space re-entry.

---

## 3. Command Space (Dashboard)

**Pages:** Dashboard (the hub).

**Responsibility:** the daily home. Surfaces metrics, the current budget split, editable goal + monthly progress bar, and links out to every other space. This is the space every other space assumes the user will return to — it is the anchor, not a peer of the spaces it links to.

**APIs:** `GET /dashboard` (aggregate — plan, goal, metrics, net worth, in one call per API Design Guidelines §7), `PATCH /dashboard/goal` (or `PATCH /budget` directly), `POST /transactions` (quick "+ Add transaction" shortcut into the Records space's data model).

**Reused components:** `ProgressRing`/`ProgressBar` (C8), `AllocationCard` (C1), `PlanSummaryCard` (C2).

**Design note:** the empty state (no plan yet) is a real, designed state, not an edge case — it should read as "let's set one up" rather than a broken/loading screen, since a user who signed in without completing onboarding (or whose plan was somehow cleared) lands here directly.

---

## 4. Plan Space

**Pages:** Savings Plan screen, HITL customize modal.

**Responsibility:** everything about the one active budget — allocations, goal, and the plain-language rationale behind them. This space has exactly one underlying record (the `budgets` row) reachable through **two equally valid entries**: directly from the Dashboard ("Customize"), or via the Conversation space when the assistant proposes a change. Neither entry point is more authoritative than the other (Data Governance Specs §4) — the modal, the validation, and the write endpoint are identical either way.

**APIs:** `GET /budget`, `PATCH /budget` (both entry points call the same endpoint), `GET /budget/progress`.

**Reused components:** `AllocationCard` (C1), `CategoryBar` (C9), `AllocationSlider` (C10, inside the HITL modal), `StepModal` shell reused for the modal (C11).

**Design note:** the "Adjust" action opening the HITL modal is a **within-space** action (Plan → HITL, still Plan space), not a hop into Conversation — the modal can be triggered by a chat suggestion, but using it does not require being in a chat session.

---

## 5. Conversation Space

**Pages:** Chatbot.

**Responsibility:** the assistant, scoped to three jobs only — explaining the plan, surfacing insights, and recommending tweaks — never the primary way to get anything done. Everything reachable here (adjusting the plan, uploading a document) is a **shortcut into another space's write path**, not a parallel one. A user who never opens this space loses no capability.

**APIs:** `GET/POST /chat/conversations`, `GET/POST /chat/conversations/{id}/messages` (streamed), `POST /chat/conversations/{id}/attachments` (shortcut into the Statements pipeline used by the Records space).

**Reused components:** `ChatBubble` (C3), `UploadDropzone` (C7, shared verbatim with the Records space), `AllocationCard` (C1, read-only preview before "Adjust plan" hands off to the Plan space's modal).

**Design note:** because sessions are freely created (Data Governance Specs §3), the empty state for a new session should not assume continuity with a prior one, and opening an old session should carry the "this context may be stale" note at the point of entry, not buried in a settings page.

---

## 6. Records Space

**Pages:** Documents & Data — three tabs: Data (values & choices), Transactions (full history), Documents (uploads).

**Responsibility:** the ground truth. This is the only space where the transaction ledger — the system's single source of truth (Data Governance Specs §7) — is directly browsable, editable, and deletable, and where documents are uploaded for OCR. Any edit here (or anywhere else that touches transactions) re-runs the plan comparison.

**APIs:** `GET/PATCH /preferences` (Data tab), `GET/POST/PATCH/DELETE /transactions` (Transactions tab), `POST/GET/DELETE /statements` (Documents tab).

**Reused components:** `DataRow` (C4), `TxnRow` (C12), `UploadDropzone` (C7).

**Design note:** "no chatbot required" is a design constraint, not just a technical fact — every action in this space must be fully completable by direct interaction with the tab UI, with no step that silently assumes the user will "just ask the assistant" instead.

---

## 7. Identity & Control Space

**Pages:** Profile & Settings.

**Responsibility:** account-level identity and control — language, linked accounts, notifications (currently out of scope per Data Governance Specs §1), and Privacy & data (export/delete). Follows the same header + bottom-nav pattern as the other hub-reachable pages, since it's a peer destination from the Dashboard, not a nested sub-page of it.

**APIs:** `GET/PATCH /users/me`, `GET/PATCH /users/me/preferences`, `GET/POST/PATCH/DELETE /accounts`, `DELETE /users/me` (full account deletion).

**Reused components:** `LanguageDropdown` (C6).

**Design note:** "delete my data" (Functional Requirements #23) lives here, and its confirmation flow should make clear what it actually removes (per File System Structure §6 — all `{user_id}/`-scoped keys plus the corresponding DB rows), not a vague "are you sure."

---

## 8. Review Space (Administration — internal only)

**Pages:** Admin — Feedback & Reported Issues list, Product Catalog management.

**Responsibility:** the one space with no end-user-facing counterpart. Exists purely so internal staff can review what users have reported and manage the small recommendation catalog — it does not surface user financial data beyond what a reported issue or reaction references.

**APIs:** `POST /admin/auth/login`, `GET /admin/feedback`, `GET/PATCH /admin/issues`, `GET/POST/PATCH/DELETE /admin/products`.

**Design note:** built on a completely separate auth surface (API Design Guidelines §8) and, being internal, is not held to the same RTL/consumer-design polish bar as the six user-facing spaces above — functional clarity for reviewers matters more here than brand consistency.
