#!/usr/bin/env bash
# demo/bird_db.sh
# BIRD Mini-Dev (PostgreSQL version; CC BY-SA 4.0; docs/eval/external_datasets.md) in a local
# Postgres, for the study in docs/research/evidence_tiers_study.md. Not part of StateSync: its own
# container on 127.0.0.1:5436 with a read-only login.
#
# The official dump puts all 11 databases' 75 tables in `public`. Official BIRD gives each question
# its own database, so after loading, each database's tables move into a schema named after its
# db_id (from minidev/MINIDEV/dev_tables.json). A question is then asked against its own schema,
# and its gold SQL runs with search_path set to that schema.
#
#   ./demo/bird_db.sh          (re)create it (needs the download in data/external/bird)
#   ./demo/bird_db.sh stop     remove it
#
# In StateSync (Library -> Databases): host.docker.internal, port 5436, database bird,
# user statesync_reader (demo-only password below), then one workspace per schema.
set -euo pipefail
NAME=statesync-bird-db
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIRD="${REPO_ROOT}/data/external/bird/minidev"
PY="${PYTHON:-/opt/anaconda3/bin/python3}"
if [[ "${1:-}" == "stop" ]]; then docker rm -f "$NAME" >/dev/null 2>&1 && echo "removed $NAME"; exit 0; fi
[[ -f "${BIRD}/MINIDEV_postgresql/BIRD_dev.sql" ]] || { echo "no BIRD download in data/external/bird" >&2; exit 1; }

docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" -p 127.0.0.1:5436:5432 \
  -e POSTGRES_DB=bird -e POSTGRES_PASSWORD="$(openssl rand -hex 16)" \
  postgres:16-alpine >/dev/null
echo -n "waiting for Postgres"
until docker exec "$NAME" pg_isready -U postgres -d bird >/dev/null 2>&1; do echo -n "."; sleep 1; done
sleep 2; echo

echo "loading the dump (about 1 GB)..."
# The dump's OWNER TO lines name the authors' role; ownership stays with postgres here.
grep -v '^ALTER TABLE .* OWNER TO ' "${BIRD}/MINIDEV_postgresql/BIRD_dev.sql" \
  | docker exec -i "$NAME" psql -q -v ON_ERROR_STOP=1 -U postgres -d bird >/dev/null

echo "splitting into one schema per database..."
"$PY" - "${BIRD}/MINIDEV/dev_tables.json" <<'PYEOF' | docker exec -i "$NAME" psql -q -v ON_ERROR_STOP=1 -U postgres -d bird
import json, sys
for db in json.load(open(sys.argv[1])):
    schema = db["db_id"]
    print(f'CREATE SCHEMA "{schema}";')
    for table in db["table_names_original"]:
        print(f'ALTER TABLE public."{table.lower()}" SET SCHEMA "{schema}";')
PYEOF

docker exec -i "$NAME" psql -q -v ON_ERROR_STOP=1 -U postgres -d bird <<'SQL'
DO $$ DECLARE n int; BEGIN
  SELECT count(*) INTO n FROM pg_tables WHERE schemaname = 'public';
  IF n <> 0 THEN RAISE EXCEPTION '% tables left in public: the split is incomplete', n; END IF;
END $$;
CREATE ROLE statesync_reader LOGIN PASSWORD 'bird-reader-pass';
ALTER ROLE statesync_reader SET default_transaction_read_only = on;
GRANT CONNECT ON DATABASE bird TO statesync_reader;
DO $$ DECLARE s text; BEGIN
  FOR s IN SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE 'pg_%' AND nspname NOT IN ('public', 'information_schema') LOOP
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO statesync_reader', s);
    EXECUTE format('GRANT SELECT ON ALL TABLES IN SCHEMA %I TO statesync_reader', s);
  END LOOP;
END $$;
ANALYZE;
SQL
docker exec "$NAME" psql -At -U postgres -d bird -c "SELECT schemaname, count(*) FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema') GROUP BY 1 ORDER BY 1"
