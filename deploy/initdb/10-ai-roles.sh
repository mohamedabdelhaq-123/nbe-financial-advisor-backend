#!/bin/bash
# Provisions the AI service's OWN database on first cluster init:
#
#   ai_user     — LOGIN, owner of its OWN database (ai_appdb). Read-write there,
#                 NO access to the backend's appdb. The AI service migrates and
#                 owns these tables (app/core/db.py -> own_engine).
#
# appuser (POSTGRES_USER) stays the superuser/owner of appdb (Django).
#
# ai_readonly (the role the AI service uses to read, and narrowly write,
# backend-owned appdb tables) is NOT provisioned here anymore — it's created
# and granted by Django migration core/migrations/0009_grant_ai_readonly_role.py
# instead. That role only ever needs to exist on appdb, which Django owns and
# migrates, and docker-entrypoint-initdb.d scripts run ONCE on first cluster
# init, before appdb's tables exist — too early for the column/table-level
# grants that role now needs on transactions/monthly_summaries.
#
# IMPORTANT: docker-entrypoint-initdb.d scripts run ONLY on first cluster init
# (empty pgdata volume). On an existing volume, run this SQL by hand once:
#   docker compose exec -e AI_DB_PASSWORD=... \
#     postgres bash /docker-entrypoint-initdb.d/10-ai-roles.sh
set -euo pipefail

: "${AI_DB_PASSWORD:?AI_DB_PASSWORD must be set for the ai_user (own DB) role}"

AI_DB_NAME="${AI_DB_NAME:-ai_appdb}"

# ── 1. ai_user role (connected to appdb, but this role only ever touches
#      its own database, created below) ─────────────────────────────────────
psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" \
     --dbname "$POSTGRES_DB" \
     --set=ai_pass="$AI_DB_PASSWORD" <<-'EOSQL'
	DO $$
	BEGIN
	  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ai_user') THEN
	    CREATE ROLE ai_user LOGIN;
	  END IF;
	END
	$$;

	-- Set/refresh password (safe on re-run; value comes from a psql var so it
	-- is never interpolated into this file).
	ALTER ROLE ai_user WITH LOGIN PASSWORD :'ai_pass';
EOSQL

# ── 2. The AI service's own database (CREATE DATABASE can't run in a txn) ────
if ! psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
        -tAc "SELECT 1 FROM pg_database WHERE datname = '${AI_DB_NAME}'" | grep -q 1; then
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
       -c "CREATE DATABASE \"${AI_DB_NAME}\" OWNER ai_user"
fi

# ── 3. Inside ai_appdb: let ai_user create tables + pre-create pgvector ──────
psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" \
     --dbname "$AI_DB_NAME" \
     --set=ai_db="$AI_DB_NAME" <<-'EOSQL'
	-- On PG15+ the DB owner is NOT automatically the public-schema owner, so
	-- alembic (running as ai_user) could not create tables without this.
	ALTER SCHEMA public OWNER TO ai_user;

	-- pgvector is not a "trusted" extension, so only a superuser can install it.
	-- Do it once here so ai_user can add VectorField columns later.
	CREATE EXTENSION IF NOT EXISTS vector;

	-- Only ai_user (owner) and the superuser should reach this database.
	REVOKE CONNECT ON DATABASE :"ai_db" FROM PUBLIC;
EOSQL

echo "ai_user ready: owner of ${AI_DB_NAME} (ai_readonly is provisioned separately by a Django migration)"
