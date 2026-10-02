#!/usr/bin/env bash
# demo/fiben_db.sh
# A local Postgres holding FIBEN (IBM, Apache-2.0; docs/eval/external_datasets.md), to test
# "connect your own database" on a real 152-table public-company schema. It is NOT part of
# StateSync: a separate container on localhost:5434, with a read-only login for StateSync.
#
#   ./demo/fiben_db.sh          (re)create it and load the data (needs scripts/fetch_external_eval.sh first)
#   ./demo/fiben_db.sh stop     remove it
#
# Names are lower case (fiben.listedsecurity, haslegalname), as a native Postgres port would
# have them: the Db2 DDL is unquoted, and Postgres folds unquoted names to lower case. FIBEN's
# gold SQL quotes UPPER-case names, so lower-case its quoted identifiers before running it
# (mechanical; FIBEN."LISTEDSECURITY" -> fiben."listedsecurity"). StateSync's MCP accepts only
# lower-case table names today (^[a-z_][a-z0-9_]{0,62}$), so an upper-case copy fails at once.
# Primary keys are kept; foreign keys are not created (they only slow the load).
# Db2 timestamps (1959-03-14-00.00.00.000000) are rewritten to ISO on the way in.
# Db2 allows 128-character names, Postgres 63: the 14 longer names (none used by the gold SQL)
# become their first 54 characters + "_" + 8 hex digits of their SHA-1, so none collide.
#
# In StateSync (Library -> Databases -> Add database):
#   Host host.docker.internal   Port 5434   Database fiben   User statesync_reader
# (Demo-only values for a throwaway local database; the reader password is below.)
set -euo pipefail
NAME=statesync-fiben-db
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="${REPO_ROOT}/data/external/fiben"
PY="${PYTHON:-/opt/anaconda3/bin/python3}"
if [[ "${1:-}" == "stop" ]]; then docker rm -f "$NAME" >/dev/null 2>&1 && echo "removed $NAME"; exit 0; fi
[[ -f "${SRC}/FIBEN.sql" && -d "${SRC}/data" ]] || { echo "run scripts/fetch_external_eval.sh first" >&2; exit 1; }

docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" -p 127.0.0.1:5434:5432 \
  -e POSTGRES_DB=fiben -e POSTGRES_PASSWORD="$(openssl rand -hex 16)" \
  postgres:16-alpine >/dev/null
echo -n "waiting for Postgres"
until docker exec "$NAME" pg_isready -U postgres -d fiben >/dev/null 2>&1; do echo -n "."; sleep 1; done
sleep 2; echo

# DDL: quoted upper-case identifiers in schema fiben; CREATE TABLE statements only.
"$PY" - "${SRC}/FIBEN.sql" <<'EOF' | docker exec -i "$NAME" psql -q -v ON_ERROR_STOP=1 -U postgres -d fiben
import hashlib, re, sys
def short(n):
    return n if len(n) <= 63 else n[:54] + "_" + hashlib.sha1(n.encode()).hexdigest()[:8]
ddl = open(sys.argv[1]).read()
print("CREATE SCHEMA fiben;")
for name, body in re.findall(r"CREATE TABLE (\w+) \((.*?)\);\s*$", ddl, flags=re.M):
    cols = []
    for part in re.split(r",\s*(?![^()]*\))", body):
        part = part.strip()
        pk = re.match(r"PRIMARY KEY \((.*)\)", part)
        if pk:
            cols.append("PRIMARY KEY (" + ", ".join(short(c.strip()).lower() for c in pk.group(1).split(",")) + ")")
        else:
            col, rest = part.split(" ", 1)
            cols.append(f"{short(col).lower()} {rest}")
    print(f"CREATE TABLE fiben.{short(name).lower()} ({', '.join(cols)});")
EOF
echo "tables: $(docker exec "$NAME" psql -At -U postgres -d fiben -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='fiben'")"

# Data: one COPY per CSV (no header), Db2 timestamps rewritten.
for csv in "${SRC}"/data/*.csv; do
  table="$("$PY" -c 'import hashlib, sys; n = sys.argv[1]; print((n if len(n) <= 63 else n[:54] + "_" + hashlib.sha1(n.encode()).hexdigest()[:8]).lower())' "$(basename "$csv" .csv)")"
  "$PY" -c '
import re, sys
ts = re.compile(r"\b(\d{4}-\d{2}-\d{2})-(\d{2})\.(\d{2})\.(\d{2})(\.\d+)?\b")
for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
    sys.stdout.write(ts.sub(lambda m: m[1] + " " + m[2] + ":" + m[3] + ":" + m[4] + (m[5] or ""), line))
' "$csv" | docker exec -i "$NAME" psql -q -v ON_ERROR_STOP=1 -U postgres -d fiben \
      -c "\\copy fiben.${table} FROM STDIN WITH (FORMAT csv)"
done

# FIBEN's own foreign keys, as metadata (NOT VALID); see demo/fiben_fks.py.
"$PY" "${REPO_ROOT}/demo/fiben_fks.py" "${SRC}/FIBEN.sql" | docker exec -i "$NAME" psql -q -U postgres -d fiben
echo "foreign keys: $(docker exec "$NAME" psql -At -U postgres -d fiben -c "SELECT count(*) FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace WHERE n.nspname = 'fiben' AND c.contype = 'f'")"

docker exec -i "$NAME" psql -q -v ON_ERROR_STOP=1 -U postgres -d fiben <<'SQL'
CREATE ROLE statesync_reader LOGIN PASSWORD 'fiben-reader-pass';
ALTER ROLE statesync_reader SET default_transaction_read_only = on;
GRANT CONNECT ON DATABASE fiben TO statesync_reader;
GRANT USAGE ON SCHEMA fiben TO statesync_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA fiben TO statesync_reader;
ANALYZE;
SQL
echo "loaded: $(docker exec "$NAME" psql -At -U postgres -d fiben -c "SELECT sum(n_live_tup) FROM pg_stat_user_tables WHERE schemaname='fiben'") rows in fiben.*"
