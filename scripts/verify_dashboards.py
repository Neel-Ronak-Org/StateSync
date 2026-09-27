#!/usr/bin/env python3
"""
scripts/verify_dashboards.py
Live acceptance check for dashboards and conversation deletion (docs/V3_PLAN.md
Phase 2b), run against the real stack through the orchestrator's HTTP API.

WHY THIS EXISTS. The properties that matter live in the database, where a unit
test cannot see them: a second person really cannot reach, rename, rearrange
or delete someone else's dashboard; a layout request really is all-or-nothing;
a tile really cannot be put on a dashboard for another dataset; a pinned
follow-up really gets its rewritten title; deleting a conversation really
removes only the owner's turns, keeps the audit trail, and leaves tiles pinned
from it working. It FAILS (it does not skip) if the stack is not up.

Needs: the stack running, secrets/api_token_dev and secrets/api_token_second
(two different principals). Asks two real questions. Everything it creates is
deleted at the end.

Usage: python3 scripts/verify_dashboards.py
"""

from __future__ import annotations

import json
import subprocess
import sys
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


def ask(question: str, session_id: str, schema: str = "public") -> dict:
    status, body = call("POST", "/query", body={"query": question, "session_id": session_id, "schema": schema})
    if status != 200:
        raise SystemExit(f"FATAL: /query returned {status}: {body}")
    return body["state_trace"]


def section(title: str) -> None:
    print(f"\n── {title} " + "─" * max(4, 60 - len(title)))


