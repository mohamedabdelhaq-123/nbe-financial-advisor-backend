# Pipeline
**AI-Powered Personal Financial Advisor — Graduation Project (NBE)**

Specifies the information flow from user to AI and back, and how the agentic loops are structured internally. Where System Architecture states *which agent owns what responsibility*, this document traces *the path a piece of information actually takes* — from a user action, through orchestration and delegation, to a stored result or a streamed reply.

---

## 1. Two Distinct Pipelines

The AI service handles exactly two kinds of information flow, and they never cross:

- **The Ingestion Pipeline** — one-shot, deterministic-then-agentic, triggered by a document upload. No back-and-forth with the user mid-flow.
- **The Conversational Pipeline** — multi-turn, orchestrated by the Maestro, triggered by a chat message. Always mediated by the Maestro; a sub-agent never talks to the user directly.

Everything below is organized around this split.

---

## 2. The Ingestion Pipeline (document → ledger)

```
User uploads a statement (dashboard or chat shortcut — same endpoint)
   │
   ▼
Extraction Tool (MinerU) — NOT an agent, no reasoning
   → deterministic OCR/layout extraction
   → outputs: content.json, markdown, extracted tables/images, confidence_score
   │
   ▼
Normalization Agent
   → reads MinerU's content.json
   → looks up bank_statement_templates by (bank_name, layout_signature)
   → if no template match: LLM infers the column mapping, a new template is created for future reuse
   → maps bank-specific columns to the canonical transaction schema
   → resolves/creates the bank_accounts record
   → runs a preview duplicate check (composite key: user, account, date, amount, raw merchant) and
     writes the proposed transaction batch to statement_normalized.normalized_json — NOT yet inserted
   │
   ▼
User approval gate (REST, not agentic — docs/API_GUIDE/Data_Shapes_Statements.md)
   → user reviews/corrects the proposed batch, POSTs it back in full
   → duplicate check re-runs for real at this point; the rest insert, generating a semantically rich
     internal description per transaction and embedding it (not stored as separate text — only the
     vector persists; merchant_raw keeps the original statement text)
   │
   ▼
Transactions ledger (Aggregations) — the single source of truth from this point forward
   │
   ▼
Background jobs (not agents — scheduled/event-triggered): recurring-charge detection,
anomaly detection, monthly summary computation, spending-pattern insights refresh
```

**Why MinerU is not an agent:** it has no reasoning step and no decision to make — it is a deterministic transformation (bytes in, structured text/tables out). Calling it a "tool" rather than an "agent" throughout this document is deliberate: nothing downstream should expect it to handle ambiguity or make a judgment call.

**The approval gate is not a loop-back.** "No back-and-forth with the user mid-flow" (§1) describes the AI processing itself — extraction and normalization run start-to-finish without pausing to ask the model anything. The user-facing review step between normalization and ledger insert is an ordinary REST request/response (`GET .../normalized` then `POST .../transactions`), not a continuation of an agentic loop; the Normalization Agent's job is finished the moment it writes `normalized_json`.

**Where this pipeline ends:** once transactions are written and background jobs have run, this pipeline's information flow is complete. It does not loop back to ask the user anything — any further follow-up (correcting a category after the fact) happens through the ordinary Conversations/Records write path, not as a continuation of this pipeline.

---

## 3. The Conversational Pipeline (message → response)

```
User message
   │
   ▼
Maestro (orchestrator — the only agent that ever talks to the user)
   → parses intent BEFORE delegating (clarifies ambiguity upfront, not after a failed sub-agent call)
   → assembles user_context (income_bracket, active budget, recent summaries, anomaly flags, recurring charges)
   │
   ├── intent: analysis question ──────────────► Analysis Agent
   ├── intent: create/adjust a plan ───────────► Planner Agent (see §5 for its own internal loop)
   └── intent: product/recommendation query ───► Recommendation matching (via Analysis Agent's findings)
   │
   ▼
Sub-agent returns STRUCTURED findings to the Maestro — never raw query results, never talks to the user itself
   │
   ▼
Maestro translates structured findings into a conversational reply
   → writes the message to `messages`
   → creates `message_references` rows for every entity cited (transaction, anomaly, recommendation, budget)
   → renders structured content as a widget (allocation slider, product card, chart) rather than prose numbers
   │
   ▼
Reply streamed to the user (SSE), with inline citations/widgets
```

**The one-directional rule:** a sub-agent's output always returns to the Maestro, never directly to the user and never directly into a database write. Even when the Analysis Agent's finding will result in a budget suggestion, the Maestro is the one that surfaces it to the user as a confirmable widget — the sub-agent computed the number, but did not decide how or whether to show it.

