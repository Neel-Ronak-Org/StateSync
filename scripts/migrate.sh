#!/usr/bin/env bash
# scripts/migrate.sh
# Run all migrations in order as statesync_migrator.
# Also computes and inserts the schema fingerprint (003_fingerprint).
#
# Prerequisites:
#   - postgres container is healthy
#   - secrets/pg_migrator_password exists
#   - Python 3.12+ available locally (for fingerprint computation)
#
# Usage:
#   ./scripts/migrate.sh
# Or from inside the running postgres container:
#   docker compose exec postgres bash -c "/migrations/migrate.sh"

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
MIGRATIONS_DIR="${REPO_ROOT}/migrations"

# ── Credentials ───────────────────────────────────────────────────────────────
MIGRATOR_PASSWORD=$(cat "${REPO_ROOT}/secrets/pg_migrator_password" 2>/dev/null || \
                    echo "${PG_MIGRATOR_PASSWORD:-}")
READER_PASSWORD=$(cat "${REPO_ROOT}/secrets/pg_reader_password" 2>/dev/null || \
                  echo "${PG_READER_PASSWORD:-}")
INGEST_PASSWORD=$(cat "${REPO_ROOT}/secrets/pg_ingest_password" 2>/dev/null || \
                  echo "${PG_INGEST_PASSWORD:-changeme_ingest}")

if [[ -z "${MIGRATOR_PASSWORD}" ]]; then
  echo "ERROR: pg_migrator_password secret not found." >&2
  exit 1
fi

# ── Connection ────────────────────────────────────────────────────────────────
PG_HOST="${POSTGRES_HOST:-localhost}"
PG_PORT="${POSTGRES_PORT:-5432}"
PG_DB="${POSTGRES_DB:-statesync}"

export PGPASSWORD="${MIGRATOR_PASSWORD}"
# Detect psql: prefer homebrew libpq on macOS, fall back to PATH
if [[ -x "/opt/homebrew/opt/libpq/bin/psql" ]]; then
  PSQL_BIN="/opt/homebrew/opt/libpq/bin/psql"
elif [[ -x "/usr/local/opt/libpq/bin/psql" ]]; then
  PSQL_BIN="/usr/local/opt/libpq/bin/psql"
else
  PSQL_BIN="psql"
fi
PSQL="${PSQL_BIN} -h ${PG_HOST} -p ${PG_PORT} -U statesync_migrator -d ${PG_DB}"

echo "==> Waiting for postgres to be ready..."
USE_DOCKER_EXEC=0
for i in $(seq 1 10); do
  if ${PSQL} -c "SELECT 1" > /dev/null 2>&1; then
    echo "    postgres ready (via host psql)."
    break
  fi
  if docker compose exec -T -e PGPASSWORD="${PGPASSWORD}" postgres psql -U statesync_migrator -d statesync -c "SELECT 1" > /dev/null 2>&1; then
    echo "    postgres ready (via docker compose exec)."
    USE_DOCKER_EXEC=1
    PSQL="docker compose exec -T -e PGPASSWORD=${PGPASSWORD} postgres psql -U statesync_migrator -d statesync"
    break
  fi
  echo "    Attempt ${i}/10 — waiting..."
  sleep 1
done

# ── Migration 001: roles ───────────────────────────────────────────────────────
echo "==> Running 001_roles.sql..."
${PSQL} < "${MIGRATIONS_DIR}/001_roles.sql"

# Set passwords for reader and ingest roles
${PSQL} -c "ALTER ROLE statesync_reader PASSWORD '${READER_PASSWORD}'"
${PSQL} -c "ALTER ROLE statesync_ingest PASSWORD '${INGEST_PASSWORD}'"
echo "    Roles created and passwords set."

# ── Migration 002: schema ─────────────────────────────────────────────────────
echo "==> Running 002_schema.sql..."
${PSQL} < "${MIGRATIONS_DIR}/002_schema.sql"
echo "    Schema applied."

