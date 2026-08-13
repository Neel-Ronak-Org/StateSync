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
PSQL="psql -h ${PG_HOST} -p ${PG_PORT} -U statesync_migrator -d ${PG_DB}"

echo "==> Waiting for postgres to be ready..."
for i in $(seq 1 30); do
  if ${PSQL} -c "SELECT 1" > /dev/null 2>&1; then
    echo "    postgres ready."
    break
  fi
  echo "    Attempt ${i}/30 — waiting..."
  sleep 2
done

# ── Migration 001: roles ───────────────────────────────────────────────────────
echo "==> Running 001_roles.sql..."
${PSQL} -f "${MIGRATIONS_DIR}/001_roles.sql"

# Set passwords for reader and ingest roles
${PSQL} -c "ALTER ROLE statesync_reader PASSWORD '${READER_PASSWORD}'"
${PSQL} -c "ALTER ROLE statesync_ingest PASSWORD '${INGEST_PASSWORD}'"
echo "    Roles created and passwords set."

# ── Migration 002: schema ─────────────────────────────────────────────────────
echo "==> Running 002_schema.sql..."
${PSQL} -f "${MIGRATIONS_DIR}/002_schema.sql"
echo "    Schema applied."

# ── Migration 003: fingerprint (computed + inserted) ─────────────────────────
echo "==> Computing schema fingerprint..."
FINGERPRINT=$(python3 - <<'PYEOF'
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
            
            cur = await conn.execute(
                "SELECT kcu.column_name, ccu.table_name, ccu.column_name "
                "FROM information_schema.table_constraints tc "
                "JOIN information_schema.key_column_usage kcu ON tc.constraint_name=kcu.constraint_name AND tc.table_schema=kcu.table_schema "
                "JOIN information_schema.constraint_column_usage ccu ON ccu.constraint_name=tc.constraint_name AND ccu.table_schema=tc.table_schema "
                "WHERE tc.constraint_type='FOREIGN KEY' AND tc.table_schema='public' AND tc.table_name=%s ORDER BY kcu.column_name",
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

${PSQL} -c "
  INSERT INTO schema_versions (fingerprint, computed_at, is_current)
  VALUES ('${FINGERPRINT}', now(), TRUE)
  ON CONFLICT (fingerprint) DO NOTHING;
"
echo "    Fingerprint inserted into schema_versions."

# ── Verify \\dt ────────────────────────────────────────────────────────────────
echo ""
echo "==> Tables in public schema:"
${PSQL} -c "\dt"

echo ""
echo "==> Migrations complete. Schema fingerprint: ${FINGERPRINT}"
