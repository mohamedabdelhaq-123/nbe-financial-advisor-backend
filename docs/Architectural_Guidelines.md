# Architectural Guidelines
**AI-Powered Personal Financial Advisor — Graduation Project (NBE)**

Replaces the decisions an opinionated framework would normally make for you: folder structure, where shared components/hooks live, how screens are allowed to talk to the API, and when (rarely) an optimistic update is acceptable. This is the frontend's equivalent of System Architecture — the binding structural rules every screen and feature is built within.

---

## 1. Folder Structure

Feature-oriented, not type-oriented — a folder groups everything a feature needs, rather than scattering a feature's parts across global `components/`, `hooks/`, `pages/` folders by kind. Cross-cutting pieces (used by more than one feature) are the only things that live in shared top-level folders.

```
src/
  app/
    routes.tsx              # React Router tree, /en and /ar prefixes
    providers.tsx           # TanStack Query client, i18next, theme, error boundary
  features/
    onboarding/
      components/           # StepModal steps, TemplateCard usage, etc.
      hooks/                 # onboarding-specific hooks (e.g. useOnboardingStep)
      api.ts                 # this feature's API calls (thin wrappers, see §4)
      types.ts
    dashboard/
    plan/                    # the /budget screen + HITL modal
    chat/
    documents-data/          # Data / Transactions / Documents tabs
    profile/
  shared/
    components/              # the LIB components: AllocationCard, PlanSummaryCard,
                              # ChatBubble, DataRow, TemplateCard, LanguageDropdown,
                              # UploadDropzone, ProgressRing, CategoryBar,
                              # AllocationSlider, StepModal, TxnRow (C1–C12 in the wireframe spec)
    hooks/                    # generic hooks with no feature ownership (useDebounce, useMediaQuery)
    lib/
      api-client.ts           # single fetch/axios instance: base URL, auth header injection,
                              # error-shape parsing (see API Design Guidelines §10)
      query-keys.ts           # centralized TanStack Query key factory (see §4)
    stores/                   # Zustand stores — kept minimal, see §3
    i18n/
      locales/en.json
      locales/ar.json
    styles/
      theme.css               # DaisyUI theme tokens (see Design Guidelines)
```

**Rule:** a component only moves from a feature folder into `shared/components/` once a second feature actually needs it — not preemptively "just in case." Premature sharing is as much a defect here as premature duplication; the wireframe spec's tagged library components (C1–C12) are the exception, since they're explicitly called out as multi-screen from the start.

---

## 2. Component Base

Every visual primitive is a DaisyUI semantic class first (`card`, `btn`, `stat`, `badge`) — see Design Guidelines and CSS documents. A `shared/components/` component wraps DaisyUI markup with the project's specific data shape and behavior; it does not reimplement styling DaisyUI already provides.

---

## 3. State: Three Kinds, Three Tools, Never Mixed

The architecture keeps three categories of state deliberately separate, because blurring them is the most common source of stale-data and re-render bugs in a data-heavy dashboard like this one:

| Kind | Tool | Examples |
|---|---|---|
| **Server state** (anything that lives in the DB) | TanStack Query | transactions, budget, dashboard metrics, chat messages |
| **UI state** (client-only, ephemeral or cross-component) | Zustand | active modal, selected date-range filter, sidebar open/closed |
| **Form state** (in-progress user input, not yet submitted) | React Hook Form + Zod | onboarding steps, manual transaction entry, allocation sliders before confirm |

**Rule:** server data is never duplicated into a Zustand store "for convenience." If a value comes from the API, TanStack Query's cache is the only place it lives; components read it via a query hook, not via a store that was manually populated from a query response. This avoids the two-sources-of-truth bug where a Zustand copy goes stale after a mutation invalidates the query cache.

---

## 4. API Interaction Rules