# ── Migration 003: fingerprint (computed + inserted) ─────────────────────────
echo "==> Computing schema fingerprint..."
if [[ ${USE_DOCKER_EXEC} -eq 1 ]]; then
  PY_EXEC="docker compose exec -T -e POSTGRES_HOST=postgres -e PGPASSWORD=${PGPASSWORD} mcp python"
else
  PY_EXEC="python3"
fi
FINGERPRINT=$(${PY_EXEC} - <<'PYEOF'
import asyncio
import os
import sys

# Minimal async fingerprint computation matching A5 formula
import hashlib
import json
import psycopg

async def compute():
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db   = os.environ.get("POSTGRES_DB", "statesync")
    pw   = os.environ.get("PGPASSWORD", "")
    dsn  = f"postgresql://statesync_migrator:{pw}@{host}:{port}/{db}"
    
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        excluded = {"schema_versions"}
        cur = await conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name"
        )
        all_tables = [r[0] for r in await cur.fetchall()]
        app_tables = [t for t in all_tables if t not in excluded]
        
        schema_rows = []
        for table in sorted(app_tables):
            cur = await conn.execute(
                "SELECT column_name, data_type, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=%s ORDER BY column_name",
                (table,)
            )
            columns = [{"column": r[0], "data_type": r[1], "is_nullable": r[2]=="YES"} for r in await cur.fetchall()]
            
            # pg_catalog, not information_schema.table_constraints -- the latter
            # only shows constraints to a role that owns the table or holds a
            # privilege other than SELECT, so it silently returns nothing for
            # statesync_reader. mcp/fingerprint.py must match this exactly
            # (same formula, any role) or the two "independent" computations
            # A5 requires can never agree.
            cur = await conn.execute(
                "SELECT att2.attname, cl.relname, att.attname "
                "FROM pg_constraint con "
                "JOIN pg_class rel ON rel.oid = con.conrelid "
                "JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace "
                "JOIN pg_class cl ON cl.oid = con.confrelid "
                "JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS ck(attnum, ord) ON true "
                "JOIN LATERAL unnest(con.confkey) WITH ORDINALITY AS cfk(attnum, ord) ON cfk.ord = ck.ord "
                "JOIN pg_attribute att2 ON att2.attrelid = con.conrelid AND att2.attnum = ck.attnum "
                "JOIN pg_attribute att ON att.attrelid = con.confrelid AND att.attnum = cfk.attnum "
                "WHERE con.contype='f' AND nsp.nspname='public' AND rel.relname=%s "
                "ORDER BY att2.attname",
                (table,)
            )
            fks = [{"fk_column": r[0], "ref_table": r[1], "ref_column": r[2]} for r in await cur.fetchall()]
            schema_rows.append({"table": table, "columns": sorted(columns, key=lambda c: c["column"]), "foreign_keys": sorted(fks, key=lambda f: f["fk_column"])})
        
        schema_rows_sorted = sorted(schema_rows, key=lambda r: r["table"])
        canonical = json.dumps(schema_rows_sorted, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

print(asyncio.run(compute()))
PYEOF
)

echo "    Fingerprint: ${FINGERPRINT}"

# Retire any existing current row before inserting/reactivating this one --
# schema_versions_one_current is a partial unique index on is_current, so
# two TRUE rows at once is a constraint violation, not just bad data. This
# also makes reverting a migration correctly reactivate the original row
# (C1-V4: the fingerprint after revert equals the original) instead of
# leaving a stale duplicate.
${PSQL} -c "
  BEGIN;
  UPDATE schema_versions SET is_current = FALSE WHERE is_current = TRUE;
  INSERT INTO schema_versions (fingerprint, computed_at, is_current)
  VALUES ('${FINGERPRINT}', now(), TRUE)
  ON CONFLICT (fingerprint) DO UPDATE SET is_current = TRUE, computed_at = now();
  COMMIT;
"
echo "    Fingerprint inserted into schema_versions (is_current)."

# ── Verify \\dt ────────────────────────────────────────────────────────────────
echo ""
echo "==> Tables in public schema:"
${PSQL} -c "\dt"

echo ""
echo "==> Migrations complete. Schema fingerprint: ${FINGERPRINT}"
