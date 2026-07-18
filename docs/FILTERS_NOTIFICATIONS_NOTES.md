# Filters, Dashboard Windowing, Budget/Account Bugs, Auth, Notifications — Review Notes

Branch: `fix/filters-notification`, based off `main` (which has
`fix/category-table` merged). Six commits, matching PLAN.md's checkpoints 1-6
(checkpoint 7 is this regression pass + doc).

```
ff63202 fix(dashboard): support period and account_id filtering on GET /dashboard
c85ff87 fix(transactions): category filter matches by name, add type filter for income/expense
06c844f fix(budgets): accept changed_via="chat" (was chat_hitl, never actually sent)
1651f18 fix(statements): stop creating a duplicate BankAccount per upload when no account_id is given
57d840d feat(auth): password reset and email verification for local users
cc67dcb feat(notifications): email users on budget changes, detected anomalies, and finished statement uploads
```

Full diff: `git diff main..cc67dcb` (or `main..HEAD` once this checkpoint lands).

---

## 1. Architecture decisions (confirmed with you during planning)

- **Dashboard windows are computed, not stored**: `_resolve_window(period)`
  (`core/views/budgets.py`) derives `(start, end, prev_start, prev_end)` live
  from `date.today()` for all four `period` values — no new table, no
  snapshotting. `prev_start`/`prev_end` is always the immediately preceding
  span of the *same length in days* as the requested window, which is what
  lets one `_percentage_change()` helper serve "this month vs last month"
  and "this year vs the same number of preceding days" without a special
  case per period.
- **Dashboard response field names are unchanged** (`current_month_spend`,
  `spend_change_percentage`, etc.) even though they now mean "the requested
  window," not literally "this calendar month" — a deliberate non-breaking
  choice since the frontend already reads these exact names expecting
  windowed values (see contract table below).
- **`category`/`type` transaction filters are explicit `django_filters`
  fields**, not `Meta.fields` auto-generation — `category` because
  auto-generation builds a `ModelChoiceFilter` expecting a `Category` pk now
  that it's a real FK (not the name string the API/frontend actually send);
  `type` because it's not a real model field at all, just a
  income/expense grouping over `transaction_type` that a single-value exact
  filter can't express (expense = debit OR fee OR transfer).
- **`changed_via` accepts `"chat"`, not `"chat_hitl"`** — traced to the
  frontend's actual `AllocationSliderTool` call site, which has always sent
  `"chat"`; `chat_hitl` had zero real callers anywhere, only docstrings/seed
  data.
- **BankAccount de-dup is re-keyed on `(user, bank_name)` only** —
  `masked_account_number` moved to `defaults=`. Root cause: the mock AI
  service's `account_hint` is `"****" + statement.checksum[:4]`, i.e.
  derived from the *uploaded file's own bytes*, not a real extracted account
  number — different on every upload by construction, so it could never
  match an existing account across two statements from the same real bank
  account. Flagged explicitly in code as a mock-data workaround to revisit
  once real OCR extracts a genuine masked number.
- **Password reset / email verification are stateless** — both reuse
  Django's `PasswordResetTokenGenerator` machinery
  (`core/auth_tokens.py`): a token is a salted HMAC over the user's pk plus
  a piece of state that changes once the token is "used" (`password` for
  reset — `set_password()` changes it; `email_verified` for verification —
  the confirm view itself flips it). No new "tokens" table. Scoped
  explicitly to `core.User` (local email+password accounts) — not
  `AdminUser`, not bank-linked (OAuth) accounts.
- **Notifications are email-only** — this was corrected mid-plan (originally
  drafted as an in-app/SSE feed with a `Notification` model; you clarified
  you meant email only). Final shape: one `notification_service.notify(user,
  subject, body)` wrapper around the existing Gmail-SMTP `send_email()`,
  wired into three trigger points (budget changed, anomaly detected,
  statement normalized) that previously had no notification at all. No new
  model, no new SSE event type, no `/notifications` endpoints.
- **No `api/schema.json` checkpoint** — dropped after checking the actually-
  installed `drf_spectacular.views.SpectacularAPIView`: its default
  `renderer_classes` already includes both YAML and JSON, so
  `GET /api/schema/?format=json` already returns JSON today with zero
  backend changes. Nothing built here; see PLAN.md's note on this.

## 2. Contract changes (additive/backward-compatible unless noted)

| Endpoint | Before | After |
|---|---|---|
| `GET /dashboard` | No `period`/`account_id` params; always "this calendar month vs last calendar month," all accounts combined | Accepts `?period=this_month\|last_month\|last_3_months\|this_year` (default `this_month`, same behavior as before if omitted) and `?account_id=<uuid>` (404 if not owned); every metric — spend, inflow, both change percentages, net worth, each allocation's `percentage_used` — computed over that window/account |
| `GET /transactions` | `?category=<name>` silently matched nothing (FK pk mismatch); no `?type=` param at all | `?category=<name>` matches case-insensitively by name; `?type=income\|expense` now implemented (income→credit, expense→debit/fee/transfer) |
| `PATCH /budget` | `changed_via: "chat"` → 422 (only `chat_hitl` accepted) | `changed_via: "chat"` → 200. **Breaking in one direction**: `"chat_hitl"` (never actually sent by anything real) is no longer accepted — flag this only if something you're not aware of still sends it |
| `POST /auth/password-reset/request` | Didn't exist | New. Always 202 |
| `POST /auth/password-reset/confirm` | Didn't exist | New. 200 on success, 422 on bad/expired/reused token |
| `POST /auth/verify-email/request` | Didn't exist | New. `IsAuthenticated`, 202, 502 on genuine send failure |
| `POST /auth/verify-email/confirm` | Didn't exist | New. 200 on success, 422 on bad/expired/reused token |
| `POST /auth/signup` | No email sent | Now also fires a verification email, best-effort (doesn't affect signup's response or status code either way) |

