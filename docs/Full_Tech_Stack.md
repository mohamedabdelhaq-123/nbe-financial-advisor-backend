# Full Tech Stack
**AI-Powered Personal Financial Advisor — Graduation Project (NBE)**

The complete stack — frontend, backend, and AI — in one place. Each choice's rationale is kept alongside it, consistent with the guiding principle already applied in the Tech Stack (Django Variant) document: minimal dependency footprint — every library fills a role nothing already covers, or replaces something genuinely error-prone to hand-roll.

---

## Frontend

| Layer | Choice | Why |
|---|---|---|
| Framework | React + Vite | No SEO/deploy need, backend already exists, avoids SSR/RTL hydration risk |
| Language | TypeScript | Type-safe API contracts |
| Routing | React Router | Standard for a Vite SPA; supports `/en` `/ar` prefixes |
| Build tool | Vite | Fast dev/HMR |
| Styling | Tailwind CSS | Fast mobile-first styling, native RTL logical properties |
| Component base | DaisyUI | Plugin-based, zero-boilerplate semantic Tailwind classes (`card`, `btn`, `stat`, `badge`) |

| Server state | TanStack Query | Caching/retry for upload polling, plan fetch, chat |
| UI state | Zustand | Minimal boilerplate for the little global state actually needed |
| Forms | React Hook Form + Zod | Efficient re-renders; one schema drives both validation and types |
| Chat structured output | Tool UI (on top of assistant-ui) | Ready-made chat runtime/streaming with isolated structured-output rendering |
| Icons | Lucide | Broad financial/utility icon coverage; only used icons ship to the bundle |
| Package manager | pnpm | Fast installs |
| Lint/format | ESLint + Prettier + Husky | No formatting noise in review |

---

## Backend (Django)

| Layer | Choice | Why |
|---|---|---|
| Web framework | Django | ORM, migrations, admin panel (used as-is for the Administration domain), auth scaffolding |
| API layer | Django REST Framework (DRF) | Serialization, validation, pagination, viewsets — covers what a second validation library would otherwise duplicate |
| DB driver | psycopg (psycopg3) | PostgreSQL driver |
| Vector adapter | pgvector-python | Django/psycopg adapter for the `vector` column type and similarity queries |
| Background jobs | Celery | Required since OCR/LLM/embedding calls run async (`202` + polling per API Design Guidelines §9) |
| Broker/cache | Redis | Celery broker/result backend; can double as a rate-limit backend later if needed |
| File storage adapter | django-storages + boto3 | S3-compatible backend pointed at SeaweedFS's Filer gateway; avoids a custom storage backend |

| Testing | pytest + pytest-django | Bridges Django's test client/fixtures into pytest |
| Test data | factory_boy | Avoids hand-writing repetitive fixture JSON across the many interrelated tables |
| E2E testing | Playwright | Framework-agnostic |
| API docs | drf-spectacular (+ django-rest-swagger for the UI) | Generated from DRF serializers/viewsets — never hand-maintained separately (API Design Guidelines §11) |
| Auth | djangorestframework-simplejwt | JWT auth integrated with DRF; lighter than a full social-login package for token-only API auth |


---

## AI Service (FastAPI)

| Layer | Choice | Why |
|---|---|---|
| API framework | FastAPI | Stateless, internal-only processing API — never called by the frontend directly |
| LLM inference | Ollama (local dev) / Groq free API or OpenAI-compatible provider (demo/dev) | Dev-time substitution for vLLM, avoiding a GPU requirement in local development (Environment Profiles §1) |
| LLM model | Llama 3.1 8B or Qwen 2.5 14B | Self-hosted model family for vLLM-served environments |
| Model serving (staging/prod) | vLLM | GPU-served, own container, pinned CUDA/driver versions |
| Embeddings | nomic-embed-text via Ollama (dev) | Local, offline-capable embedding generation |
| Agent framework | LangChain + LangGraph | Maestro orchestration + sub-agent delegation |
| OCR/extraction | MinerU | Deterministic tool, called by the AI service only (not Django — see Services and Background Tasks §1) |
| Migrations | Alembic | AI service owns and migrates only its own tables in the shared Postgres instance |
| Observability | Langfuse OSS (self-hosted) | Matches the offline-production requirement — no external SaaS tracing dependency |
| Testing | Deterministic tests (blocking) + golden-dataset quality/eval tests (non-blocking) | Because AI output is non-deterministic — exact-match testing would fail randomly (DevOps Planning Proposal §5.7) |

---

## Shared Infrastructure

| Layer | Choice | Why |
|---|---|---|
| Primary database | PostgreSQL + pgvector | One instance, shared by Django and the AI service via separate tables/migration tools |
| File storage | SeaweedFS | Raw PDFs, MinerU artifacts, budget reference templates — reached via its S3-compatible Filer gateway |
| Reverse proxy | Nginx | Single public entry point; HTTPS termination (NBE-issued internal CA cert) |
| Orchestration | Docker Compose (not Kubernetes) | Right-sized for one offline, on-prem machine maintained by a small team |
| Monitoring (production) | Prometheus + Grafana + Loki (+ GPU exporter) | Fully offline/self-hosted observability stack |
| Secret scanning | TruffleHog | Catches accidentally committed credentials — critical for a bank-adjacent project |
| PR review automation | CodeRabbit (pending NBE approval) | Auto PR descriptions/review; dev-side only, no effect on offline production |

