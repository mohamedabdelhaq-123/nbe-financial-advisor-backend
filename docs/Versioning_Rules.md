# Versioning Rules
**AI-Powered Personal Financial Advisor — Graduation Project (NBE)**

> **Status: Proposal.** Unlike the DevOps Planning Proposal's confirmed container/repo decisions, none of this has been through a team meeting yet. Treat every rule below as a starting point to accept, amend, or reject — the goal is one shared convention across all three repos (frontend, backend, AI service) before the first feature branches start piling up.

Specifies branch naming, what each branch type is for, the rules applied to protected branches, and how versions are tagged for the offline deployment process described in the DevOps Planning Proposal.

---

## 1. Branches, One Convention Across All Three Repos

Since the team runs three separate repos (Option A, confirmed — DevOps Planning Proposal §5.2), branch naming is kept **identical across all of them** so nobody has to remember a different convention per repo.

| Branch | Purpose | Lives forever? |
|---|---|---|
| `main` | Always deployable. Every merge here is a candidate for staging, then production. Protected (see §3). | Yes |
| `feature/<short-name>` | New functionality, scoped to one PR's worth of work (e.g. `feature/hitl-allocation-modal`). | No — deleted after merge |
| `fix/<short-name>` | Bug fixes that aren't urgent enough to bypass normal review (e.g. `fix/goal-progress-rounding`). | No — deleted after merge |
| `hotfix/<short-name>` | Urgent production fix, branched from `main`, merged back to `main` directly with expedited (but not skipped) review. | No — deleted after merge |
| `docs/<short-name>` | Documentation-only changes (this document set, README updates). | No — deleted after merge |
| `chore/<short-name>` | Tooling, CI config, dependency bumps — no product behavior change. | No — deleted after merge |

**No long-lived `develop` branch.** With a small team and one active project phase, an extra long-lived integration branch would just be a second place for merge conflicts to hide before they reach `main`. Shared development/integration testing (DevOps Planning Proposal §5.6) happens by deploying `main` itself to the staging environment, not by maintaining a separate branch for it.

**Naming rule:** lowercase, hyphen-separated, no ticket-tracker prefix required (the project doesn't currently use one) — but if the team later adopts issue tracking, the convention becomes `feature/<issue-number>-<short-name>` at that point, not before.

---

## 2. Commit Messages

Conventional, short, imperative mood (`add allocation slider widget`, not `added` or `adding`). A one-line summary is required; a body explaining *why* (not just *what*) is expected for anything touching a guardrail (duplicate-check logic, consent handling, budget-write validation) — those are exactly the changes a future engineer will `git blame` looking for reasoning, per the DevOps proposal's "handoff to future engineers" framing (§5.9).

---

## 3. Branch Protection on `main`

Applies identically to all three repos:

- **No direct pushes to `main`.** Every change arrives via pull request.
- **Required checks before merge** (from the CI pipeline — see DevOps Planning Proposal §5.7): lint, format check, tests, build. A red check blocks merge regardless of who's asking.
- **At least one review** required, even on a small team — the point isn't gatekeeping, it's a second set of eyes on anything that touches money, consent, or auth.
- **AI service exception, explicitly not an exception:** the AI service's non-deterministic quality/eval tests are **non-blocking** by design (DevOps Planning Proposal §5.7) — but its deterministic tests (boot, response shape, RAG retrieval) are blocking like everywhere else. A PR cannot merge "because the eval score is close enough" if the deterministic suite is red.
- **No force-pushes to `main`** — a mistaken merge is reverted with a new commit, not rewritten away, so history stays trustworthy for the "handoff to future engineers" goal.

---

## 4. Versioning & Image Tags

Each repo's CI builds and tags an image on every merge to `main` (DevOps Planning Proposal §5.5, §5.9):

- **Tag format:** `<service>:<git-sha>` always (e.g. `api:a1b2c3d`), so any deployed image is traceable to an exact commit without ambiguity.
- **A human-readable version tag is added alongside the git-sha tag for anything actually deployed to staging or production** — semantic versioning (`vMAJOR.MINOR.PATCH`) per repo, bumped manually at deploy time, not automatically on every commit. This keeps the day-to-day git-sha tagging lightweight while still giving the `deploy/` compose files a stable, readable version to pin to (rather than a bare git-sha) when a human is reading the compose file later.
- **Each repo versions independently.** There is no single "system version number" spanning frontend/backend/AI service — a backend patch release does not require bumping the frontend's version. The `deploy/` compose file is what ties a specific combination of the three versions together for a given deployment (see §5).

---

## 5. Coordinating a Deployment Across Three Repos

Because the three services version independently, a deployment is defined by **which tag of each service the `deploy/` compose file points to** (backend repo's `deploy/docker-compose.prod.yml` — see DevOps Planning Proposal §5.3):

```
# excerpt, illustrative
services:
  web:  { image: "registry/web:v1.4.0" }
  api:  { image: "registry/api:v2.1.0" }
  ai:   { image: "registry/ai:v1.2.3" }
```

A deployment's "version" is really this trio, recorded once in the `deploy/` compose file at deploy time and committed — so rolling back means restoring the previous trio of pins, not guessing which combination was last known-good.

---

## 6. What This Document Deliberately Doesn't Cover

- **CI pipeline steps themselves** — see DevOps Planning Proposal §5.7.
- **How images get from CI to the air-gapped production registry** — see DevOps Planning Proposal §5.9.
- **Environment-specific config differences** — see the Environment Profiles document.
