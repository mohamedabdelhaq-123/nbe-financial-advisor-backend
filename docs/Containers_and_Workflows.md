# Containers and Workflows
**AI-Powered Personal Financial Advisor — Graduation Project (NBE)**

Specifies the containers needed to run the system, and the workflows (linting, testing, building, deploying) triggered by each action a developer takes — from a single commit push through to a production release. This is the operational counterpart to Versioning Rules (which covers branches/tags) and Environment Profiles (which covers where things run) — this document covers *what automatically happens* at each step.

---

## 1. Container Inventory

**Rule governing this list:** one independent lifecycle per container (System Architecture §1 / DevOps Planning Proposal §5.1). "Fewer containers" means fewer things to run on one host via one compose file — never merging two independently-scaling, independently-upgraded services into one image.

| Container | Built or pulled | Notes |
|---|---|---|
| Frontend (React) | Built (own repo) | |
| Backend (Django) | Built (own repo) | |
| AI service (FastAPI + LangGraph) | Built (own repo) | Kept together — one logical service, tightly coupled lifecycle |
| MinerU | Pulled | Its own container; heavy OCR dependencies, own lifecycle; GPU need to confirm with AI team |
| vLLM | Pulled | GPU reservation, long weight-loading startup, pinned CUDA/driver versions — never merged with anything else |
| Postgres + pgvector | Pulled | Shared by Django and the AI service; separate tables, separate migration tools (Django migrations vs. Alembic) |
| SeaweedFS | Pulled | Combined "server" mode internally is acceptable — that's an internal-to-SeaweedFS lifecycle match, not a violation of the one-service rule |
| Reverse proxy (Nginx) | Pulled | The only container exposed publicly |
| Monitoring (Prometheus/Grafana/Loki) | Pulled | Production only |

**Acceptable vs. not, restated for this list specifically:** MinerU staying separate from the AI service's own container is the one case worth double-checking as the system evolves — MinerU is reached only by the AI service internally (see Services and Background Tasks §1), but it remains its own container because it has its own heavy dependency set and crash/restart/upgrade cycle independent of the AI service's LangGraph code.

---

## 2. Workflow: Push to a Feature/Fix Branch

Runs on every push, before a PR even exists, so problems surface as early as possible:

| Repo | Checks run |
|---|---|
| All repos | Lint, format check, unit tests, build check, Docker layer caching (speed only) |
| Frontend | + `npm run build` |
| Backend | + Django migration check, secret scanning (TruffleHog), env-validation-at-startup check |
| AI service | + Alembic migration check (own tables only), deterministic tests (boot / response shape / RAG retrieval — blocking) |

Quality/eval tests against the golden dataset (AI service) are **not** run on every push — they're comparatively slow and non-blocking by design; they run at PR level instead (§3).

---

## 3. Workflow: Pull Request Opened / Updated

Same checks as §2, plus:

- **Required review** (at least one — Versioning Rules §3).
- **AI service PRs additionally run the non-blocking quality/eval suite** against the golden dataset, scored with the AI Specialist's rubric — visible on the PR, but does not block merge on its own (only the deterministic suite blocks).
- **CodeRabbit** (or equivalent) auto-generates a PR description/summary and flags issues, *if approved for the NBE project* — dev-side only, no effect on offline production, and contingent on confirming a cloud service reading the source is acceptable for this project.

---

## 4. Workflow: Merge to `main`

Branch protection (Versioning Rules §3) gates this: all required checks green, one approval, no direct push.

Once merged:
1. CI builds the image for that repo.
2. Image is tagged `<service>:<git-sha>` (always) and scanned (Trivy).
3. Image is pushed to the registry (git-sha tag). **This is as far as CI goes automatically** — there is no automatic deploy step, because production is offline and cannot be reached from CI (Environment Profiles §3).

---

## 5. Workflow: Deploying to Staging

A human-initiated step, distinct from the automatic build-on-merge above:

1. A human picks the git-sha(s) to promote (usually the latest `main` build for the repo(s) that changed).
2. The `deploy/` compose file (in the backend repo — DevOps Planning Proposal §5.3) is updated to point at those tags, or a human-readable semver tag is applied alongside the git-sha (Versioning Rules §4).
3. `docker compose up` runs against the staging server, which mirrors production's full container set (Environment Profiles §2).
4. The golden-dataset evaluation runs against the **real vLLM** here (not the online provider used in local dev) — this is the guardrail step that must pass before anything is considered for production (Environment Profiles §4, guardrail 2).

---

## 6. Workflow: Releasing to Production

1. Images already validated in staging are exported (`docker save` tarballs or synced to a transfer registry).
2. Carried across the air-gap on approved media.
3. Loaded into the on-prem registry (Harbor) inside NBE's network.
4. A human runs `docker compose up` with the pinned versions from the updated `deploy/docker-compose.prod.yml`, keeping the previous version available for immediate rollback.
5. Post-deploy: healthcheck endpoints (`/health`) confirm readiness before the reverse proxy is considered fully cut over; this is also the point at which a scheduled backup should be freshly verified as still working against the new release.

---

## 7. Per-Repo CI Ownership Summary

| Repo | Owns | Special CI steps |
|---|---|---|
| Frontend | Build check, lint, format, tests | RTL/EN screenshot pair per PR is a manual QA step, not automated CI, but is treated as required for merge on any UI-touching PR (Architectural Guidelines §9) |
| Backend (Django) | Migrations (own tables), secret scanning, env validation, health endpoint, Swagger/OpenAPI auto-generation | Swagger generation failing (schema drift) is treated as a build failure, not a warning — see API Design Guidelines §11 |
| AI service (FastAPI) | Alembic migrations (own tables), deterministic tests (blocking), quality/eval tests (non-blocking) | Model weights mounted as a volume, never baked into the image being tested |
| `deploy/` folder (backend repo) | Full compose + prod override, named volumes, reverse proxy config, backup script, runbook | Not a CI-driven artifact — hand-edited and reviewed like any other code change, but never auto-deployed |

---

## 8. Two Open Inputs (carried over from DevOps Planning Proposal, still unresolved)

- **Does MinerU need a GPU?** Affects which physical machine it's scheduled on. To confirm with the AI team.
- **Backup schedule/retention.** Proposed as daily; needs NBE sign-off on the actual requirement before it's treated as final.
