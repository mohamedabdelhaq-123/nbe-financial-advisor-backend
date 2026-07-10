#!/bin/bash
# Provisions the AI service's Postgres isolation on first cluster init:
#
#   ai_user     — LOGIN, owner of its OWN database (ai_appdb). Read-write there,
#                 NO access to the backend's appdb. The AI service migrates and
#                 owns these tables (app/core/db.py -> own_engine).
#   ai_readonly — LOGIN, SELECT-only on appdb. Used to read backend-owned
#                 (Django) tables (app/backend_db -> read-only engine). The DB
#                 itself rejects any write.
#
# appuser (POSTGRES_USER) stays the superuser/owner of appdb (Django). After
# this runs, the AI service container holds only ai_user + ai_readonly creds —
# no superuser, and no write path to Django's data.
#
# IMPORTANT: docker-entrypoint-initdb.d scripts run ONLY on first cluster init
# (empty pgdata volume). On an existing volume, run this SQL by hand once:
#   docker compose exec -e AI_READONLY_PASSWORD=... -e AI_DB_PASSWORD=... \
#     postgres bash /docker-entrypoint-initdb.d/10-ai-roles.sh
#
# Django's tables don't exist yet at init time (the backend runs `migrate` after
# Postgres is healthy), so the appdb grants rely on ALTER DEFAULT PRIVILEGES to
# cover every table appuser creates from now on — current AND future.
set -euo pipefail

: "${AI_READONLY_PASSWORD:?AI_READONLY_PASSWORD must be set for the read-only role}"
: "${AI_DB_PASSWORD:?AI_DB_PASSWORD must be set for the ai_user (own DB) role}"

AI_DB_NAME="${AI_DB_NAME:-ai_appdb}"

# ── 1. Roles + backend read-only grants (connected to appdb) ────────────────
psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" \
     --dbname "$POSTGRES_DB" \
     --set=db="$POSTGRES_DB" \
     --set=owner="$POSTGRES_USER" \
     --set=ro_pass="$AI_READONLY_PASSWORD" \
     --set=ai_pass="$AI_DB_PASSWORD" <<-'EOSQL'
	DO $$
	BEGIN
	  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ai_readonly') THEN
	    CREATE ROLE ai_readonly LOGIN;
	  END IF;
	  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'ai_user') THEN
	    CREATE ROLE ai_user LOGIN;
	  END IF;
	END
	$$;

	-- Set/refresh passwords (safe on re-run; values come from psql vars so they
	-- are never interpolated into this file).
	ALTER ROLE ai_readonly WITH LOGIN PASSWORD :'ro_pass';
	ALTER ROLE ai_user     WITH LOGIN PASSWORD :'ai_pass';

	-- ai_readonly: read-only at the role level as defence in depth on top of the
	-- absence of any write grants.
	ALTER ROLE ai_readonly SET default_transaction_read_only = on;

	GRANT CONNECT ON DATABASE :"db" TO ai_readonly;
	GRANT USAGE ON SCHEMA public TO ai_readonly;

	-- Existing tables (typically just extensions at init time).
	GRANT SELECT ON ALL TABLES IN SCHEMA public TO ai_readonly;

	-- Every table appuser creates later (Django migrate).
	ALTER DEFAULT PRIVILEGES FOR ROLE :"owner" IN SCHEMA public
	  GRANT SELECT ON TABLES TO ai_readonly;

	-- Lock appdb down to explicitly-granted roles only. Postgres grants CONNECT
	-- to PUBLIC by default, so without this ai_user (and any future role) could
	-- still open a connection. The owner/superuser (appuser) and the explicit
	-- GRANT to ai_readonly above are unaffected.
	REVOKE CONNECT ON DATABASE :"db" FROM PUBLIC;
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

echo "AI roles ready: ai_user (owner of ${AI_DB_NAME}), ai_readonly (SELECT on ${POSTGRES_DB})"
