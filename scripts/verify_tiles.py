#!/usr/bin/env python3
"""
scripts/verify_tiles.py
Live acceptance check for dashboard tiles (docs/V3_PLAN.md Phase 1), run
against the real stack through the orchestrator's HTTP API.

WHY THIS EXISTS. Tiles re-run saved SQL on a schedule, so their correctness
lives in properties a unit test cannot see: the refresh really re-executes the
same statement, a schema change really stops the numbers, and one user really
cannot reach another's tile through any route. This script asks three real
questions, pins the answers, and checks each property end to end. It FAILS
(it does not skip) if the stack is not up, like verify_deployed.py.

Needs: the stack running, secrets/api_token_dev and secrets/api_token_second
(two different principals), and docker access to the postgres container (two
checks tamper with stored rows the way a schema change or the passage of time
would, then restore nothing -- the test tile is deleted at the end).

Usage: python3 scripts/verify_tiles.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from api_auth import api_token  # noqa: E402

BASE = "http://localhost:8090"
TOKEN_A = api_token()
TOKEN_B = (REPO_ROOT / "secrets" / "api_token_second").read_text().strip()
CHART_MAX = 5000            # config/statesync.toml [sql] chart_max_data_rows

results: list[tuple[bool, str]] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    results.append((ok, label))
    print(f"  {'✓ PASS' if ok else '✗ FAIL'}: {label}" + (f" — {detail}" if detail and not ok else ""))
    return ok


def call(method: str, path: str, token: str | None = TOKEN_A, body: dict | None = None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return e.code, {"raw": raw.decode(errors="replace")}


def psql(sql: str) -> str:
    out = subprocess.run(
        ["docker", "exec", "postgres", "psql", "-U", "statesync_migrator", "-d", "statesync", "-tAc", sql],
        capture_output=True, text=True,
    )
    return out.stdout.strip()


def ask(question: str, session_id: str) -> dict:
    status, body = call("POST", "/query", body={"query": question, "session_id": session_id})
    if status != 200:
        raise SystemExit(f"FATAL: /query returned {status}: {body}")
    return body["state_trace"]


def main() -> int:
    status, health = call("GET", "/health", token=None)
    if status != 200:
        print(f"FATAL: orchestrator not reachable at {BASE} (HTTP {status}).")
        return 1

    print("── setup: three real answers in one conversation ──")
    session = f"verify-tiles-{uuid.uuid4().hex[:10]}"
    t1 = ask("How many orders are there in total?", session)
    t2 = ask("And how many sellers?", session)
    t3 = ask("What transit time has FastShip 3 (LP003) committed to for Metro Mumbai?", session)
    print(f"  turns: {t1.get('turn_index')} ({t1.get('route')}), {t2.get('turn_index')} ({t2.get('route')}), "
          f"{t3.get('turn_index')} ({t3.get('route')})")

    print("── pin ──")
    status, body = call("POST", "/tiles", body={"session_id": session, "turn_index": t1["turn_index"],
                                                "title": "Total orders"})
    check(status in (200, 201), "pin a database answer", f"HTTP {status} {body}")
    tile = body.get("tile") or {}
    tile_id = tile.get("tile_id")
    if not tile_id:
        print("FATAL: no tile_id returned; cannot continue.")
        return 1
    check(tile.get("version") == 1 and tile.get("state") == "ok", "new tile is version 1 and ok", str(tile)[:200])
    result = tile.get("result") or {}
    check(bool(result.get("rows")) and result.get("row_count", 0) >= 1, "pin computes a first result", str(result)[:200])
    check(tile.get("title") == "Total orders", "title is stored")
    if tile.get("chart") is not None:
        values = (tile["chart"].get("data") or {}).get("values")
        check(values == result.get("rows", [])[:CHART_MAX], "chart has the result rows bound as data.values",
              str(values)[:200])
    else:
        check(True, "no chart for this answer (a single number) — binding not applicable")

    print("── refresh re-runs the same SQL ──")
    time.sleep(1.1)
    status, body = call("POST", f"/tiles/{tile_id}/refresh")
    refreshed = body.get("tile") or {}
    check(status == 200, "force refresh", f"HTTP {status} {body}")
    check(refreshed.get("sql") == tile.get("sql"), "refresh keeps the identical SQL")
    check((refreshed.get("result") or {}).get("computed_at", "") > result.get("computed_at", ""),
          "refresh produces a newer timestamp")

    print("── read paths ──")
    status, body = call("GET", "/tiles")
    check(status == 200 and any(t.get("tile_id") == tile_id for t in body.get("tiles", [])), "GET /tiles lists it")
    status, body = call("GET", f"/tiles/{tile_id}")
    check(status == 200 and len((body.get("tile") or {}).get("versions", [])) == 1,
          "GET /tiles/{id} returns it with its version history", str(body)[:200])

    print("── another principal cannot reach it through any route ──")
    for method, path, payload in [
        ("GET", f"/tiles/{tile_id}", None),
        ("POST", f"/tiles/{tile_id}/refresh", None),
        ("PATCH", f"/tiles/{tile_id}", {"title": "hijacked"}),
        ("POST", f"/tiles/{tile_id}/confirm", None),
        ("POST", f"/tiles/{tile_id}/replace", {"session_id": session, "turn_index": t2["turn_index"]}),
        ("DELETE", f"/tiles/{tile_id}", None),
    ]:
        status, _ = call(method, path, token=TOKEN_B, body=payload)
        check(status == 404, f"other principal: {method} {path.replace(tile_id, '{id}')} -> 404", f"got {status}")
    status, body = call("GET", "/tiles", token=TOKEN_B)
    check(status == 200 and all(t.get("tile_id") != tile_id for t in body.get("tiles", [])),
          "other principal's GET /tiles does not include it")
    status, _ = call("POST", "/tiles", token=TOKEN_B, body={"session_id": session, "turn_index": t1["turn_index"]})
    check(status == 404, "other principal cannot pin this principal's answer", f"got {status}")
    status, _ = call("GET", "/tiles", token=None)
    check(status == 401, "unauthenticated GET /tiles -> 401", f"got {status}")

    print("── what cannot be pinned ──")
    status, body = call("POST", "/tiles", body={"session_id": session, "turn_index": t3["turn_index"]})
    check(status == 422 and "database" in str(body.get("detail", "")).lower(),
          "a document-only answer -> 422", f"HTTP {status} {body}")
    status, _ = call("POST", "/tiles", body={"session_id": session, "turn_index": 999})
    check(status == 404, "a turn that does not exist -> 404", f"got {status}")

    print("── update ──")
    status, _ = call("PATCH", f"/tiles/{tile_id}", body={"refresh_interval_s": 5})
    check(status == 422, "refresh interval below the minimum -> 422", f"got {status}")
    status, body = call("PATCH", f"/tiles/{tile_id}", body={"title": "Orders (all time)"})
    check(status == 200 and (body.get("tile") or {}).get("title") == "Orders (all time)", "rename", str(body)[:200])

    print("── a schema change stops the numbers ──")
    psql(f"UPDATE session_state.tile_versions SET fingerprint = 'schema-changed' WHERE tile_id = '{tile_id}'")
    _, before = call("GET", f"/tiles/{tile_id}")
    before_at = ((before.get("tile") or {}).get("result") or {}).get("computed_at")
    status, body = call("POST", f"/tiles/{tile_id}/refresh")
    changed = body.get("tile") or {}
    check(changed.get("state") == "needs_review", "refresh after a schema change -> needs_review", str(changed)[:200])
    check((changed.get("result") or {}).get("computed_at") == before_at, "the last good result is kept, not recomputed")
    status, body = call("POST", f"/tiles/{tile_id}/confirm")
    confirmed = body.get("tile") or {}
    check(status == 200 and confirmed.get("state") == "ok" and confirmed.get("version") == 2,
          "confirm accepts the schema as version 2 and refreshes", str(confirmed)[:200])

    print("── replace from a follow-up answer ──")
    status, body = call("POST", f"/tiles/{tile_id}/replace",
                        body={"session_id": session, "turn_index": t2["turn_index"]})
    replaced = body.get("tile") or {}
    check(status == 200 and replaced.get("version") == 3 and replaced.get("sql") != tile.get("sql"),
          "replace makes version 3 with the new answer's SQL", str(replaced)[:200])
    _, body = call("GET", f"/tiles/{tile_id}")
    check(len((body.get("tile") or {}).get("versions", [])) == 3, "version history has all three versions")

    print("── a stale tile refreshes when read ──")
    call("PATCH", f"/tiles/{tile_id}", body={"refresh_interval_s": 60})
    psql(f"UPDATE session_state.tile_results SET computed_at = now() - interval '2 minutes' WHERE tile_id = '{tile_id}'")
    backdated = psql(f"SELECT computed_at FROM session_state.tile_results WHERE tile_id = '{tile_id}'")
    _, body = call("GET", f"/tiles/{tile_id}")
    after = psql(f"SELECT computed_at FROM session_state.tile_results WHERE tile_id = '{tile_id}'")
    check(after > backdated and (body.get("tile") or {}).get("stale") is False,
          "reading a tile past its interval refreshes it", f"{backdated} -> {after}")

    print("── audit ──")
    audited = psql("SELECT count(*) FROM session_state.audit_log WHERE principal_id = 'p_dev_local' "
                   f"AND route LIKE '/tiles%' AND outcome = 'allowed' AND detail LIKE '%{tile_id}%'")
    check(audited.isdigit() and int(audited) >= 5, "tile changes are in the audit log", f"{audited} rows")

    print("── delete ──")
    status, _ = call("DELETE", f"/tiles/{tile_id}")
    check(status == 200, "delete", f"got {status}")
    status, _ = call("GET", f"/tiles/{tile_id}")
    check(status == 404, "deleted tile -> 404", f"got {status}")
    left = psql(f"SELECT (SELECT count(*) FROM session_state.tile_versions WHERE tile_id = '{tile_id}') + "
                f"(SELECT count(*) FROM session_state.tile_results WHERE tile_id = '{tile_id}')")
    check(left == "0", "its versions and results are gone too", f"{left} rows left")

    passed = sum(ok for ok, _ in results)
    failed = len(results) - passed
    print(f"\n──────── Summary ────────\n  PASSED: {passed}\n  FAILED: {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