---

## 4. Analysis Agent — Tool Selection Within the Loop

The Analysis Agent's job inside the loop is to pick the right pre-computed source rather than recompute from scratch:

| Question type | Source read |
|---|---|
| Spending total for category/period | `monthly_summaries.category_breakdown_json` |
| Recurring charges list | `recurring_charges` |
| Anomaly detail | `anomaly_flags` JOIN `transactions` |
| Trend over time | `monthly_summaries` series |
| What-if scenario / projection | `transactions` + `budgets` + a live calculation |
| Natural-language / semantic query | pgvector similarity search on `transactions.embedding` |
| Income stability | `spending_pattern_insights` where `insight_type = 'income_stability'` |
| Net worth | `net_worth_snapshots` |

**Rule enforced at this step of the loop:** the Analysis Agent never computes a monthly summary, recurring charge, or anomaly itself — those are background-job outputs it reads, not agent outputs it produces. If a question needs something not yet pre-computed, the correct response is "not available for this period," not an on-the-fly recomputation that bypasses the same logic the background job uses.

---

## 5. Planner Agent — the One Multi-Turn Sub-Loop

The Planner is the only sub-agent with its own internal loop, because plan creation genuinely needs information the system doesn't already have. This loop is relayed through the Maestro (the Planner still never talks to the user directly):

```
Maestro → Analysis Agent: current financial snapshot
Maestro → Planner Agent: snapshot
   │
   ▼
Phase 1 — Questionnaire loop (capped at 7 questions, ends early once sufficient)
   for each turn:
      Planner checks: is this answer already inferable from the snapshot? (e.g. skip asking income
        if salary deposits are visible in transactions)
      if not inferable → ask one question, relay through Maestro to user, wait for answer
      loop continues until profile_complete = true OR 7 questions reached
   │
   ▼
Phase 2 — Plan generation (single pass, no further back-and-forth)
   1. Load matching category-limit reference template (SeaweedFS) by income bracket + household type
   2. Compute required monthly savings for goal within timeline
   3. Identify gap between current surplus and required savings
   4. Rank recommended expense reductions, grounded in the template limits — never invented
   5. Flag subscriptions for review
   │
   ▼
Maestro presents the structured plan conversationally + as a confirmable widget
User confirms → written to `budgets` + `budget_allocations` (same write path as a dashboard edit)
Prior plan snapshotted to `budget_history`
```

**Why this loop is capped and grounded:** an uncapped questionnaire risks the user never reaching a plan; an ungrounded generation risks hallucinated allocations. Both failure modes are closed off structurally (a hard cap, and a mandatory reference-template lookup) rather than left to prompt-level instruction alone.

---

## 6. Guardrails Enforced *Inside* the Loop (not just around it)

These are checked at the point of generation, not only validated afterward:

- **Numeric traceability** — any figure the Maestro states in a reply must resolve to a real `message_references` entity. This is enforced as part of assembling the reply, not as a post-hoc filter — the Maestro cannot state a number it cannot also cite.
- **Grounded budget percentages** — any percentage a reply cites must trace to a SeaweedFS reference template lookup that actually happened in that turn, not a plausible-sounding figure.
- **Topic lock** — the Maestro's system prompt restricts it to finance-related queries; an out-of-scope message is declined at the Maestro level, before any sub-agent is invoked.
- **PII scrubbing** — full account/card numbers are masked before `user_context` or any message content reaches the LLM, at the point `user_context` is assembled (Django's responsibility, before the request even leaves for the AI service).
- **Write power is checkpointed, not immediate** — any state-changing outcome of the loop (a budget change, a manually-entered transaction suggested mid-chat) is only committed once the user confirms inside a widget; the loop's own completion is never itself the trigger for a database write.

---

## 7. What Is Explicitly Not Part of Any Loop

- **The dashboard's read path never invokes an agent.** Dashboard metrics are served from tables background jobs already populated — an agent is not invoked on-demand just because a user opened a screen.
- **Manual data entry never invokes the Maestro.** A transaction typed directly into the Records space triggers only the deterministic duplicate check and the (non-conversational) single-transaction anomaly-check background task — no agentic loop is involved at all.
- **A sub-agent never re-enters the loop on its own.** If the Analysis Agent needs more context, it returns that as a finding to the Maestro, which decides whether to ask the user or call another sub-agent — a sub-agent does not call another sub-agent directly, keeping the whole loop's shape a single-level star (Maestro at the center) rather than a chain.
