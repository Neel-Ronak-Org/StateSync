#!/usr/bin/env python3
"""
scripts/eval_routing.py
Part D v3's routing comparison (docs/specs/routing_docs_fixes.md §4) on the labelled routing set
(docs/eval/routing_set.json): one workspace with the SEC database AND the 84 FinanceBench filings, so both
sources hold overlapping facts and routing is a real decision.

  setup                 the workspace, through Part D's API (127.0.0.1:8091): a connection to the SEC demo
                        database (demo/sec_db.sh, schema sec), the cross-check study's business context
                        (docs/specs/sec_crosscheck.md §2, verbatim), the product's own draft of the column notes
                        approved unchanged, and the FinanceBench folder linked. Idempotent by name.
  route --arm A --port P [--split dev|test|all] [--repeat R]
                        every single question and every conversation turn through POST /query with
                        debug_route_only (T4a; the Part D container must run with debug_hooks = "on"); a
                        conversation turn gets its earlier turns as debug_history, with their GOLD standalone
                        questions, routes and answers, so routing is measured apart from answer errors. The arm
                        is whatever the container's flags say (front_door off = shipped, llm, jev); the script
                        checks the container's flags match --arm before it starts.
  report                accuracy against the acceptable routes by class, macro-F1, follow-up detection,
                        agreement between repeats, and calls/latency before the answer work, per arm and split.

    python3 scripts/eval_routing.py setup
    python3 scripts/eval_routing.py route --arm shipped --port 8091 --split dev
    python3 scripts/eval_routing.py report
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import uuid
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import eval_sec_crosscheck as sx  # noqa: E402
import paperb_docs as pd  # noqa: E402

SET = REPO / "docs" / "eval" / "routing_set.json"
SETUP = REPO / "docs" / "eval" / "routing_setup.json"
OUT = REPO / "docs" / "eval" / "routing_runs.json"
WORKSPACE_NAME = "partd-routing-sec-filings"
ARMS = {"shipped": "off", "llm": "llm", "jev": "jev", "jev_rw": "jev_rw", "jev_choice": "jev_choice"}       # arm -> the container's [pipeline] front_door
HISTORY_ROUTE = {"docs": "docs", "sql": "sql", "hybrid": "hybrid", "neither": "neither", "either": "docs"}
DRAFT_POLL_S = 5            # noqa: literals - polling the product's draft job
ROUTE_TIMEOUT_S = 180       # noqa: literals - a routing-only request (one model call at most, plus evidence)
RATE_LIMIT_RETRIES = 6      # noqa: literals - the orchestrator's per-principal limiter (HTTP 429): wait and ask again
RATE_LIMIT_WAIT_S = 20      # noqa: literals - seconds between those attempts


def api(port: int, method: str, path: str, body: dict | None = None, timeout: float = ROUTE_TIMEOUT_S):
    from api_auth import api_token
    return pd.api(f"http://127.0.0.1:{port}", method, path, api_token(), body, timeout=timeout)


def cmd_setup(port: int) -> int:
    record = json.loads(SETUP.read_text()) if SETUP.exists() else {}
    _, listed = api(port, "GET", "/workspaces")
    ws = next((w for w in listed.get("workspaces", []) if w["name"] == WORKSPACE_NAME), None)
    if ws is None:
        st, conn = api(port, "POST", "/library/connections", {**sx.SEC_CONNECTION, "name": "partd-routing-sec"})
        if st != 201:   # noqa: literals - HTTP Created
            raise SystemExit(f"connection refused: HTTP {st} {conn.get('detail')}")
        cid = conn["connection"]["connection_id"]
        st, body = api(port, "PUT", f"/library/connections/{cid}/schema", {"schema_name": "sec"})
        if st != 200:   # noqa: literals - HTTP OK
            raise SystemExit(f"schema refused: HTTP {st} {body.get('detail')}")
        st, body = api(port, "POST", "/workspaces", {"name": WORKSPACE_NAME, "target_schema": cid,
                                                     "business_context": sx.SQL_BUSINESS_CONTEXT})
        if st != 201:   # noqa: literals - HTTP Created
            raise SystemExit(f"workspace refused: HTTP {st} {body.get('detail')}")
        ws = body["workspace"]
        record["connection_id"] = cid
    record["workspace_id"] = ws["workspace_id"]
    record["target_schema"] = ws.get("target_schema")
    if not record.get("draft_approved"):
        st, body = api(port, "POST", f"/workspaces/{ws['workspace_id']}/draft")
        if st != 202:   # noqa: literals - HTTP Accepted
            raise SystemExit(f"draft refused: HTTP {st} {body.get('detail')}")
        while True:
            time.sleep(DRAFT_POLL_S)
            _, status = api(port, "GET", f"/workspaces/{ws['workspace_id']}/draft")
            job = status.get("job") or {}
            if job.get("state") not in ("queued", "running"):
                break
        if job.get("state") != "done":
            raise SystemExit(f"draft ended {job.get('state')}: {job.get('error')}")
        st, body = api(port, "POST", f"/workspaces/{ws['workspace_id']}/approve")   # unchanged
        if st != 200:   # noqa: literals - HTTP OK
            raise SystemExit(f"approve refused: HTTP {st} {body.get('detail')}")
        record["draft_approved"] = {"columns": body.get("approved_columns"), "definitions": body.get("approved_definitions")}
    folder = json.loads(pd.INGEST.read_text())["folder_id"]
    st, body = api(port, "PUT", f"/workspaces/{ws['workspace_id']}/folders", {"folder_ids": [folder]})
    if st != 200:   # noqa: literals - HTTP OK
        raise SystemExit(f"folder link refused: HTTP {st} {body.get('detail')}")
    record.update({"folder_id": folder, "set_up_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    SETUP.write_text(json.dumps(record, indent=1) + "\n")
    print(json.dumps(record, indent=1))
    return 0


def container_front_door(port: int) -> str:
    name = {8091: "statesync-orchestrator-paperb", 8092: "statesync-orchestrator-paperb-off",
            8093: "statesync-orchestrator-paperb-route"}.get(port)
    out = subprocess.run(["docker", "exec", name, "sh", "-c", "grep -E '^(front_door|debug_hooks)' /app/config/flags.toml"],
                         capture_output=True, text=True).stdout
    vals = {line.split("=")[0].strip(): line.split("=")[1].split("#")[0].strip().strip('"') for line in out.splitlines()}
    if vals.get("debug_hooks") != "on":
        raise SystemExit(f"{name} runs with debug_hooks={vals.get('debug_hooks')!r}; routing runs need \"on\"")
    return vals.get("front_door", "off")


def route_one(port: int, workspace: str, question: str, history: list[dict] | None) -> dict:
    body = {"query": question, "session_id": str(uuid.uuid4()), "workspace_id": workspace, "debug_route_only": True}
    if history is not None:
        body["debug_history"] = history
    t0 = time.monotonic()
    for attempt in range(RATE_LIMIT_RETRIES + 1):
        try:
            st, resp = api(port, "POST", "/query", body)
        except Exception as exc:  # noqa: BLE001 - a hung or dropped request is recorded and asked again on resume
            return {"http_status": None, "detail": f"{type(exc).__name__}: {exc}"[:300], "ms": round((time.monotonic() - t0) * 1000)}
        if st != 429 or attempt == RATE_LIMIT_RETRIES:   # noqa: literals - HTTP Too Many Requests (the orchestrator's own limiter)
            break
        time.sleep(RATE_LIMIT_WAIT_S)
        t0 = time.monotonic()
    trace = resp.get("state_trace") or {}
    return {"http_status": st, "detail": resp.get("detail"), "route": trace.get("route"),
            "out_of_scope": trace.get("out_of_scope"), "normalized_query": trace.get("normalized_query"),
            "front_door": trace.get("front_door"), "metrics": trace.get("metrics"),
            "ms": round((time.monotonic() - t0) * 1000)}


def done(rec: dict | None) -> bool:
    """Answered, with a database card (a record made while the database was down is asked again)."""
    return bool(rec) and rec.get("http_status") == 200 and ((rec.get("front_door") or {}).get("card_status")) != "unavailable"


def card_guard(rec: dict) -> None:
    """Stop the run when the front door had no database card: the database it describes is down (on 2026-10-06
    the SEC database container stayed stopped after a Docker restart, and every front door decided blind until
    this guard). The record stays, and done() makes a resume ask it again."""
    if ((rec.get("front_door") or {}).get("card_status")) == "unavailable":
        raise SystemExit("the front door had no database card (is statesync-sec-db running?); stopping")


def cmd_route(arm: str, port: int, split: str, repeat: int, history_mode: str = "gold", tag: str = "") -> int:
    """history_mode "gold": earlier turns are given as their gold standalone questions (what a rewriter would
    have produced); "raw": as the user typed them, which is all a pipeline that rewrites nothing ever has
    (docs/specs/partd_tasks/T4c_jev_choice_and_context.md). Raw runs are stored as "<arm>@raw|split|rep"."""
    if container_front_door(port) != ARMS[arm]:
        raise SystemExit(f"the container on {port} does not run arm {arm!r}")
    rs = json.loads(SET.read_text())
    workspace = json.loads(SETUP.read_text())["workspace_id"]
    record = json.loads(OUT.read_text()) if OUT.exists() else {"runs": {}}
    images = json.loads((REPO / "research" / "paperb" / "partd_images.json").read_text())
    for rep in range(repeat):
        label = (arm if history_mode == "gold" else f"{arm}@raw") + (f"+{tag}" if tag else "")   # tag: a variant of the arm
        key = f"{label}|{split}|{rep}"
        run = record["runs"].setdefault(key, {"arm": label, "history": history_mode, "split": split, "repeat": rep, "images": images, "items": {}, "turns": {}})
        for it in rs["items"]:
            if (split != "all" and it["split"] != split) or done(run["items"].get(it["id"])):
                continue
            run["items"][it["id"]] = route_one(port, workspace, it["question"], None)
            OUT.write_text(json.dumps(record, indent=1) + "\n")
            card_guard(run["items"][it["id"]])
        for conv in rs["conversations"]:
            if split != "all" and conv["split"] != split:
                continue
            for t in conv["turns"]:
                tid = f"{conv['id']}|{t['turn']}"
                if done(run["turns"].get(tid)):
                    continue
                history = [{"question": p["standalone"] if history_mode == "gold" else p["question"], "route": HISTORY_ROUTE[p["class"]],
                            "answered": p["class"] != "neither", "answer": p.get("gold_answer") or ""}
                           for p in conv["turns"] if p["turn"] < t["turn"]]
                run["turns"][tid] = route_one(port, workspace, t["question"], history)
                OUT.write_text(json.dumps(record, indent=1) + "\n")
                card_guard(run["turns"][tid])
        print(f"{key}: {len(run['items'])} questions, {len(run['turns'])} turns", flush=True)
    return 0


def predicted(rec: dict) -> str | None:
    if rec.get("http_status") != 200:   # noqa: literals - HTTP OK
        return None
    return "neither" if rec.get("out_of_scope") else rec.get("route")


def cmd_report() -> int:
    rs = json.loads(SET.read_text())
    items = {i["id"]: i for i in rs["items"]}
    turns = {f"{c['id']}|{t['turn']}": t for c in rs["conversations"] for t in c["turns"]}
    record = json.loads(OUT.read_text())
    summary = {}
    for key, run in sorted(record["runs"].items()):
        by_class = defaultdict(lambda: [0, 0])
        confusion = Counter()
        covering = over = 0
        for iid, rec in run["items"].items():
            gold = items[iid]
            p = predicted(rec)
            ok = p in gold["acceptable"]
            # "covers": the route runs every source the question needs (hybrid runs both), so it can be
            # answered, at extra cost when it runs a source it does not need ("over").
            covers = ok or (p == "hybrid" and gold["class"] in ("docs", "either", "sql"))
            covering += covers
            over += (p == "hybrid" and gold["class"] in ("docs", "either", "sql"))
            by_class[gold["class"]][0] += ok
            by_class[gold["class"]][1] += 1
            confusion[(gold["class"], p)] += 1
        n = sum(v[1] for v in by_class.values())
        acc = sum(v[0] for v in by_class.values()) / n if n else None
        macro = sum(v[0] / v[1] for v in by_class.values()) / len(by_class) if by_class else None
        t_ok = t_n = dep_ok = dep_n = 0
        for tid, rec in run["turns"].items():
            gold = turns[tid]
            t_n += 1
            t_ok += predicted(rec) in gold["acceptable"]
            fd = rec.get("front_door") or {}
            if "uses_history" in fd:
                dep_n += 1
                dep_ok += bool(fd["uses_history"]) == gold["depends_on_history"]
        lat = sorted(r["ms"] for r in run["items"].values() if r.get("ms") is not None)
        calls = [((r.get("metrics") or {}).get("llm_calls") or 0) for r in run["items"].values()]
        summary[key] = {"questions": n, "route_accuracy": round(acc, 3) if acc is not None else None,
                        "covers_needed_source": round(covering / n, 3) if n else None, "over_routed_to_hybrid": over,
                        "macro_class_accuracy": round(macro, 3) if macro is not None else None,
                        "by_class": {c: f"{v[0]}/{v[1]}" for c, v in sorted(by_class.items())},
                        "turns": t_n, "turn_route_accuracy": round(t_ok / t_n, 3) if t_n else None,
                        "history_detection": f"{dep_ok}/{dep_n}" if dep_n else None,
                        "p50_ms": lat[len(lat) // 2] if lat else None, "mean_llm_calls": round(sum(calls) / len(calls), 2) if calls else None,
                        "errors": sum(1 for r in list(run["items"].values()) + list(run["turns"].values()) if r.get("http_status") != 200),
                        "confusion": {f"{g}->{p}": c for (g, p), c in sorted(confusion.items(), key=str)}}
        print(key, json.dumps({k: v for k, v in summary[key].items() if k != "confusion"}))
    # agreement between repeats of the same arm and split
    groups = defaultdict(list)
    for key, run in record["runs"].items():
        groups[(run["arm"], run["split"])].append(run)
    for (arm, split), runs in groups.items():
        if len(runs) > 1:
            a, b = runs[0]["items"], runs[1]["items"]
            ids = sorted(set(a) & set(b))
            agree = sum(predicted(a[i]) == predicted(b[i]) for i in ids)
            summary[f"{arm}|{split}|agreement"] = f"{agree}/{len(ids)}"
            print(f"{arm}|{split} agreement between repeats: {agree}/{len(ids)}")
    record["summary"] = summary
    OUT.write_text(json.dumps(record, indent=1) + "\n")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup")
    s.add_argument("--port", type=int, default=8091)
    r = sub.add_parser("route")
    r.add_argument("--arm", choices=sorted(ARMS), required=True)
    r.add_argument("--port", type=int, required=True)
    r.add_argument("--split", choices=["dev", "test", "all", "r2"], default="dev")
    r.add_argument("--repeat", type=int, default=1)
    r.add_argument("--history", choices=["gold", "raw"], default="gold")
    r.add_argument("--tag", default="", help="a variant of the arm (e.g. a mounted wording), stored as <arm>+<tag>")
    sub.add_parser("report")
    ap.add_argument("--set", choices=["routing", "r2"], default="routing",
                    help="r2: rerun R2's fresh set (docs/eval/routing_set_r2.json, split r2) -> docs/eval/routing_runs_r2.json")
    args = ap.parse_args()
    if args.set == "r2":
        global SET, OUT
        SET, OUT = REPO / "docs" / "eval" / "routing_set_r2.json", REPO / "docs" / "eval" / "routing_runs_r2.json"
    if args.cmd == "setup":
        return cmd_setup(args.port)
    if args.cmd == "route":
        return cmd_route(args.arm, args.port, args.split, args.repeat, args.history, args.tag)
    return cmd_report()


if __name__ == "__main__":
    raise SystemExit(main())
