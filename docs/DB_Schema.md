-- ============================================================================
-- Database Schema
-- AI-Powered Personal Financial Advisor — Graduation Project (NBE)
-- Target: PostgreSQL 15+ with pgvector extension
-- Usage: import directly into dbdiagram.io (Import Database > PostgreSQL)
--
-- Domain grouping mirrors the Data Governance Specs document:
--   1. Profile        2. Statements      3. Conversations   4. Budgets
--   5. Feedback       6. Recommendation  7. Aggregations     8. Administration
--
-- Notes:
--   - Django owns every table below except the "AI-service-owned" ones marked
--     explicitly (problem_statements embeddings, recommendation matching
--     internals) — see System Architecture §3 on migration ownership.
--   - All primary keys are UUID, generated with gen_random_uuid() (pgcrypto).
--   - "transactions" is the single source of truth for monetary data — see
--     the UNIQUE constraint enforcing duplicate prevention at the DB level.
--   - "budgets" is 1:1 with users — enforced via UNIQUE on user_id.
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;

-- ============================================================================
-- 1. PROFILE DOMAIN
-- ============================================================================

CREATE TABLE users (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                VARCHAR(255) NOT NULL,
    email               VARCHAR(255) NOT NULL UNIQUE,
    password_hash       VARCHAR(255) NOT NULL,
    phone               VARCHAR(50),
    employment_status   VARCHAR(50),                 -- e.g. salaried, freelance, business_owner
    income_bracket      VARCHAR(50),                  -- e.g. "15000-25000"
    monthly_income       NUMERIC(14,2),
    income_steadiness    VARCHAR(20),                  -- steady | variable
    dependents_count     SMALLINT DEFAULT 0,
    onboarding_date      TIMESTAMPTZ,
    status              VARCHAR(20) NOT NULL DEFAULT 'active',   -- active | suspended | deleted
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE bank_accounts (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                 UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    bank_name               VARCHAR(255) NOT NULL,
    account_type            VARCHAR(50),               -- checking | savings | credit_card
    masked_account_number   VARCHAR(50) NOT NULL,
    currency                VARCHAR(10) NOT NULL DEFAULT 'EGP',
    is_active               BOOLEAN NOT NULL DEFAULT true,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE consent_records (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    consent_type        VARCHAR(50) NOT NULL,          -- data_processing | marketing | ...
    policy_version      VARCHAR(20) NOT NULL,
    granted_at          TIMESTAMPTZ,
    revoked_at          TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
    -- append-only: rows are never updated/deleted, only inserted (grant or revoke event)
);

CREATE TABLE user_preferences (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                     UUID NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    language                    VARCHAR(10) NOT NULL DEFAULT 'en',   -- en | ar
    currency_display_format     VARCHAR(20) NOT NULL DEFAULT 'symbol',
    date_format                 VARCHAR(20) NOT NULL DEFAULT 'DD/MM/YYYY',
    budget_cycle_start_day      SMALLINT NOT NULL DEFAULT 1,
    default_view                VARCHAR(20) NOT NULL DEFAULT 'monthly',
    retain_raw_documents        BOOLEAN NOT NULL DEFAULT false,
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- 2. STATEMENTS DOMAIN
-- ============================================================================

CREATE TABLE bank_statement_templates (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bank_name           VARCHAR(255) NOT NULL,
    layout_signature    VARCHAR(255) NOT NULL,          -- fingerprint used to match a statement to this template
    column_mapping_json JSONB NOT NULL,
    date_format         VARCHAR(20),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (bank_name, layout_signature)
);

CREATE TABLE statement_files (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    account_id                  UUID REFERENCES bank_accounts(id) ON DELETE SET NULL,
    template_id                 UUID REFERENCES bank_statement_templates(id) ON DELETE SET NULL,
    seaweed_file_id              VARCHAR(255) NOT NULL,   -- raw file location
    checksum                    VARCHAR(64) NOT NULL,     -- file-level duplicate-upload check
    file_size                    BIGINT,                   -- raw file size in bytes, captured at upload (null for seed rows)
    file_type                    VARCHAR(20),               -- file extension: pdf | jpg | png
    status                      VARCHAR(30) NOT NULL DEFAULT 'extraction',
                                 -- extraction|normalization|approval|processed
                                 -- names the phase the statement is currently at/working toward; no
                                 -- "pending_" prefix — is_processing already says whether that phase
                                 -- is actively running, so baking "pending" into the name too would
                                 -- just say the same thing twice. A row only ever exists once its
                                 -- file is stored, so there is no record_created/stored/failed
                                 -- status (docs/API_GUIDE/Data_Shapes_Statements.md)
    failure_reason               TEXT,                     -- why the current phase last failed, if it did
    failed_phase                 VARCHAR(20),               -- extraction|normalization|null
    is_processing                BOOLEAN NOT NULL DEFAULT false, -- true only while a phase runner is actively executing
    start_transaction_date       DATE,
    last_transaction_date        DATE,
    upload_date                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, checksum)
);

CREATE TABLE statement_ocr_results (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    statement_id        UUID NOT NULL REFERENCES statement_files(id) ON DELETE CASCADE,
    seaweed_file_id      VARCHAR(255) NOT NULL,   -- artifacts folder: content.json, markdown, images
    ocr_engine          VARCHAR(50) NOT NULL DEFAULT 'MinerU',
    confidence_score     NUMERIC(4,3),
    processed_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE statement_normalized (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    statement_id        UUID NOT NULL REFERENCES statement_files(id) ON DELETE CASCADE,
    normalized_json      JSONB NOT NULL,
    model_used           VARCHAR(100),
    adjusted_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- 3. CONVERSATIONS DOMAIN (LLM Sessions)
-- ============================================================================

CREATE TABLE conversations (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_message_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    status              VARCHAR(20) NOT NULL DEFAULT 'active'   -- active | closed
);

CREATE TABLE messages (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id      UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    sender              VARCHAR(20) NOT NULL,        -- user | assistant
    content             TEXT NOT NULL,
    stage               VARCHAR(30) NOT NULL DEFAULT 'general',
                        -- general | extraction_review | budget_review | categorisation_review | planning | analysis
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE message_references (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id          UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    target_type         VARCHAR(50) NOT NULL,   -- transaction | budget | anomaly | recommendation | statement | ...
    target_id           UUID NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- 4. BUDGETS DOMAIN
-- ============================================================================

-- One row per user: the single, current, active plan (1:1 with users).
CREATE TABLE budgets (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                 UUID NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    name                    VARCHAR(255) NOT NULL DEFAULT 'My Plan',
    period_type             VARCHAR(20) NOT NULL DEFAULT 'monthly',
    status                  VARCHAR(20) NOT NULL DEFAULT 'active',
    selected_template_key    VARCHAR(50),             -- which onboarding template this started from, if any
    savings_goal_name        VARCHAR(255),
    goal_target_amount       NUMERIC(14,2),
    goal_timeline_months     INTEGER,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE budget_allocations (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    budget_id               UUID NOT NULL REFERENCES budgets(id) ON DELETE CASCADE,
    category                VARCHAR(100) NOT NULL,
    allocated_percentage     NUMERIC(5,2) NOT NULL,     -- must sum to 100 across a budget_id (app-layer validation)
    allocated_amount         NUMERIC(14,2) NOT NULL,     -- derived: monthly_income * allocated_percentage
    currency                VARCHAR(10) NOT NULL DEFAULT 'EGP',
    UNIQUE (budget_id, category)
);

-- Append-only version log; snapshot written before every replace of `budgets`.
CREATE TABLE budget_history (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    budget_id           UUID NOT NULL REFERENCES budgets(id) ON DELETE CASCADE,
    previous_values      JSONB NOT NULL,     -- full snapshot: allocations + goal fields at time of change
    changed_via          VARCHAR(20) NOT NULL DEFAULT 'dashboard',  -- dashboard | chat_hitl | onboarding
    changed_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- 5. FEEDBACK DOMAIN
-- ============================================================================

CREATE TABLE reactions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    target_type         VARCHAR(50) NOT NULL,   -- transaction | recommendation | message | budget
    target_id           UUID NOT NULL,
    rating              SMALLINT CHECK (rating BETWEEN 1 AND 5),
    comment             TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE reported_issues (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    description         TEXT NOT NULL,
    status              VARCHAR(20) NOT NULL DEFAULT 'open',   -- open | in_review | resolved | dismissed
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at         TIMESTAMPTZ
);

-- ============================================================================
-- 6. RECOMMENDATION DOMAIN
-- ============================================================================

CREATE TABLE products (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title               VARCHAR(255) NOT NULL,
    description         TEXT,
    categories          TEXT[],                 -- e.g. {savings, investment}
    tags                TEXT[],
    features            JSONB,                  -- flat, display-ready structure
    external_link        VARCHAR(500),
    is_active            BOOLEAN NOT NULL DEFAULT true,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- AI-service-owned (Alembic-managed) — embeddings for RAG matching.
CREATE TABLE problem_statements (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id          UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    statement_text       TEXT NOT NULL,
    embedding           vector(1024)
);

CREATE TABLE recommendation_logs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    product_id          UUID NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    matched_query        TEXT,
    similarity_score     NUMERIC(5,4),
    shown_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- 7. AGGREGATIONS DOMAIN
-- ============================================================================

-- Single source of truth for every monetary fact in the system.
CREATE TABLE transactions (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                 UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    account_id              UUID NOT NULL REFERENCES bank_accounts(id) ON DELETE CASCADE,
    statement_id            UUID REFERENCES statement_files(id) ON DELETE SET NULL,   -- null for manual entries
    transaction_date         DATE NOT NULL,
    merchant_raw             VARCHAR(500),
    merchant_normalized       VARCHAR(255),
    category                VARCHAR(100),
    amount                  NUMERIC(14,2) NOT NULL,
    currency                VARCHAR(10) NOT NULL DEFAULT 'EGP',
    is_recurring             BOOLEAN NOT NULL DEFAULT false,
    confidence_score          NUMERIC(4,3),
    source                  VARCHAR(20) NOT NULL DEFAULT 'statement',  -- statement | manual | chat
    balance                 NUMERIC(14,2),           -- running account balance, if derivable
    transaction_type          VARCHAR(20),             -- debit | credit | fee | transfer
    extra_fields             JSONB,                   -- catch-all for bank-specific data
    embedding               vector(1536),
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Duplicate-prevention guardrail (System Architecture §8 / Functional Req. — no dup transactions
    -- from the same bank), enforced regardless of origin (import or manual entry):
    UNIQUE (user_id, account_id, transaction_date, amount, merchant_raw)
);

CREATE TABLE monthly_summaries (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                 UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    account_id              UUID REFERENCES bank_accounts(id) ON DELETE CASCADE,   -- null = all accounts combined
    month                   DATE NOT NULL,               -- first day of month
    total_spend              NUMERIC(14,2),
    total_inflow              NUMERIC(14,2),
    category_breakdown_json   JSONB,
    top_merchants_json        JSONB,
    embedding               vector(1536),
    UNIQUE (user_id, account_id, month)
);

CREATE TABLE recurring_charges (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    account_id          UUID REFERENCES bank_accounts(id) ON DELETE CASCADE,
    merchant_normalized   VARCHAR(255) NOT NULL,
    frequency           VARCHAR(20) NOT NULL,         -- monthly | weekly | yearly
    avg_amount           NUMERIC(14,2),
    last_occurrence_date  DATE,
    next_expected_date    DATE
);

CREATE TABLE anomaly_flags (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    transaction_id       UUID NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    reason              TEXT NOT NULL,
    severity            VARCHAR(10) NOT NULL,          -- low | medium | high
    resolved            BOOLEAN NOT NULL DEFAULT false,
    detected_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Extensible, typed insight structure (no new table per insight type).
CREATE TABLE spending_pattern_insights (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    insight_type         VARCHAR(50) NOT NULL,
                        -- income_stability | savings_rate_trend | category_overspend | overdraft_risk |
                        -- cash_flow | merchant_frequency | time_of_month_pattern | category_volatility |
                        -- debt_service_ratio_proxy
    period              VARCHAR(20),                 -- e.g. "2026-07"
    value_json           JSONB NOT NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE net_worth_snapshots (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    as_of_date                   DATE NOT NULL,
    total_across_accounts         NUMERIC(14,2) NOT NULL,
    per_account_breakdown_json     JSONB,
    UNIQUE (user_id, as_of_date)
);

-- ============================================================================
-- 8. ADMINISTRATION DOMAIN
-- ============================================================================

-- Internal staff accounts only — never on the same auth surface as `users`.
CREATE TABLE admin_users (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                VARCHAR(255) NOT NULL,
    email               VARCHAR(255) NOT NULL UNIQUE,
    password_hash       VARCHAR(255) NOT NULL,
    role                VARCHAR(50) NOT NULL DEFAULT 'reviewer',  -- reviewer | super_admin
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- INDEXES (beyond those implied by UNIQUE constraints above)
-- ============================================================================

CREATE INDEX idx_transactions_user_date        ON transactions (user_id, transaction_date);
CREATE INDEX idx_transactions_user_category     ON transactions (user_id, category);
CREATE INDEX idx_transactions_account           ON transactions (account_id);
CREATE INDEX idx_messages_conversation          ON messages (conversation_id, created_at);
CREATE INDEX idx_message_references_target      ON message_references (target_type, target_id);
CREATE INDEX idx_reactions_target               ON reactions (target_type, target_id);
CREATE INDEX idx_anomaly_flags_severity          ON anomaly_flags (severity, resolved);
CREATE INDEX idx_spending_insights_user_type     ON spending_pattern_insights (user_id, insight_type);
CREATE INDEX idx_recommendation_logs_user        ON recommendation_logs (user_id, shown_at);
CREATE INDEX idx_statement_files_user_status     ON statement_files (user_id, status);

-- Vector similarity indexes (HNSW) for RAG lookups
CREATE INDEX idx_transactions_embedding    ON transactions USING hnsw (embedding vector_cosine_ops);
CREATE INDEX idx_monthly_summaries_embedding ON monthly_summaries USING hnsw (embedding vector_cosine_ops);
CREATE INDEX idx_problem_statements_embedding ON problem_statements USING hnsw (embedding vector_cosine_ops);
