"""
scripts/verify_workspaces.py
Live acceptance check for workspaces, the knowledge store and row-level
security (docs/V3_PLAN.md Phase 3, slice 1), run against the real stack.

WHY THIS EXISTS. The properties that matter live in the database, where a unit
test cannot see them:
- every table in session_state and workspace_docs really has RLS and a policy
  (the live twin of tests/test_rls_coverage.py);
- as the application role, a transaction with NO tenant set sees nothing, a
  transaction for tenant A sees only A's rows, and writing a row for another
  tenant is refused by the database itself;
- the existing app still sees its own data through RLS (dashboards, sessions);
- a second person cannot see, change, scan, approve or delete a workspace;
- business context is versioned; owner edits are unapproved until approval;
  only approved knowledge leaves through `context`; an example's SQL must pass
  the SQL gate; a setup run scores, turns right answers into examples, and an
  answer the owner marks wrong stops being one;
- deleting a workspace removes its knowledge.
It FAILS (it does not skip) if the stack is not up. It asks no model anything.

Needs: the stack running, migration 021 applied, secrets/api_token_dev and
secrets/api_token_second (two different principals). Everything it creates is
deleted at the end.

Usage: python3 scripts/verify_workspaces.py [--setup SCHEMA ...]
  --setup also walks the setup wizard on each named dataset (olist, public, live):
  AI drafts, an owner edit that a re-draft keeps, approval and the 10-question
  setup test, and reports its score (spends model quota: about 60 model calls
  per dataset). The workspace is kept so its answers can be read; it is named
  "Verify setup <schema>" and replaced on the next run.
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

results: list[tuple[bool, str]] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    results.append((ok, label))
    print(f"  {'✓ PASS' if ok else '✗ FAIL'}: {label}" + (f" — {detail}" if detail and not ok else ""))
    return ok


QUERY_TIMEOUT_S = 600      # the model adapter retries a rate limit for several minutes


def call(method: str, path: str, token: str | None = TOKEN_A, body: dict | None = None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=QUERY_TIMEOUT_S if path == "/query" else 120) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return e.code, {"raw": raw.decode(errors="replace")}


def psql(sql: str) -> str:
    """As the owner role (bypasses RLS): the ground truth."""
    out = subprocess.run(
        ["docker", "exec", "-i", "postgres", "psql", "-U", "statesync_migrator", "-d", "statesync",
         "-qtA", "-v", "ON_ERROR_STOP=1"],
        input=sql, capture_output=True, text=True,
    )
    return (out.stdout.strip() + ("\nERROR: " + out.stderr.strip() if out.returncode else "")).strip()


def as_app(tenant: str | None, sql: str) -> str:
    """One transaction as statesync_history (RLS applies), optionally with a tenant bound like the MCP does."""
    bind = f"SELECT set_config('statesync.tenant_id', '{tenant}', true);\n" if tenant is not None else ""
    return psql(f"BEGIN;\nSET LOCAL ROLE statesync_history;\n{bind}{sql}\nROLLBACK;\n")


_MCP_SNIPPET = """
import json, sys, urllib.request
req = json.load(sys.stdin)
body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": req["method"], "params": req["params"]}).encode()
r = urllib.request.Request("http://mcp:8080/rpc", data=body, method="POST",
                           headers={"Content-Type": "application/json", "X-Tenant-ID": req["tenant"]})
