#!/bin/bash
# Provisions the mock-bank-sync service's OWN database on first cluster init:
#
#   mock_bank_user — LOGIN, owner of its OWN database (mock_bank_db).
#                     Read-write there, NO access to the backend's appdb.
#                     mock-bank-sync migrates and owns these tables
#                     (app/db.py -> engine).
#
# appuser (POSTGRES_USER) stays the superuser/owner of appdb (Django).
#
# Mirrors deploy/initdb/10-ai-roles.sh's structure/conventions exactly —
# see that file for the fuller rationale (schema ownership on PG15+,
# REVOKE CONNECT FROM PUBLIC, etc).
#
# IMPORTANT: docker-entrypoint-initdb.d scripts run ONLY on first cluster
# init (empty pgdata volume). On an existing volume, run this SQL by hand once:
#   docker compose exec -e MOCK_BANK_DB_PASSWORD=... \
#     postgres bash /docker-entrypoint-initdb.d/20-mock-bank-roles.sh
set -euo pipefail

: "${MOCK_BANK_DB_PASSWORD:?MOCK_BANK_DB_PASSWORD must be set for the mock_bank_user (own DB) role}"

MOCK_BANK_DB_NAME="${MOCK_BANK_DB_NAME:-mock_bank_db}"

# ── 1. mock_bank_user role (connected to appdb, but this role only ever
#      touches its own database, created below) ────────────────────────────
psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" \
     --dbname "$POSTGRES_DB" \
     --set=mock_bank_pass="$MOCK_BANK_DB_PASSWORD" <<-'EOSQL'
	DO $$
	BEGIN
	  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'mock_bank_user') THEN
	    CREATE ROLE mock_bank_user LOGIN;
	  END IF;
	END
	$$;

	-- Set/refresh password (safe on re-run; value comes from a psql var so it
	-- is never interpolated into this file).
	ALTER ROLE mock_bank_user WITH LOGIN PASSWORD :'mock_bank_pass';
EOSQL

# ── 2. The mock-bank-sync service's own database (CREATE DATABASE can't run
#      in a txn) ─────────────────────────────────────────────────────────────
if ! psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
        -tAc "SELECT 1 FROM pg_database WHERE datname = '${MOCK_BANK_DB_NAME}'" | grep -q 1; then
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
       -c "CREATE DATABASE \"${MOCK_BANK_DB_NAME}\" OWNER mock_bank_user"
fi

# ── 3. Inside mock_bank_db: let mock_bank_user create tables ────────────────
psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" \
     --dbname "$MOCK_BANK_DB_NAME" \
     --set=mock_bank_db="$MOCK_BANK_DB_NAME" <<-'EOSQL'
	-- On PG15+ the DB owner is NOT automatically the public-schema owner, so
	-- alembic (running as mock_bank_user) could not create tables without this.
	ALTER SCHEMA public OWNER TO mock_bank_user;

	-- Only mock_bank_user (owner) and the superuser should reach this database.
	REVOKE CONNECT ON DATABASE :"mock_bank_db" FROM PUBLIC;
EOSQL

echo "mock_bank_user ready: owner of ${MOCK_BANK_DB_NAME}"
