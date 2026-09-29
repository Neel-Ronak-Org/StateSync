#!/usr/bin/env python3
"""
scripts/verify_connections.py
Live checks for the owner's own Postgres (docs/V3_PLAN.md Phase 3c slice 6,
migration 026, mcp/connections.py). Asks no model anything.

It starts a THROWAWAY Postgres container (statesync-extdb-verify) on the app
network with three logins -- a superuser, one that can write, and a read-only
one -- and proves, through the real API and the MCP:
- refusals: superuser, a login that can change data, a wrong password,
  StateSync's own database, loopback, an unknown host;
- the read-only login is saved (password never returned), its only readable
  schema chosen; another principal can neither test it nor build on it;
- a workspace on it scans the tables; queries on it read its tables (qualified
  or not) and nothing else: another schema, the system catalog, a write, a
  connection name in SQL for our own database, another tenant -- all refused;
- a connection used by a workspace cannot be deleted; after the workspace, it can.
Everything it creates is removed, the container included.

Needs: the stack running, migration 026 applied, secrets/connection_key,
secrets/api_token_dev and secrets/api_token_second.

Usage: python3 scripts/verify_connections.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from api_auth import api_token  # noqa: E402

BASE = "http://localhost:8090"
TOKEN_A = api_token()
TOKEN_B = (REPO_ROOT / "secrets" / "api_token_second").read_text().strip()
ORCHESTRATOR = "statesync-orchestrator-1"
CONTAINER = "statesync-extdb-verify"
ALIAS = "extdb-verify"
NETWORK = "statesync_app_net"
IMAGE = "postgres:16-alpine"
READY_WAIT_S = 60

SEED = """
CREATE SCHEMA shop;
CREATE TABLE shop.customers (customer_id int PRIMARY KEY, city text);
CREATE TABLE shop.orders (order_id int PRIMARY KEY, customer_id int REFERENCES shop.customers, amount numeric(10,2),
                          status text);
INSERT INTO shop.customers SELECT g, (ARRAY['Pune','Mumbai','Delhi'])[1 + g % 3] FROM generate_series(1, 30) g;
INSERT INTO shop.orders SELECT g, 1 + g % 30, (g * 37 % 500) + 0.5,
       (ARRAY['delivered','cancelled','delivered','shipped'])[1 + g % 4] FROM generate_series(1, 400) g;
CREATE ROLE analyst LOGIN PASSWORD 'analyst-verify-only';
GRANT USAGE ON SCHEMA shop TO analyst; GRANT SELECT ON ALL TABLES IN SCHEMA shop TO analyst;
CREATE ROLE writer LOGIN PASSWORD 'writer-verify-only';
GRANT USAGE ON SCHEMA shop TO writer; GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA shop TO writer;
"""

results: list[tuple[bool, str]] = []


def check(ok, label: str, detail: str = "") -> bool:
    ok = bool(ok)
    results.append((ok, label))
    print(f"  {'✓ PASS' if ok else '✗ FAIL'}: {label}" + (f" — {detail}" if detail and not ok else ""))
    return ok


def call(method: str, path: str, body: dict | None = None, token: str = TOKEN_A):
    req = urllib.request.Request(f"{BASE}{path}", data=json.dumps(body).encode() if body is not None else None,
                                 method=method, headers={"Authorization": f"Bearer {token}",
                                                         "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return e.code, {"raw": raw.decode(errors="replace")}


_MCP_SNIPPET = """
import json, sys, urllib.request
req = json.load(sys.stdin)
body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": req["method"], "params": req["params"]}).encode()
r = urllib.request.Request("http://mcp:8080/rpc", data=body, method="POST",
                           headers={"Content-Type": "application/json", "X-Tenant-ID": req["tenant"]})