def main() -> int:
    status, health = call("GET", "/health", token=None)
    if status != 200:
        print(f"FATAL: orchestrator not reachable at {BASE} ({status})")
        return 1

    tag = uuid.uuid4().hex[:8]
    created: list[str] = []
    session_id = f"verify-dash-{tag}"
    try:
        section("Dashboards are owned")
        status, body = call("POST", "/dashboards", body={"name": f"Verify {tag}", "target_schema": "public"})
        check(status == 201, "create returns 201", f"{status} {body}")
        board = body["dashboard"]["dashboard_id"]
        created.append(board)
        status, body = call("POST", "/dashboards", body={"name": f"Verify {tag} olist", "target_schema": "olist"})
        other_dataset = body["dashboard"]["dashboard_id"]
        created.append(other_dataset)
        status, body = call("POST", "/dashboards", body={"name": f"Verify {tag} second", "target_schema": "public"})
        second_board = body["dashboard"]["dashboard_id"]
        created.append(second_board)

        _, listed = call("GET", "/dashboards?schema=public")
        check(any(d["dashboard_id"] == board for d in listed["dashboards"]), "owner sees the new dashboard")
        _, listed_b = call("GET", "/dashboards", token=TOKEN_B)
        check(all(d["dashboard_id"] not in created for d in listed_b["dashboards"]),
              "another principal's list does not include it")
        check(call("PATCH", f"/dashboards/{board}", token=TOKEN_B, body={"name": "mine now"})[0] == 404,
              "another principal cannot rename it (404)")
        check(call("PUT", f"/dashboards/{board}/layout", token=TOKEN_B, body={"items": []})[0] == 404,
              "another principal cannot rearrange it (404)")
        check(call("DELETE", f"/dashboards/{board}", token=TOKEN_B)[0] == 404,
              "another principal cannot delete it (404)")
        check(call("GET", f"/tiles?dashboard_id={board}", token=TOKEN_B)[0] == 404,
              "another principal cannot list its tiles (404)")
        check(call("POST", "/dashboards", body={"name": "  ", "target_schema": "public"})[0] == 422,
              "a blank name is refused (422)")
        check(call("POST", "/dashboards", body={"name": "x", "target_schema": "pg_catalog"})[0] == 422,
              "a dataset outside the allowlist is refused (422)")
        status, body = call("PATCH", f"/dashboards/{board}", body={"name": f"Verify {tag} renamed"})
        check(status == 200 and body["dashboard"]["name"] == f"Verify {tag} renamed", "owner can rename")

        section("Pinning onto a dashboard (two real questions)")
        trace1 = ask("Which 5 cities have the most sellers?", session_id)
        trace2 = ask("And the fewest?", session_id)
        turn1, turn2 = trace1["turn_index"], trace2["turn_index"]

        status, body = call("POST", "/tiles", body={"session_id": session_id, "turn_index": turn1,
                                                    "dashboard_id": board, "viz": "hbar",
                                                    "layout": {"x": 0, "y": 0, "w": 6, "h": 8}})
        check(status == 201, "pin onto a chosen dashboard with viz and layout", f"{status} {body}")
        tile1 = body["tile"]
        check(tile1["dashboard_id"] == board and tile1["viz"] == "hbar"
              and tile1["layout"] == {"x": 0, "y": 0, "w": 6, "h": 8} and tile1["target_schema"] == "public",
              "the tile carries its dashboard, viz, layout and dataset", json.dumps(tile1)[:300])

        status, body = call("POST", "/tiles", body={"session_id": session_id, "turn_index": turn2,
                                                    "dashboard_id": board})
        check(status == 201, "pin the follow-up", f"{status} {body}")
        tile2 = body["tile"]
        _, stored = call("GET", f"/sessions/{session_id}")
        stored_turn = next(t for t in stored["turns"] if t["turn_index"] == turn2)
        check(bool(stored_turn["rewrite_applied"]), "the follow-up was rewritten by the pipeline",
              json.dumps(stored_turn)[:200])
        expected = stored_turn["rewritten_query"] if stored_turn["rewrite_applied"] else stored_turn["raw_query"]
        check(tile2["title"] == expected[:120], "a pinned follow-up is titled by its rewrite, not 'And the fewest?'",
              f"title={tile2['title']!r} expected={expected!r}")
        check(tile2["layout"] is None and tile2["viz"] is None, "no layout or viz asked for: both left to the client")

        status, body = call("POST", "/tiles", body={"session_id": session_id, "turn_index": turn1,
                                                    "dashboard_id": other_dataset})
        check(status == 422 and "olist" in body.get("detail", ""),
              "a public answer cannot go on an olist dashboard (422)", f"{status} {body}")
        check(call("POST", "/tiles", body={"session_id": session_id, "turn_index": turn1,
                                           "dashboard_id": board, "viz": "pie"})[0] == 422,
              "an unknown viz is refused at pin (422)")
        check(call("POST", "/tiles", body={"session_id": session_id, "turn_index": turn1, "dashboard_id": board,
                                           "layout": {"x": 10, "y": 0, "w": 6, "h": 8}})[0] == 422,
              "a layout past the grid edge is refused at pin (422)")

        _, listed = call("GET", f"/tiles?dashboard_id={board}")
        ids = {t["tile_id"] for t in listed["tiles"]}
        check(ids == {tile1["tile_id"], tile2["tile_id"]}, "listing by dashboard returns exactly its tiles",
              str(ids))

        section("Layout is all or nothing")
        good = [{"tile_id": tile1["tile_id"], "x": 6, "y": 0, "w": 6, "h": 8},
                {"tile_id": tile2["tile_id"], "x": 0, "y": 0, "w": 6, "h": 8}]
        status, body = call("PUT", f"/dashboards/{board}/layout", body={"items": good})
        check(status == 200 and body.get("updated") == 2, "a valid layout is stored", f"{status} {body}")
        mixed = [{"tile_id": tile1["tile_id"], "x": 0, "y": 20, "w": 3, "h": 4},
                 {"tile_id": str(uuid.uuid4()), "x": 3, "y": 20, "w": 3, "h": 4}]
        check(call("PUT", f"/dashboards/{board}/layout", body={"items": mixed})[0] == 404,
              "a layout naming an unknown tile is refused (404)")
        _, one = call("GET", f"/tiles/{tile1['tile_id']}")
        check(one["tile"]["layout"] == {"x": 6, "y": 0, "w": 6, "h": 8},
              "... and changed nothing, not even the valid item", str(one["tile"]["layout"]))
        check(call("PUT", f"/dashboards/{board}/layout",
                   body={"items": [{"tile_id": tile1["tile_id"], "x": 8, "y": 0, "w": 6, "h": 8}]})[0] == 422,
              "a layout past the grid edge is refused (422)")
        check(call("PUT", f"/dashboards/{second_board}/layout", body={"items": good})[0] == 404,
              "tiles cannot be laid out on a dashboard they are not on (404)")

        section("Viz and moves")
        status, body = call("PATCH", f"/tiles/{tile1['tile_id']}", body={"viz": "donut"})
        check(status == 200 and body["tile"]["viz"] == "donut", "viz can be changed")
        status, body = call("PATCH", f"/tiles/{tile1['tile_id']}", body={"viz": "auto"})
        check(status == 200 and body["tile"]["viz"] is None, "'auto' clears it")
        check(call("PATCH", f"/tiles/{tile1['tile_id']}", body={"viz": "pie"})[0] == 422, "an unknown viz is refused")
        check(call("PATCH", f"/tiles/{tile1['tile_id']}", token=TOKEN_B, body={"viz": "kpi"})[0] == 404,
              "another principal cannot change it (404)")
        check(call("PATCH", f"/tiles/{tile1['tile_id']}", body={"dashboard_id": other_dataset})[0] == 422,
              "a tile cannot move to another dataset's dashboard (422)")
        status, body = call("PATCH", f"/tiles/{tile1['tile_id']}", body={"dashboard_id": second_board})
        check(status == 200 and body["tile"]["dashboard_id"] == second_board and body["tile"]["layout"] is None,
              "a tile moves to another dashboard and is placed afresh", json.dumps(body)[:300])

        section("Deleting a conversation")
        before_audit = psql("SELECT count(*) FROM session_state.audit_log WHERE detail LIKE 'history:delete%'")
        check(call("DELETE", f"/sessions/{session_id}", token=TOKEN_B)[0] == 404,
              "another principal cannot delete it (404)")
        _, still = call("GET", f"/sessions/{session_id}")
        check(len(still.get("turns", [])) == 2, "... and its turns are untouched", str(len(still.get("turns", []))))
        status, body = call("DELETE", f"/sessions/{session_id}")
        check(status == 200 and body.get("deleted_turns") == 2, "the owner deletes it", f"{status} {body}")
        _, gone = call("GET", f"/sessions/{session_id}")
        check(gone.get("turns") == [], "its turns are gone", str(gone)[:200])
        rows_left = psql("SELECT count(*) FROM session_state.turn_rows r JOIN session_state.conversation_turns t "
                         f"USING (turn_id) WHERE t.session_id = '{session_id}'")
        check(rows_left == "0", "its stored rows are gone", rows_left)
        after_audit = psql("SELECT count(*) FROM session_state.audit_log WHERE detail LIKE 'history:delete%'")
        check(int(after_audit) >= int(before_audit or 0) + 2, "both delete attempts are in the audit log",
              f"{before_audit} -> {after_audit}")
        status, body = call("POST", f"/tiles/{tile2['tile_id']}/refresh")
        check(status == 200 and body["tile"]["state"] == "ok", "a tile pinned from it still refreshes",
              f"{status} {json.dumps(body)[:200]}")
        check(call("DELETE", f"/sessions/{session_id}")[0] == 404, "deleting it again is a 404")

        section("Deleting a dashboard removes its tiles")
        check(call("DELETE", f"/dashboards/{board}")[0] == 200, "delete the dashboard")
        created.remove(board)
        check(call("GET", f"/tiles/{tile2['tile_id']}")[0] == 404, "its tile is gone with it")
        check(call("GET", f"/tiles/{tile1['tile_id']}")[0] == 200, "a tile moved off it survives")
    finally:
        for dashboard_id in created:
            call("DELETE", f"/dashboards/{dashboard_id}")
        call("DELETE", f"/sessions/{session_id}")

    passed = sum(1 for ok, _ in results if ok)
    failed = len(results) - passed
    print(f"\n{'─' * 20} Summary {'─' * 20}\n  PASSED: {passed}\n  FAILED: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