print(urllib.request.urlopen(r, timeout=60).read().decode())
"""


def mcp(tenant: str, method: str, params: dict) -> dict:
    """Call the MCP directly from inside the network (for actions the HTTP API does not expose)."""
    out = subprocess.run(
        ["docker", "exec", "-i", ORCHESTRATOR, "python", "-c", _MCP_SNIPPET],
        input=json.dumps({"tenant": tenant, "method": method, "params": params}), capture_output=True, text=True,
    )
    if out.returncode:
        raise SystemExit(f"FATAL: MCP call failed: {out.stderr.strip()}")
    reply = json.loads(out.stdout)
    return reply.get("result") or {"ok": False, "error": "rpc", "message": json.dumps(reply.get("error"))}


def section(title: str) -> None:
    print(f"\n── {title} " + "─" * max(4, 60 - len(title)))


def verify_preflight() -> None:
    section("The browser may call the workspace routes")
    req = urllib.request.Request(f"{BASE}/workspaces/x/descriptions", method="OPTIONS", headers={
        "Origin": "http://localhost:3000", "Access-Control-Request-Method": "PUT",
        "Access-Control-Request-Headers": "authorization,content-type"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            allowed = r.headers.get("access-control-allow-methods", "")
            ok = r.status == 200 and "PUT" in allowed
    except urllib.error.HTTPError as e:
        ok, allowed = False, f"{e.code}"
    check(ok, "a PUT from the web app's origin passes the CORS preflight", allowed)


def verify_rls_catalog() -> None:
    section("Row-level security in the catalog")
    rows = psql("""
        SELECT n.nspname || '.' || c.relname, c.relrowsecurity,
               (SELECT count(*) FROM pg_policies p WHERE p.schemaname = n.nspname AND p.tablename = c.relname)
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname IN ('session_state', 'workspace_docs') AND c.relkind = 'r' ORDER BY 1;
    """).splitlines()
    check(len(rows) >= 16, "session_state and workspace_docs hold the expected tables", f"{len(rows)} tables")
    missing = [r.split("|")[0] for r in rows if r.split("|")[1] != "t" or r.split("|")[2] == "0"]
    check(not missing, "every table has RLS enabled and a policy", ", ".join(missing))
    bypass = psql("SELECT string_agg(rolname, ',') FROM pg_roles WHERE rolname IN "
                  "('statesync_history', 'statesync_reader') AND (rolbypassrls OR rolsuper);")
    check(bypass == "", "the application roles cannot bypass RLS", bypass)


def verify_rls_behaviour(tenant_a: str) -> None:
    section("Row-level security, as the application role")
    total = int(psql("SELECT count(*) FROM session_state.dashboards;") or 0)
    mine = int(psql(f"SELECT count(*) FROM session_state.dashboards WHERE tenant_id = '{tenant_a}';") or 0)
    check(total > 0, "there are dashboards to hide", str(total))
    unset = as_app(None, "SELECT count(*) FROM session_state.dashboards;").splitlines()[-1]
    check(unset == "0", "with no tenant bound, the application role sees no rows", unset)
    bound = as_app(tenant_a, "SELECT count(*) FROM session_state.dashboards;").splitlines()[-1]
    check(bound == str(mine), "with tenant A bound, it sees exactly A's rows", f"{bound} vs {mine}")
    other = as_app("tenant-that-does-not-exist", "SELECT count(*) FROM session_state.turn_rows;").splitlines()[-1]
    check(other == "0", "another tenant sees none of A's stored results", other)
    tile_rows = int(psql(f"SELECT count(*) FROM session_state.tile_versions v JOIN session_state.tiles t "
                         f"USING (tile_id) WHERE t.tenant_id = '{tenant_a}';") or 0)
    seen = as_app(tenant_a, "SELECT count(*) FROM session_state.tile_versions;").splitlines()[-1]
    check(seen == str(tile_rows), "child tables without a tenant column follow their tile", f"{seen} vs {tile_rows}")
    forged = as_app(tenant_a, "INSERT INTO session_state.dashboards (dashboard_id, tenant_id, principal_id, name, "
                              "target_schema, position) VALUES ('rls-forged', 'someone-else', 'p', 'x', 'olist', 0);")
    check("row-level security" in forged, "writing a row for another tenant is refused by the database", forged[-160:])


def verify_existing_app_still_sees_its_data(tenant_a: str) -> None:
    section("The existing app through RLS")
    status, body = call("GET", "/dashboards")
    principal = psql("SELECT principal_id FROM session_state.dashboards WHERE tenant_id = "
                     f"'{tenant_a}' GROUP BY 1 ORDER BY count(*) DESC LIMIT 1;")
    expected = psql(f"SELECT count(*) FROM session_state.dashboards WHERE tenant_id = '{tenant_a}' "
                    f"AND principal_id = '{principal}';")
    check(status == 200 and str(len(body.get("dashboards", []))) == expected,
          "GET /dashboards returns all of my dashboards", f"{status} {len(body.get('dashboards', []))} vs {expected}")
    status, body = call("GET", "/sessions")
    check(status == 200 and len(body.get("sessions", [])) > 0, "GET /sessions still lists my conversations", str(status))
    status, body = call("GET", "/tiles")
    check(status == 200, "GET /tiles still works", str(status))


SETUP_EXIT_MIN = 8          # docs/V3_PLAN.md Phase 3 exit: at least 8 of 10
DRAFT_TIMEOUT_S = 600
TEST_TIMEOUT_S = 1800


def wait_for(path: str, key: str, timeout_s: int) -> dict:
    deadline = time.time() + timeout_s
    body: dict = {}
    while time.time() < deadline:
        body = call("GET", path)[1]
        job = body.get("job") or {}
        if job.get("state") in ("done", "failed"):
            return body
        time.sleep(5)
    return body


def verify_setup(schema: str) -> None:
    section(f"Setup wizard on {schema}")
    psql(f"DELETE FROM session_state.workspaces WHERE name = 'Verify setup {schema}';")
    status, body = call("POST", "/workspaces", body={"name": f"Verify setup {schema}", "target_schema": schema})
    if not check(status == 201, "create the workspace", f"{status} {body}"):
        return
    wid = body["workspace"]["workspace_id"]
    check(call("POST", f"/workspaces/{wid}/setup")[0] == 409, "the test before approval is refused (409)")
    check(call("POST", f"/workspaces/{wid}/draft", token=TOKEN_B)[0] == 404, "another principal cannot draft it")

    status, body = call("POST", f"/workspaces/{wid}/draft")
    check(status == 202, "drafting starts (202)", f"{status} {body}")
    check(call("POST", f"/workspaces/{wid}/draft")[0] == 409, "a second setup step while one runs is refused (409)")
    job = wait_for(f"/workspaces/{wid}/draft", "job", DRAFT_TIMEOUT_S).get("job") or {}
    check(job.get("state") == "done", "drafting finishes", f"{job.get('state')} {job.get('error')}")
    cols = call("GET", f"/workspaces/{wid}/catalog")[1].get("columns", [])
    drafted = [c for c in cols if c["description"] and c["description_source"] == "ai"]
    check(len(drafted) >= 0.9 * len(cols), "at least 90% of tables and columns have a draft", f"{len(drafted)}/{len(cols)}")
    check(not any(c["approved"] for c in cols), "no draft is approved")
    real = set(psql(f"SELECT table_name || '.' || column_name FROM information_schema.columns "
                    f"WHERE table_schema = '{schema}';").splitlines())
    stray = [f"{c['table_name']}.{c['column_name']}" for c in cols
             if c["column_name"] and f"{c['table_name']}.{c['column_name']}" not in real]
    check(not stray, "every drafted column exists in the database", ", ".join(stray[:5]))
    defs = call("GET", f"/workspaces/{wid}/definitions")[1].get("definitions", [])
    check(len(defs) > 0 and all(d["source"] == "ai" for d in defs), "business definitions are drafted", str(len(defs)))
    for c in drafted[:3]:
        print(f"      e.g. {c['table_name']}.{c['column_name'] or '(table)'}: {c['description']}")

    target = drafted[0]
    call("PUT", f"/workspaces/{wid}/descriptions", body={"items": [
        {"table_name": target["table_name"], "column_name": target["column_name"], "description": "Owner wording."}]})
    call("POST", f"/workspaces/{wid}/draft")
    wait_for(f"/workspaces/{wid}/draft", "job", DRAFT_TIMEOUT_S)
    again = call("GET", f"/workspaces/{wid}/catalog")[1].get("columns", [])
    kept = next(c for c in again if c["table_name"] == target["table_name"] and c["column_name"] == target["column_name"])
    check(kept["description"] == "Owner wording." and kept["description_source"] == "owner",
          "a re-draft keeps what the owner wrote", str(kept))

    status, body = call("POST", f"/workspaces/{wid}/approve")
    check(status == 200 and body["workspace"]["setup_state"] == "approved", "the owner approves", f"{status}")
    status, body = call("POST", f"/workspaces/{wid}/setup")
    check(status == 202, "the setup test starts (202)", f"{status} {body}")
    graded_by = body.get("graded_by")
    body = wait_for(f"/workspaces/{wid}/setup", "job", TEST_TIMEOUT_S)
    run = body.get("run") or {}
    job = body.get("job") or {}
    check(job.get("state") == "done" and run.get("state") == "done", "the test finishes",
          f"{job.get('state')} {job.get('error')}")
    for r in run.get("results", []):
        print(f"      {r['position']:>2}. {r['verdict']:<7} {r['question'][:70]}  {r.get('detail') or ''}")
    _, sessions = call("GET", "/sessions")
    check(not any(x["session_id"].startswith("setup-") for x in sessions.get("sessions", [])),
          "the test's conversations are hidden from the chat list")
    if graded_by == "reference":
        score = run.get("score") or 0
        ws_now = call("GET", f"/workspaces/{wid}")[1]["workspace"]
        check(ws_now["counts"]["examples"] == score, "every right answer became an example",
              f"{ws_now['counts']['examples']} vs {score}")
        print(f"      SCORE on {schema}: {score}/{run.get('total')} (exit bar {SETUP_EXIT_MIN})")
        results.append((score >= SETUP_EXIT_MIN, f"{schema} scores at least {SETUP_EXIT_MIN}/10"))
        print(f"  {'✓ PASS' if score >= SETUP_EXIT_MIN else '✗ FAIL'}: {schema} scores at least {SETUP_EXIT_MIN}/10")
    else:
        check(all(r["verdict"] in ("pending", "error") for r in run.get("results", [])),
              "an owner-graded run waits for the owner")


def main() -> int:
    args = sys.argv[1:]
    setup_schemas = args[args.index("--setup") + 1:] if "--setup" in args else []
    try:
        call("GET", "/health", token=None)
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"FATAL: the stack is not up ({exc})") from None

    verify_preflight()
    verify_rls_catalog()

    section("Create, read, isolate")
    status, body = call("POST", "/workspaces", body={"name": "Verify shop", "target_schema": "olist",
                                                     "business_context": "A Brazilian marketplace."})
    if not check(status == 201, "create a workspace (201)", f"{status} {body}"):
        return 1
    ws = body["workspace"]
    wid = ws["workspace_id"]
    tenant_a, principal_a = psql(f"SELECT tenant_id || '|' || owner_principal_id FROM session_state.workspaces "
                                 f"WHERE workspace_id = '{wid}';").split("|")
    try:
        check(ws["context_version"] == 1 and ws["setup_state"] == "created", "it starts at version 1, state created")
        check(call("POST", "/workspaces", body={"name": "x", "target_schema": "pg_catalog"})[0] == 422,
              "an unknown dataset is refused (422)")
        check(call("POST", "/workspaces", body={"name": "  ", "target_schema": "olist"})[0] == 422,
              "a blank name is refused (422)")
        for method, path, payload in (("GET", "", None), ("PATCH", "", {"name": "stolen"}), ("POST", "/scan", None),
                                      ("POST", "/approve", None), ("GET", "/catalog", None), ("DELETE", "", None)):
            got = call(method, f"/workspaces/{wid}{path}", token=TOKEN_B, body=payload)[0]
            check(got == 404, f"another principal gets 404 for {method} {path or '/'}", str(got))
        _, listing = call("GET", "/workspaces", token=TOKEN_B)
        check(all(w["workspace_id"] != wid for w in listing.get("workspaces", [])), "and does not see it listed")
        other = mcp("tenant-that-does-not-exist", "workspaces.manage",
                    {"action": "get", "principal_id": principal_a, "workspace_id": wid})
        check(other.get("error") == "not_found", "the same principal under another tenant cannot see it", str(other))

        verify_rls_behaviour(tenant_a)
        verify_existing_app_still_sees_its_data(tenant_a)

        section("Business context is versioned")
        status, body = call("PATCH", f"/workspaces/{wid}", body={"business_context": "Marketplace; revenue is price + freight."})
        check(status == 200 and body["workspace"]["context_version"] == 2, "an edit makes version 2", f"{status} {body}")
        call("PATCH", f"/workspaces/{wid}", body={"business_context": "Marketplace; revenue is price + freight."})
        versions = psql(f"SELECT string_agg(version::text, ',' ORDER BY version) FROM "
                        f"session_state.business_context_versions WHERE workspace_id = '{wid}';")
        check(versions == "1,2", "both versions are kept, and an unchanged save adds none", versions)

        section("Scan and catalog")
        status, body = call("POST", f"/workspaces/{wid}/scan")
        olist_tables = int(psql("SELECT count(*) FROM pg_tables WHERE schemaname = 'olist';"))
        check(status == 200 and body.get("tables") == olist_tables, "the scan reads every olist table",
              f"{status} {body} vs {olist_tables}")
        _, catalog = call("GET", f"/workspaces/{wid}/catalog")
        cols = catalog.get("columns", [])
        status_col = next((c for c in cols if c["table_name"] == "orders" and c["column_name"] == "order_status"), None)
        check(status_col is not None and "delivered" in status_col["sample_values"],
              "orders.order_status carries sample values", str(status_col))
        check(all(not c["approved"] for c in cols), "nothing is approved after a scan")
        check(call("GET", f"/workspaces/{wid}")[1]["workspace"]["setup_state"] == "scanned", "state is scanned")
        status, _ = call("POST", f"/workspaces/{wid}/scan")
        check(status == 200 and len(call("GET", f"/workspaces/{wid}/catalog")[1]["columns"]) == len(cols),
              "a second scan replaces, not duplicates")

        section("Descriptions, definitions, approval")
        status, body = call("PUT", f"/workspaces/{wid}/descriptions", body={"items": [
            {"table_name": "orders", "column_name": "order_status", "description": "Where the order is."},
            {"table_name": "orders", "description": "One row per order."}]})
        check(status == 200 and body.get("updated") == 2, "the owner describes a column and a table", f"{status} {body}")
        check(call("PUT", f"/workspaces/{wid}/descriptions", body={"items": [
            {"table_name": "no_such_table", "column_name": "x", "description": "y"}]})[0] == 404,
              "describing something not in the catalog is refused (404)")
        source = psql(f"SELECT description_source FROM session_state.kb_columns WHERE workspace_id = '{wid}' "
                      "AND table_name = 'orders' AND column_name = 'order_status';")
        check(source == "owner", "it is stored as the owner's words", source)
        status, body = call("PUT", f"/workspaces/{wid}/definitions", body={"items": [
            {"term": "Revenue", "definition": "price + freight_value"}, {"term": "On time", "definition": "delivered by the estimate"}]})
        check(status == 200 and len(body.get("definitions", [])) == 2, "two definitions saved", f"{status} {body}")
        _, body = call("PUT", f"/workspaces/{wid}/definitions", body={"items": [{"term": "revenue", "definition": "price only"}]})
        defs = body.get("definitions", [])
        check(len(defs) == 2 and any(d["definition"] == "price only" for d in defs),
              "the same term in another case updates, not duplicates", str(defs))

        context_args = {"action": "context", "principal_id": principal_a, "workspace_id": wid,
                        "question": "What is the revenue by order status?"}
        ctx = mcp(tenant_a, "workspaces.manage", context_args)
        check(ctx.get("ok") and ctx["descriptions"] == [] and ctx["definitions"] == [],
              "before approval, no description or definition reaches a prompt", str(ctx)[:200])
        check(ctx.get("business_context", "").startswith("Marketplace"), "the business context does (the owner wrote it)")

        status, body = call("POST", f"/workspaces/{wid}/approve")
        check(status == 200 and body.get("approved_columns") == 2 and body.get("approved_definitions") == 2,
              "approve accepts the two descriptions and two definitions", f"{status} {body}")
        ctx = mcp(tenant_a, "workspaces.manage", context_args)
        check(len(ctx.get("descriptions", [])) == 2, "after approval, the descriptions reach the context")
        check([d["term"] for d in ctx.get("definitions", [])] == ["revenue"],
              "only the definitions the question mentions", str(ctx.get("definitions")))
        call("PUT", f"/workspaces/{wid}/descriptions", body={"items": [
            {"table_name": "orders", "column_name": "order_status", "description": "Edited later."}]})
        ctx = mcp(tenant_a, "workspaces.manage", context_args)
        check(len(ctx.get("descriptions", [])) == 1, "an edit after approval is a draft again until re-approved")
        _, defs = call("GET", f"/workspaces/{wid}/definitions")
        on_time = next(d for d in defs["definitions"] if d["term"] == "On time")
        check(call("DELETE", f"/workspaces/{wid}/definitions/{on_time['definition_id']}", token=TOKEN_B)[0] == 404,
              "another principal cannot delete a definition")
        check(call("DELETE", f"/workspaces/{wid}/definitions/{on_time['definition_id']}")[0] == 200,
              "the owner can")

        section("Examples and the setup test")
        bad = mcp(tenant_a, "workspaces.manage", {"action": "add_example", "principal_id": principal_a,
                                                  "workspace_id": wid, "question": "q", "sql": "DELETE FROM olist.orders"})
        check(bad.get("error") == "invalid", "an example whose SQL fails the gate is refused", str(bad))
        questions = [
            {"question": "How many orders are there?", "reference_sql": "SELECT count(*) FROM olist.orders"},
            {"question": "How many customers are there?", "reference_sql": "SELECT count(*) FROM olist.customers"},
        ]
        run = mcp(tenant_a, "workspaces.manage", {"action": "start_run", "principal_id": principal_a,
                                                  "workspace_id": wid, "graded_by": "reference", "questions": questions})
        check(run.get("ok") and run["run"]["total"] == 2 and all(r["has_reference"] for r in run["run"]["results"]),
              "a reference-graded run starts with 2 questions", str(run)[:200])
        run_id = run["run"]["run_id"]
        mcp(tenant_a, "workspaces.manage", {"action": "record_result", "principal_id": principal_a, "run_id": run_id,
                                            "position": 1, "verdict": "right", "answer_text": "99,441 orders",
                                            "answer_sql": "SELECT count(*) FROM olist.orders"})
        mcp(tenant_a, "workspaces.manage", {"action": "record_result", "principal_id": principal_a, "run_id": run_id,
                                            "position": 2, "verdict": "wrong", "answer_sql": "SELECT 1"})
        done = mcp(tenant_a, "workspaces.manage", {"action": "finish_run", "principal_id": principal_a, "run_id": run_id})
        check(done.get("ok") and done["run"]["score"] == 1 and done["run"]["state"] == "done", "the run scores 1/2", str(done)[:200])
        ws_now = call("GET", f"/workspaces/{wid}")[1]["workspace"]
        check(ws_now["setup_state"] == "tested" and ws_now["setup_score"] == 1 and ws_now["counts"]["examples"] == 1,
              "the workspace is tested, 1/2, with one example", str(ws_now))
        ctx = mcp(tenant_a, "workspaces.manage", {**context_args, "question": "how many orders are there"})
        check((ctx.get("exact") or {}).get("sql") == "SELECT count(*) FROM olist.orders",
              "the right answer is now an exact match for the fast path", str(ctx.get("exact")))
        section("Questions asked in the workspace")
        status, body = call("POST", "/query", body={"query": "How many orders are there?", "workspace_id": wid})
        trace = body.get("state_trace", {}) if status == 200 else {}
        check(status == 200 and trace.get("verified_example") is True
              and trace.get("executed_sql") == "SELECT count(*) FROM olist.orders",
              "an exact match of an approved example runs its SQL (fast path, verified)",
              f"{status} {trace.get('verified_example')} {trace.get('executed_sql')}")
        check(trace.get("target_schema") == "olist" and trace.get("workspace_id") == wid,
              "on the workspace's dataset, recorded in the trace")
        check(call("POST", "/query", token=TOKEN_B, body={"query": "How many orders are there?",
                                                         "workspace_id": wid})[0] == 404,
              "another principal cannot ask in my workspace")
        check(call("POST", "/query", body={"query": "How many orders are there?", "workspace_id": wid,
                                           "schema": "public"})[0] == 409,
              "a question cannot name another dataset than its workspace's")
        status, body = call("POST", "/query", body={"query": "How many sellers are in each state?", "workspace_id": wid})
        check(status == 200 and body["state_trace"].get("verified_example") is False
              and body["state_trace"].get("executed_sql"),
              "a question without an exact match goes through the pipeline", f"{status}")

        _, latest = call("GET", f"/workspaces/{wid}/setup")
        check(latest.get("run", {}).get("run_id") == run_id, "GET /setup returns the latest run")
        check(call("PUT", f"/workspaces/{wid}/setup/{run_id}/results/1", body={"verdict": "wrong"})[0] == 422,
              "a reference-graded answer cannot be re-graded by hand")

        owner_run = mcp(tenant_a, "workspaces.manage", {"action": "start_run", "principal_id": principal_a,
                                                        "workspace_id": wid, "graded_by": "owner",
                                                        "questions": [{"question": "How many sellers are there?"}]})
        orun = owner_run["run"]["run_id"]
        mcp(tenant_a, "workspaces.manage", {"action": "record_result", "principal_id": principal_a, "run_id": orun,
                                            "position": 1, "verdict": "pending", "answer_sql": "SELECT count(*) FROM olist.sellers"})
        check(call("PUT", f"/workspaces/{wid}/setup/{orun}/results/1", token=TOKEN_B, body={"verdict": "right"})[0] == 404,
              "another principal cannot grade my run")
        status, body = call("POST", "/workspaces", body={"name": "Verify other", "target_schema": "public"})
        other_wid = body["workspace"]["workspace_id"]
        check(call("PUT", f"/workspaces/{other_wid}/setup/{orun}/results/1", body={"verdict": "right"})[0] == 404,
              "a run cannot be graded under another workspace's URL")
        call("DELETE", f"/workspaces/{other_wid}")
        status, body = call("PUT", f"/workspaces/{wid}/setup/{orun}/results/1", body={"verdict": "right"})
        check(status == 200 and body["run"]["score"] == 1, "the owner marks it right and the score follows", f"{status} {body}")
        check(call("GET", f"/workspaces/{wid}")[1]["workspace"]["counts"]["examples"] == 2, "and it becomes an example")
        call("PUT", f"/workspaces/{wid}/setup/{orun}/results/1", body={"verdict": "wrong"})
        check(call("GET", f"/workspaces/{wid}")[1]["workspace"]["counts"]["examples"] == 1,
              "marking it wrong again withdraws the example")
    finally:
        section("Delete")
        status, _ = call("DELETE", f"/workspaces/{wid}")
        check(status == 200, "the owner deletes the workspace", str(status))
        left = psql("SELECT (SELECT count(*) FROM session_state.kb_columns WHERE workspace_id = '%s') + "
                    "(SELECT count(*) FROM session_state.kb_definitions WHERE workspace_id = '%s') + "
                    "(SELECT count(*) FROM session_state.kb_examples WHERE workspace_id = '%s') + "
                    "(SELECT count(*) FROM session_state.setup_runs WHERE workspace_id = '%s') + "
                    "(SELECT count(*) FROM session_state.business_context_versions WHERE workspace_id = '%s');"
                    % ((wid,) * 5))
        check(left == "0", "its knowledge, runs and context versions go with it", left)
        psql("DELETE FROM session_state.workspaces WHERE name LIKE 'Verify %';")

    for schema in setup_schemas:
        verify_setup(schema)

    failed = [label for ok, label in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