print(urllib.request.urlopen(r, timeout=60).read().decode())
"""


def mcp(tenant: str, method: str, params: dict) -> dict:
    out = subprocess.run(["docker", "exec", "-i", ORCHESTRATOR, "python", "-c", _MCP_SNIPPET],
                         input=json.dumps({"tenant": tenant, "method": method, "params": params}),
                         capture_output=True, text=True)
    if out.returncode:
        raise SystemExit(f"FATAL: MCP call failed: {out.stderr.strip()}")
    return json.loads(out.stdout).get("result") or {}


def psql(sql: str) -> str:
    out = subprocess.run(["docker", "exec", "-i", "postgres", "psql", "-U", "statesync_migrator", "-d", "statesync",
                          "-qtA"], input=sql, capture_output=True, text=True)
    return out.stdout.strip()


def start_database() -> None:
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
    subprocess.run(["docker", "run", "-d", "--name", CONTAINER, "--network", NETWORK, "--network-alias", ALIAS,
                    "-e", "POSTGRES_PASSWORD=super-verify-only", IMAGE], check=True, capture_output=True)
    deadline = time.monotonic() + READY_WAIT_S
    while time.monotonic() < deadline:
        ready = subprocess.run(["docker", "exec", CONTAINER, "pg_isready", "-U", "postgres"], capture_output=True)
        if ready.returncode == 0:
            break
        time.sleep(1)
    time.sleep(2)   # the image restarts once after initdb
    subprocess.run(["docker", "exec", "-i", CONTAINER, "psql", "-q", "-v", "ON_ERROR_STOP=1", "-U", "postgres"],
                   input=SEED, check=True, capture_output=True, text=True)


def main() -> int:
    print("Own Postgres (connections)")
    start_database()
    connection_id = workspace_id = None
    try:
        base = {"host": ALIAS, "port": 5432, "dbname": "postgres", "sslmode": "disable"}
        for label, extra, expect in [
            ("a superuser", {"username": "postgres", "password": "super-verify-only"}, "superuser"),
            ("a login that can write", {"username": "writer", "password": "writer-verify-only"}, "change data"),
            ("a wrong password", {"username": "analyst", "password": "nope"}, "Could not connect"),
            ("StateSync's own database", {"host": "postgres", "username": "x", "password": "x"}, "StateSync"),
            ("loopback", {"host": "127.0.0.1", "username": "x", "password": "x"}, "local-only"),
            ("an unknown host", {"host": "no-such-host.invalid", "username": "x", "password": "x"}, "not be found"),
        ]:
            status, body = call("POST", "/library/connections", {**base, **extra, "name": f"verify {label}"})
            check(status == 422 and expect in body.get("detail", ""), f"{label} is refused", f"{status} {body}")

        status, body = call("POST", "/library/connections",
                            {**base, "name": "verify shop", "username": "analyst", "password": "analyst-verify-only"})
        if not check(status == 201, "a read-only login is saved", f"{status} {body}"):
            return 1
        connection = body["connection"]
        connection_id = connection["connection_id"]
        check(connection["schema_name"] == "shop" and body["schemas"] == ["shop"],
              "its only readable schema is chosen", str(body))
        check("analyst-verify-only" not in json.dumps(body) and "password" not in connection,
              "the password is not returned")
        stored = psql(f"SELECT password_enc FROM session_state.connections WHERE connection_id = '{connection_id}';")
        check(stored and "analyst-verify-only" not in stored, "the password is stored encrypted")

        status, _ = call("POST", f"/library/connections/{connection_id}/test", token=TOKEN_B)
        check(status == 404, "another principal cannot test it", str(status))
        status, _ = call("POST", "/workspaces", {"name": "verify steal", "target_schema": connection_id}, token=TOKEN_B)
        check(status == 422, "another principal cannot build a workspace on it", str(status))

        status, body = call("POST", "/workspaces", {"name": "verify shop", "target_schema": connection_id})
        workspace_id = body.get("workspace", {}).get("workspace_id")
        check(status == 201 and workspace_id, "a workspace uses it", f"{status} {body}")
        status, body = call("POST", f"/workspaces/{workspace_id}/scan")
        check(status == 200 and body.get("tables") == 2, "the scan reads its tables", f"{status} {body}")

        tenant = psql(f"SELECT tenant_id FROM session_state.connections WHERE connection_id = '{connection_id}';")

        def run(sql: str, dataset: str | None = connection_id, as_tenant: str = tenant) -> dict:
            params = {"sql": sql, "max_rows": 5}
            if dataset:
                params["dataset"] = dataset
            return mcp(as_tenant, "data.run_select", params)

        t = connection_id
        got = run(f"SELECT c.city, count(*) AS n FROM {t}.orders o JOIN {t}.customers c "
                  f"ON c.customer_id = o.customer_id GROUP BY 1 ORDER BY 1")
        check(got.get("ok") and [r["n"] for r in got["rows"]] == [134, 133, 133],
              "a qualified query reads its tables", str(got)[:200])
        got = run("SELECT count(*) AS n FROM orders")
        check(got.get("ok") and got["rows"] == [{"n": 400}], "an unqualified name resolves to its schema", str(got))
        for label, sql, dataset in [
            ("another schema", f"SELECT * FROM {t}.orders JOIN public.sellers ON true", t),
            ("the system catalog", "SELECT * FROM pg_catalog.pg_roles", t),
            ("a write", f"DELETE FROM {t}.orders", t),
            ("a connection name in SQL for our own database", f"SELECT * FROM {t}.orders", None),
        ]:
            got = run(sql, dataset)
            check(got.get("ok") is False, f"{label} is refused", str(got)[:200])
        got = run(f"SELECT 1 FROM {t}.orders", as_tenant="t-verify-other-tenant")
        check(got.get("ok") is False and "Unknown dataset" in str(got.get("error")),
              "another tenant cannot resolve it", str(got)[:200])
        count = subprocess.run(["docker", "exec", CONTAINER, "psql", "-U", "postgres", "-Atc",
                                "SELECT count(*) FROM shop.orders"], capture_output=True, text=True).stdout.strip()
        check(count == "400", "the database is unchanged", count)

        status, body = call("DELETE", f"/library/connections/{connection_id}")
        check(status == 422 and "workspace" in body.get("detail", ""), "a connection in use cannot be deleted",
              f"{status} {body}")
    finally:
        if workspace_id:
            call("DELETE", f"/workspaces/{workspace_id}")
        if connection_id:
            status, _ = call("DELETE", f"/library/connections/{connection_id}")
            check(status == 200, "after its workspace, the connection is deleted", str(status))
        subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)

    passed = sum(ok for ok, _ in results)
    print(f"\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
