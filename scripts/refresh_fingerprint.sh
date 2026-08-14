#!/usr/bin/env bash
# scripts/refresh_fingerprint.sh
# Recompute the A5 schema fingerprint against the live database, retire the
# current schema_versions row and insert/reactivate the new one, then
# restart mcp so its startup check verifies against the fresh value.
#
# Same formula as scripts/migrate.sh's migration-time computation and
# mcp/fingerprint.py's startup check -- this script exists so a schema
# change made outside a full `migrate.sh` run (e.g. C1-V4's rename/revert
# test) doesn't require hand-copying that Python block again.
#
# Usage:
#   ./scripts/refresh_fingerprint.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

MIGRATOR_PASSWORD=$(cat "${REPO_ROOT}/secrets/pg_migrator_password")

echo "==> Computing schema fingerprint..."
FINGERPRINT=$(docker compose run --rm --no-deps -e POSTGRES_HOST=postgres \
  -e PGPASSWORD="${MIGRATOR_PASSWORD}" mcp python - <<'PYEOF' 2>/dev/null
import asyncio, os, hashlib, json
import psycopg

async def compute():
    host = os.environ.get("POSTGRES_HOST", "localhost")
    pw   = os.environ.get("PGPASSWORD", "")
    dsn  = f"postgresql://statesync_migrator:{pw}@{host}:5432/statesync"
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        excluded = {"schema_versions"}
        cur = await conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name"
        )
        app_tables = [r[0] for r in await cur.fetchall() if r[0] not in excluded]
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
        print(hashlib.sha256(canonical.encode("utf-8")).hexdigest())

asyncio.run(compute())
PYEOF
)
echo "    Fingerprint: ${FINGERPRINT}"

docker compose exec -T -e PGPASSWORD="${MIGRATOR_PASSWORD}" postgres psql -U statesync_migrator -d statesync -c "
  BEGIN;
  UPDATE schema_versions SET is_current = FALSE WHERE is_current = TRUE;
  INSERT INTO schema_versions (fingerprint, computed_at, is_current)
  VALUES ('${FINGERPRINT}', now(), TRUE)
  ON CONFLICT (fingerprint) DO UPDATE SET is_current = TRUE, computed_at = now();
  COMMIT;
" > /dev/null
echo "    schema_versions updated (is_current)."

echo "==> Restarting mcp..."
# up -d (not restart): restart cycles the existing container's process
# without picking up a newly built image; up -d recreates it from whatever
# image is currently tagged, which is what a fingerprint refresh after a
# code change (not just a schema change) actually needs.
docker compose up -d mcp > /dev/null 2>&1

for i in $(seq 1 15); do
  STATUS=$(docker compose ps mcp --format json 2>/dev/null | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('Health','?'))" 2>/dev/null || echo "?")
  if [[ "${STATUS}" == "healthy" ]]; then
    echo "    mcp healthy. Fingerprint verified at startup: ${FINGERPRINT}"
    echo "${FINGERPRINT}"
    exit 0
  fi
  sleep 2
done
echo "ERROR: mcp did not become healthy -- check: docker compose logs mcp" >&2
exit 1
