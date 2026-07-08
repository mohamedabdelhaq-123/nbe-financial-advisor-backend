# Seeding Guide

`manage.py seed_db` populates the local database with synthetic, internally
consistent mock data across every domain in `docs/Data_Governance_Specs.md`,
so the API can be exercised end-to-end without running a real
upload/OCR/statement pipeline first. See `PLAN_SEED.md` at the repo root for
the full design rationale — this doc is just the "how do I run it" reference.

## Quick start

```bash
python manage.py seed_db
```

Or against the dev Docker stack:

```bash
docker compose exec backend python manage.py seed_db
```

That's it — 5 users with accounts, transactions, budgets, conversations,
feedback, and a recommendation catalog, all wired together with valid
foreign keys.

## Options

| Flag | Default | What it does |
|---|---|---|
| `--users N` | `5` | Number of end users to generate. |
| `--seed 1234` | none | Fixes Python's `random.seed()`, so the same flags always produce the same data. |
| `--gen_statements` | `False` | Also generates the Statements domain (see below). Accepts a bare flag or an explicit value: `--gen_statements`, `--gen_statements=true`, `--gen_statements=false`. |
| `--force` | `False` | Bypasses the `DEBUG`-only guardrail (see below). |

Examples:

```bash
python manage.py seed_db --users 10 --seed 42
python manage.py seed_db --gen_statements
python manage.py seed_db --users 3 --gen_statements=true --seed 7
```

## `--gen_statements`: why it's off by default

This is a **DB-only** seed — there's no real file sitting in SeaweedFS
behind any of it. If a `StatementFile` row is created with a fake
`seaweed_file_id`, that's a pointer to a file that doesn't exist.

- **Default (`False`)**: the Statements domain is skipped entirely — no
  `BankStatementTemplate`, `StatementFile`, `StatementOcrResult`, or
  `StatementNormalized` rows. Every transaction is a plain
  `source="manual"` row with `statement=None` — a fully legitimate path
  (manual dashboard entry), with nothing dangling.
- **With the flag on**: a small shared template pool plus 1–2
  `StatementFile` rows per bank account are created, each with an
  obviously-fake placeholder `seaweed_file_id` (`seed-placeholder-<uuid>`)
  so it reads as synthetic if anything ever tries to resolve it. A portion
  of that account's transactions are then attributed to whichever
  statement covers their date (`source="statement"`); the rest stay
  manual — a realistic mix, not "every transaction came from a file."

Use it when you specifically need to test statement-linked flows (e.g. the
transaction detail view's `statement_id`, or anything that filters
`source=statement`). Skip it (the default) for everything else.

## Guardrail: local dev only

Per `docs/Environment_Profiles.md`, synthetic/fake data is a
**local-development-only** concept. The command refuses to run when
`settings.DEBUG` is `False`:

```
CommandError: Refusing to seed synthetic data: settings.DEBUG is False. ...
Pass --force to override.
```

Only pass `--force` if you're certain you're pointed at a local, throwaway
database — never staging or production.

## Rerunning: flush-then-reseed

**Every run wipes its own previously-seeded rows first**, then reseeds
fresh. There's no separate `--flush` flag — it's always on. This makes
`--seed N` genuinely reproducible instead of accumulating a growing pile of
`seed_user_0`, `seed_user_0_2`, etc. on every rerun.

The flush is scoped to rows this command owns, identified by a fixed
marker, so it will **never touch your own manually-created dev data**:

- Users: `User.objects.filter(email__startswith="seed_user_")` — deleting
  these cascades (via `CASCADE`/`OneToOneField`) through essentially every
  per-user row: bank accounts, consent records, preferences, transactions
  and everything computed from them, budgets, conversations, feedback,
  recommendation logs.
- Admins: `AdminUser.objects.filter(email__startswith="seed_admin_")`.
- Shared catalogs this command fully owns, cleared unconditionally:
  `Product` (matched by `external_link` prefix, cascades to
  `ProblemStatement`) and `BankStatementTemplate` (matched by
  `layout_signature` prefix).

A user you created by hand (e.g. `test@example.com`,
`statements-test@example.com`) is untouched by any of this — only the
`seed_user_*` / `seed_admin_*` / seed-marked catalog rows are deleted.

## What you get

- **Users**: `seed_user_0@example.com` … `seed_user_{N-1}@example.com`,
  password `SeedPass123!` for all of them. Each has 1–2 bank accounts,
  preferences, and consent records.
- **Transactions**: ~4 months of history per account — a monthly salary
  credit, a few recurring merchants (Vodafone, Netflix, gym), and 15–30
  one-off purchases, with a running `balance` maintained per account so
  `GET /analytics/net-worth` and `BankAccount.current_balance` reflect real
  numbers.
- **Aggregations**: `RecurringCharge` and `AnomalyFlag` rows derived from
  the actual generated transactions (not invented independently),
  `SpendingPatternInsight` (`cash_flow`, `income_stability`),
  `MonthlySummary`, and a `NetWorthSnapshot` per user.
- **Budgets**: one `Budget` per user with allocations that sum to exactly
  100%, plus one `BudgetHistory` snapshot.
- **Conversations**: 1–2 conversations per user with a few messages
  (including one with a `widget_json` allocation-slider payload) and
  message references pointing at real transactions/statements.
- **Feedback**: a few `Reaction`s per user targeting real transactions, and
  occasionally a `ReportedIssue`.
- **Recommendation**: a small hand-authored product catalog (~7 products)
  with problem statements and a few `RecommendationLog` entries per user.
- **Administration**: two `AdminUser` accounts —
  `seed_admin_reviewer@example.com` (role `reviewer`) and
  `seed_admin_super@example.com` (role `super_admin`), password
  `SeedAdminPass123!` for both.

Credentials for everything created are also printed to stdout at the end of
the run.

## What's intentionally *not* seeded

- `Ping` — trivial health-check row, unrelated to product data.
- Real bytes in SeaweedFS, even with `--gen_statements` on — this is a
  DB-only seed; anything that tries to actually download a seeded
  statement file will not find one.
- Real embeddings on `Transaction.embedding`, `MonthlySummary.embedding`,
  or `ProblemStatement.embedding` — left `null`. There's no local embedding
  model wired into this command, so RAG/similarity search won't return
  meaningful results against seeded rows; it exercises the surrounding
  CRUD/API surface, not the AI matching layer.