**No frontend action required** for the dashboard/transactions/budget fixes —
these close gaps the frontend was already coded against (see its own
`ASSUMED BACKEND CHANGE` comments in `dashboard.ts`/`transactions.ts`, now
stale and safe to remove). The four new auth endpoints and their request/
response shapes are new frontend work whenever a reset/verify UI is wanted —
nothing currently calls them.

## 3. Assumptions made (worth double-checking)

- **`last_3_months`/`this_year` window definitions**: `last_3_months` is a
  rolling window from 3 calendar months ago (1st of that month) through
  today, not "the last 3 fully-completed calendar months." `this_year` is
  Jan 1 through today. Both are spelled out in PLAN.md's Confirmed
  Decisions; revisit if a different definition was actually intended.
- **Password reset / verification email bodies are placeholder text** — a
  plain `user_id`/`token` pair, not a clickable frontend link. There's no
  `FRONTEND_URL`-style setting anywhere in this backend to build a real
  link against, so this is left for whoever builds the actual frontend
  reset/verify page to wire up (construct the link client-side, or add a
  settings-based base URL here once one exists).
- **`email_verified` doesn't gate anything yet** — login, and every other
  endpoint, works identically whether or not a user has verified their
  email. Purely informational until/unless something is explicitly gated
  on it later.
- **Budget-change email fires unconditionally on every `PATCH /budget`**,
  including a `name`-only change with no allocation changes at all — not
  narrowed to "only when allocations actually changed."

## 4. Known limitations / explicitly deferred (not silently ignored)

- **No rate limiting on `POST /auth/password-reset/request`** — a real
  production concern (repeated requests could be used to spam an inbox),
  explicitly out of scope for this pass.
- **No per-user notification preferences/opt-out** — every trigger point
  emails unconditionally.
- **`match_recommendations()`-style "out of scope" carve-outs from the
  previous Celery/SSE work are untouched** — this pass doesn't add or
  remove anything there.

## 5. Files touched (by area)

- **Dashboard**: `core/views/budgets.py` (`DashboardView`, `_resolve_window`,
  `_window_totals`, replacing the old `_month_totals`)
- **Transaction filters**: `core/filters/aggregations.py`
  (`TransactionFilterSet`)
- **Budget `changed_via`**: `core/serializers/budgets.py`,
  `core/views/budgets.py` (docstring only)
- **Statement/BankAccount dedup**: `core/tasks/statements.py`
  (`run_normalization_phase`)
- **Auth (password reset / email verification)**: `core/models/profile/user.py`
  (+`email_verified`), `core/migrations/0015_user_email_verified.py` (new),
  `core/auth_tokens.py` (new), `core/serializers/auth.py`,
  `core/views/auth.py`, `core/views/__init__.py`, `core/urls.py`
- **Email notifications**: `services/notification_service.py` (+`notify()`),
  `core/tasks/bank_sync.py`, `core/tasks/statements.py`,
  `core/views/budgets.py`
- **Tests (all new files, none replacing existing ones)**:
  `tests/test_dashboard.py`, `tests/test_transactions_filters.py`,
  `tests/test_budgets.py`, `tests/test_statement_account_dedup.py`,
  `tests/test_password_reset_and_verification.py`; extended
  `tests/test_notification_service.py`, `tests/test_bank_sync_tasks.py`

## 6. What was live-verified vs. statically verified only

**Live-verified** (real Postgres via this sandbox's configured DB, real
`pytest` runs, no mocking of the code under test itself):

- Full local test suite after every checkpoint (`pytest -q`), diffed against
  a baseline run of the unmodified branch each time to confirm the same 24
  pre-existing failures/4 errors (real-AI-service-HTTP-dependent and
  real-Redis-hostname-dependent tests that were already failing before any
  of this work — see below) and no new ones.
- Every new/changed behavior has a passing, non-trivial test: dashboard
  window math and account scoping (including a deliberately-reverted check
  that the bank-account dedup test actually fails without the fix, then
  passes with it), transaction `category`/`type` filtering, `changed_via`
  acceptance/rejection, password-reset/verification token issuance-through-
  confirmation-through-replay-rejection, and each of the three new email
  triggers (asserted via `django.core.mail.outbox`, Django's own test email
  backend).

**Statically verified only / not exercised here**:

- **Not run against a live docker-compose stack, real Redis, or a real
  AI-service instance.** This sandbox has none of those; the 24 pre-existing
  failures on this branch's baseline are exactly the tests that need them
  (`tests/test_ai_service.py`'s "real" HTTP paths, `tests/test_bank_sync_tasks.py`'s
  Redis-dependent cases whose `REDIS_URL` points at the docker-compose
  hostname `redis`, `tests/integration/*`). None of this work fixes or
  worsens that gap — confirmed by running the exact same tests against the
  unmodified branch first.
- **No frontend changes made or required** by this pass, and none of it was
  clicked through in a browser — this is a backend-only branch; the
  dashboard/transactions/budget fixes close gaps the frontend already
  codes against, and the new auth endpoints have no frontend caller yet.
- **Password reset / verification emails' actual deliverability** (real
  Gmail SMTP) wasn't exercised — tests assert against Django's locmem test
  backend, not a real send.