- **One API client, one place.** All requests go through `shared/lib/api-client.ts` — no feature makes a raw `fetch` call of its own. This is where the auth header, base URL, and the shared error-shape parsing (API Design Guidelines §10) live, once.
- **A feature's `api.ts` is a thin typed wrapper**, not business logic — it calls the shared client and returns typed data; validation/derivation stays in components or hooks, not smuggled into the API layer.
- **Query keys are centralized** (`shared/lib/query-keys.ts`) as a factory function per resource (`queryKeys.transactions(filters)`, `queryKeys.budget()`), so invalidation after a mutation (e.g. `PATCH /budget` invalidating `queryKeys.budget()` and `queryKeys.dashboard()`) is consistent and can't drift into ad hoc, hand-typed key arrays scattered across features.
- **Server-derived values are never recomputed on the frontend.** Per API Design Guidelines §3, the backend returns both `allocated_percentage` and `allocated_amount` — the frontend displays both, it does not multiply income × percentage itself. The same applies to `months_remaining` on a goal — displayed as received, not recalculated client-side.
- **Pagination is driven by the backend's chosen strategy per resource** (API Design Guidelines §5) — a feature using cursor-paginated messages does not attempt to retrofit page numbers onto it, and a feature using offset-paginated transactions does not attempt infinite-scroll cursor logic onto it.
- **Every mutation invalidates, rather than manually patches, the affected queries** by default (see §5 for the narrow exception).

---

## 5. Optimistic Updates — the Exception, Not the Default

**Default: wait for the server response, then update.** This is a financial application — an allocation slider, a transaction edit, or a budget change showing a value the backend hasn't actually accepted yet risks a user acting on a number that reverts a moment later. The extra latency of waiting for confirmation is an acceptable, deliberate trade-off against that risk.

**Optimistic updates are permitted only where all three hold:**
1. The action is trivially, almost-never-failingly reversible (no real business-rule validation on the backend beyond basic shape).
2. A failure is low-stakes and easy for the user to notice/undo if it does happen.
3. The perceived-responsiveness gain is meaningful for that specific interaction.

**Concretely allowed:** marking an anomaly as resolved/dismissed, liking/reacting to a chat message, toggling a UI preference (default view, language). **Not allowed:** anything touching `transactions`, `budget` allocations/goal, or bank account records — these always wait for the real response and show a loading state, consistent with §4's "no client-side recomputation" rule; showing an unconfirmed number is a variant of the same problem as computing one yourself.

---

## 6. Forms

React Hook Form + Zod, one schema per form, shared between client-side validation and the TypeScript types the form consumes — not two hand-maintained shapes. The Zod schema is the one source of truth for "what does a valid submission look like" on the frontend; it does not need to be a perfect mirror of the backend's DRF serializer, but any rule enforced server-side that the user should see before submitting (e.g. allocations summing to 100) is also expressed in the Zod schema, so the error surfaces before a round trip, not only after a `422`.

---

## 7. Widgets: Chat-Rendered Structured Output

The Tool UI layer (on top of assistant-ui) intercepts structured JSON payloads from the AI Service's responses and routes them to the same `shared/components/` widgets used elsewhere in the app — the allocation-slider widget shown in a HITL chat modal is the same component as the one used from the dashboard's "Customize" action, not a separate chat-only implementation. This keeps the "two edit paths, one write path" rule (Data Governance Specs §4) true at the component level as well as the API level: there is exactly one `AllocationSlider` component, reused, not forked.

---

## 8. Routing & i18n

- Routes are prefixed `/en` / `/ar` (React Router), matching Frontend Decisions #3.
- Locale JSON files are per-locale, human-reviewed (Frontend Decisions #10) — no machine-translated financial copy ships without review, given the risk of a mistranslated financial term.
- Numerals stay Western/LTR even inside Arabic text blocks (see Design Guidelines §3, CSS §5) — this is enforced at the component level by routing all numeric display through a shared `<Amount>` / `<Number>` formatting component (using `Intl.NumberFormat`/`Intl.DateTimeFormat`) rather than every feature hand-formatting numbers inline. A feature does not call `Intl.NumberFormat` directly inside its own component — it goes through the shared formatter so the numerals/currency/date rule is enforced in exactly one place.

---

## 9. Accessibility & RTL QA (process, not code, but binding here)

Manual WCAG 2.1 AA checks and an EN + AR screenshot pair per UI PR (Frontend Decisions #19–20) are treated as part of "done" for any new screen or shared component — not a separate QA-team responsibility bolted on afterward.
