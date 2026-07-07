# Environment Profiles
**AI-Powered Personal Financial Advisor — Graduation Project (NBE)**

Specifies the environments the system runs in, what each represents, and the constraints/requirements specific to it. The guiding rule (DevOps Planning Proposal §5.6): **the same compose topology across every environment — only local development is allowed to differ, and only for developer speed.** Nothing about *how a request flows through the system* should change between environments; only *which concrete service answers a call* changes.

---

## Overview

| Environment | Purpose | Runs on | Model serving |
|---|---|---|---|
| **Local development** | One person's daily work on their own slice | A developer's laptop | Online OpenAI-compatible provider (no GPU needed) |
| **Shared development / integration (staging)** | Cross-slice integration testing, golden-dataset evaluation | A shared server, mirrors production | Real vLLM, real GPU |
| **Production** | Live system at NBE | NBE's on-prem, air-gapped machine(s) | Real vLLM, real GPU |

---

## 1. Local Development

**What it represents:** a single developer working on their own slice of the system without needing the whole stack running, and without needing a GPU.

**Constraints & rules:**
- Uses **Docker Compose profiles**, so a teammate runs only what they need (e.g. `--profile frontend`) rather than the entire system every time.
- **AI calls go to an online, OpenAI-compatible provider** instead of vLLM — nobody needs a GPU to develop against the AI service locally. The AI Specialist is the exception, testing the real vLLM directly on their own hardware when working on model-specific behavior.
- **No SQLite substitution "just for convenience."** Postgres + pgvector runs locally too (via the backend repo's small compose file — DevOps Planning Proposal §5.4) — pgvector's behavior doesn't match SQLite closely enough to trust, and using it locally would risk shipping bugs that only appear once pgvector's real semantics are in play.
- **Synthetic/fake data only.** Because local development talks to an external, online AI provider, real NBE statements or real user financial data must never be used here — synthetic bank statements and fabricated transaction data are the only acceptable test fixtures at this level.
- `.env` files (git-ignored) with a committed `.env.example`, loaded via `env_file` — see DevOps Planning Proposal §5.8, option A/D.
- The frontend does not strictly require Docker at all — `npm run dev` is sufficient unless a teammate specifically needs to match a pinned Node version via the frontend's (optional) compose file.

---

## 2. Shared Development / Integration (Staging)

**What it represents:** the environment where the three repos' independent work actually meets — cross-slice integration testing, and the place non-deterministic AI quality is measured against reality rather than assumed.

**Constraints & rules:**
- **Mirrors production exactly** in topology: real vLLM on a real GPU, real Postgres, real SeaweedFS, real MinerU, same container set as production. The only environments allowed to differ from each other in *shape* are local dev vs. everything else — staging is not a "local dev with more services," it's production's twin.
- **This is where the golden-dataset evaluation must run against the actual vLLM model**, not only against the online provider used in local dev — model behavior differences between the online provider and the real self-hosted model are exactly the kind of thing that must be caught here, before they reach production (Guardrail 2, see §4).
- **CI-built, version-pinned images are what get deployed here** — staging runs the same artifact-promotion path production will use (build → scan → export → load into an on-prem-style registry → human-gated `docker compose up`), so the deployment mechanics themselves get exercised before they matter for real.
- Real vLLM, real Postgres, real SeaweedFS also means this is the first environment where realistic **performance** characteristics (OCR latency, embedding generation time, chat streaming latency) can be observed — local dev's online-provider substitution makes those numbers meaningless.
- **Data policy:** should use realistic (not necessarily real-NBE) data — closer to production shape than local dev's synthetic fixtures, but still not actual customer financial data unless NBE has explicitly approved staging for that purpose.

---

## 3. Production

**What it represents:** the live system, deployed on NBE's premises, fully offline per the bank's air-gapped requirement.

**Constraints & rules:**
- **Fully offline.** No image pulls, no package installs, no model-weight downloads happen on the production machine itself — everything is prepared in advance and carried across the air-gap on approved media (DevOps Planning Proposal §5.9).
- **Version-pinned everything.** Exact image versions, exact model weights (mounted as a versioned/checksummed volume, never baked into an image), restart policies, and resource limits are all fixed — nothing floats to "latest" in production.
- **Human-gated deploys.** `docker compose up` with pinned versions is triggered by a person, not an automated pipeline — there is no "CD pushes to prod" step, because the pipeline cannot reach production at all.
- **Secrets live only on the server**, injected at runtime (env vars or Docker secrets/SOPS+age) — never in git, never baked into an image (DevOps Planning Proposal §5.8, option B/E).
- **Backups with tested restores** — scheduled `pg_dump` + SeaweedFS snapshots, with an actual rehearsed restore on staging so the team knows the backup is real, not just "probably fine" (schedule/retention to be confirmed with NBE; daily proposed).
- **Offline monitoring** — Prometheus + Grafana + Loki, plus a GPU exporter for vLLM, all self-hosted (no external SaaS monitoring, consistent with the air-gap requirement).
- **Rollback** keeps the previous image version on hand specifically so a bad deploy can be reverted quickly without needing network access to re-pull anything.

---

## 4. Cross-Environment Guardrails

Because local development intentionally diverges from staging/production on model serving (online provider vs. real vLLM), two guardrails keep that divergence from silently causing production surprises:

1. **One interface, switched by env var.** The AI service always calls an OpenAI-compatible endpoint — only the base URL and key differ between local dev (online provider) and staging/production (vLLM). No code branches on environment; only configuration does. If a feature only works against one provider's quirks, that's a defect in the abstraction, not something to special-case.
2. **Staging is where real-model behavior is actually verified.** The golden-dataset evaluation is not considered to have "passed" based on the online-provider results from local dev or CI alone — it must also run against the real vLLM model in staging before a release is considered production-ready.

---

## 5. Data Policy Summary

| Environment | Acceptable data |
|---|---|
| Local development | Synthetic/fake bank statements and transactions only |
| Staging | Realistic sample data; real NBE data only with explicit approval |
| Production | Real user data (the system's actual purpose) |

This mirrors the same consent and retention rules defined in the Data Governance Specs regardless of environment — staging and production both still enforce the consent gate and duplicate-transaction checks; only local development's use of an external AI provider is the reason real data is barred there specifically.

---

## 6. Environment Variables Per Environment (cross-reference)

The concrete mechanism (which of `.env`, server-only env vars, `env_file`, or runtime injection is used where) is specified in DevOps Planning Proposal §5.8 — this document only states the environments; that one states how each environment's secrets are actually delivered.
