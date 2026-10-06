#!/usr/bin/env python3
"""
scripts/eval_hybrid.py
Part D v3, the hybrid route end to end (docs/specs/routing_docs_fixes.md §5; T5). The routing set's 24 hybrid
questions (12 whose documents half depends on the database half's result, 12 independent; dev and test) are
asked in full, as a user would, in the routing workspace (SEC database + filings), on three arms:
  - shipped       flags_partd_v3_off.toml           (the shipped router and hybrid path)
  - v3            flags_partd_v3_final_bge.toml     (the evaluated system; hybrid_entities off)
  - v3_entities   flags_partd_v3_final_bge_entities.toml  (the same, hybrid_entities on)
Each answer is scored on two halves, fixed before any run:
  - database half: the judge (paperb_docs' grader endpoint and model) compares the answer with the reference
    SQL result: correct / partly correct / incorrect / missing;
  - documents half: mechanical, the answer cites a passage of the item's filing (title normalised: lowercase,
    letters and digits only).
  Both halves right = database half "correct" and the filing cited. Reported per arm, by dependent /
  independent and split, and the route taken. n is small (24); results are descriptive, with paired
  exact McNemar on "both halves right" for v3_entities vs v3 and v3 vs shipped.

    python3 scripts/eval_hybrid.py ask --arm v3 --port 8093
    python3 scripts/eval_hybrid.py grade
    python3 scripts/eval_hybrid.py report
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
import time
import tomllib
import uuid
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import eval_bird as eb  # noqa: E402
import eval_routing as er  # noqa: E402
import paperb_docs as pd  # noqa: E402

OUT = REPO / "docs" / "eval" / "partd_hybrid.json"                 # grades and the report


def run_path(arm: str) -> Path:
    """One file per arm, so the arms can be asked in parallel."""
    return REPO / "docs" / "eval" / f"partd_hybrid_answers_{arm}.json"
FLAGS = {"shipped": "flags_partd_v3_off.toml", "v3": "flags_partd_v3_final_bge.toml",
         "v3_entities": "flags_partd_v3_final_bge_entities.toml"}
CONTAINERS = {8091: "statesync-orchestrator-paperb", 8092: "statesync-orchestrator-paperb-off",
              8093: "statesync-orchestrator-paperb-route"}
QUERY_TIMEOUT_S = 600       # noqa: literals - a full hybrid answer (SQL agent, search, re-ranker, writer)
GRADER_HALF = (
    "You check one part of an answer about US public companies. You are given a question, the reference result "
    "of the database query that answers the question's numeric part, and a system's answer. Decide whether the "
    "system's answer states that numeric part correctly. A number counts when it agrees within 1% or within "
    "the reference's rounding, in any unit or scale (e.g. 37676000000 = $37.7 billion); a company may be named "
    "or given by ticker. Ignore everything else in the answer, including any discussion of filings. Reply with "
    "one word on the last line: CORRECT (all of it), PARTLY (some of it), INCORRECT (stated but wrong) or "
    "MISSING (not stated, or the system declined).")
VERDICTS = {"CORRECT": "correct", "PARTLY": "partly correct", "INCORRECT": "incorrect", "MISSING": "missing"}


def items() -> list[dict]:
    return [i for i in json.loads(er.SET.read_text())["items"] if i["class"] == "hybrid"]


def norm_title(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def check_flags(arm: str, port: int) -> dict:
    want = tomllib.loads((REPO / "research" / "paperb" / FLAGS[arm]).read_text())["pipeline"]
    got = subprocess.run(["docker", "exec", CONTAINERS[port], "cat", "/app/config/flags.toml"],
                         capture_output=True, text=True, check=True).stdout
    if tomllib.loads(got)["pipeline"] != want:
        raise SystemExit(f"{CONTAINERS[port]} does not run {FLAGS[arm]}")
    # both containers must run the images last built (a container started before a rebuild keeps its old image)
    built = json.loads((REPO / "research" / "paperb" / "partd_images.json").read_text())
    for name, key in ((CONTAINERS[port], "orchestrator_image"), ("statesync-mcp-paperb", "mcp_image")):
        image = subprocess.run(["docker", "inspect", "-f", "{{.Image}}", name], capture_output=True, text=True).stdout.strip()
        if image != built[key]:
            raise SystemExit(f"{name} runs {image[:19]}, not the {key} built at {built['commit']}; restart it")
    return want


def load() -> dict:
    record = json.loads(OUT.read_text()) if OUT.exists() else {"grades": {}}
    record["runs"] = {arm: json.loads(run_path(arm).read_text()) for arm in FLAGS if run_path(arm).exists()}
    return record


def save(record: dict) -> None:
    OUT.write_text(json.dumps({k: v for k, v in record.items() if k != "runs"}, indent=1) + "\n")


def cmd_ask(arm: str, port: int) -> int:
    from api_auth import api_token
    flags = check_flags(arm, port)
    workspace = json.loads(er.SETUP.read_text())["workspace_id"]
    path = run_path(arm)
    run = json.loads(path.read_text()) if path.exists() else ({"flags_file": FLAGS[arm], "pipeline": flags, "port": port,
                                          "images": json.loads((REPO / "research" / "paperb" / "partd_images.json").read_text()),
                                          "answers": {}})
    for it in items():
        if run["answers"].get(it["id"], {}).get("http_status") == 200:
            continue
        t0 = time.monotonic()
        try:
            st, body = pd.api(f"http://127.0.0.1:{port}", "POST", "/query", api_token(),
                              {"query": it["question"], "session_id": str(uuid.uuid4()), "workspace_id": workspace},
                              timeout=QUERY_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - recorded and asked again on resume
            st, body = None, {"detail": f"{type(exc).__name__}: {exc}"[:300]}
        trace = body.get("state_trace") or {}
        run["answers"][it["id"]] = {
            "http_status": st, "detail": body.get("detail"), "answer_text": body.get("answer") or body.get("answer_text"),
            "route": trace.get("route"), "citations": body.get("citations"), "executed_sql": trace.get("executed_sql"),
            "hybrid_entities": trace.get("hybrid_entities"), "front_door": trace.get("front_door"),
            "errors": trace.get("errors"), "ms": round((time.monotonic() - t0) * 1000)}
        path.write_text(json.dumps(run, indent=1) + "\n")
        a = run["answers"][it["id"]]
        print(f"{arm} {it['id']} HTTP {st} route={a['route']} {a['ms'] / 1000:.1f} s", flush=True)
    return 0


def cmd_grade(container: str) -> int:
    record = load()
    by_id = {i["id"]: i for i in items()}
    todo = []
    for arm, run in record["runs"].items():
        for iid, a in run["answers"].items():
            key = f"{arm}|{iid}"
            if a.get("http_status") == 200 and record["grades"].get(key, {}).get("verdict") is None:
                it = by_id[iid]
                todo.append({"id": key, "prompt": f"Question:\n{it['question']}\n\nReference database result:\n"
                                                  f"{it['sql_answer']}\n\nSystem's answer:\n{a.get('answer_text') or '(empty)'}"})
    req = {"endpoint": pd.GRADER_ENDPOINT, "model": pd.GRADER_MODEL, "system": GRADER_HALF,
           "max_tokens": eb.JUDGE_MAX_TOKENS, "spacing_s": 1.0, "items": todo}
    res = subprocess.run(["docker", "exec", "-i", container, "python", "-c", eb._JUDGE_SNIPPET],  # noqa: SLF001
                         input=json.dumps(req), capture_output=True, text=True, timeout=max(600, len(todo) * 120))
    if res.returncode:
        raise SystemExit(res.stderr[-600:])
    for v in json.loads(res.stdout.strip().splitlines()[-1]):
        last = (v.get("text") or "").strip().splitlines()[-1:] or [""]
        word = re.sub(r"[^A-Z]", "", last[0].upper())
        v["verdict"] = VERDICTS.get(word)
        record["grades"][v["id"]] = v
    save(record)
    print(f"{len(todo)} graded; unparsed: {[k for k, v in record['grades'].items() if v.get('verdict') is None]}")
    return 0


def mcnemar(b: int, c: int) -> float:
    n = b + c
    return 1.0 if n == 0 else min(1.0, 2 * sum(math.comb(n, i) for i in range(min(b, c) + 1)) / 2 ** n)


def cmd_report() -> int:
    record = load()
    by_id = {i["id"]: i for i in items()}
    scored: dict[str, dict[str, dict]] = {}
    for arm, run in record["runs"].items():
        for iid, a in run["answers"].items():
            it = by_id[iid]
            cited = {norm_title(c.get("title", "")) for c in a.get("citations") or [] if isinstance(c, dict)}
            sql = (record["grades"].get(f"{arm}|{iid}") or {}).get("verdict")
            docs = norm_title(it["filing"]) in cited
            scored.setdefault(arm, {})[iid] = {"route": a.get("route"), "sql": sql, "docs": docs,
                                               "both": sql == "correct" and docs, "dependent": it["dependent"],
                                               "split": it["split"], "narrowed": bool(a.get("hybrid_entities"))}
    summary = {}
    for arm, s in scored.items():
        out = {}
        for name, sel in (("all", lambda x: True), ("dependent", lambda x: x["dependent"]),
                          ("independent", lambda x: not x["dependent"]), ("test", lambda x: x["split"] == "test")):
            xs = [x for x in s.values() if sel(x)]
            out[name] = {"n": len(xs), "both": sum(x["both"] for x in xs),
                         "database_correct": sum(x["sql"] == "correct" for x in xs),
                         "database_partly": sum(x["sql"] == "partly correct" for x in xs),
                         "filing_cited": sum(x["docs"] for x in xs), "neither_half": sum(x["sql"] != "correct" and not x["docs"] for x in xs),
                         "routed_hybrid": sum(x["route"] == "hybrid" for x in xs)}
        summary[arm] = out
        print(arm, json.dumps(out))
    for a, b in (("v3_entities", "v3"), ("v3", "shipped")):
        if a in scored and b in scored:
            ids = set(scored[a]) & set(scored[b])
            w = sum(scored[a][i]["both"] and not scored[b][i]["both"] for i in ids)
            l_ = sum(scored[b][i]["both"] and not scored[a][i]["both"] for i in ids)
            summary[f"{a}_vs_{b}"] = {"n": len(ids), "wins": w, "losses": l_, "p_mcnemar": round(mcnemar(w, l_), 4)}
            print(f"{a} vs {b}: both halves right +{w} / -{l_}, exact McNemar p = {mcnemar(w, l_):.4f}")
    record["summary"], record["scored"] = summary, scored
    save(record)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("ask")
    a.add_argument("--arm", choices=sorted(FLAGS), required=True)
    a.add_argument("--port", type=int, choices=sorted(CONTAINERS), required=True)
    g = sub.add_parser("grade")
    g.add_argument("--container", default="statesync-orchestrator-paperb")
    sub.add_parser("report")
    args = ap.parse_args()
    if args.cmd == "ask":
        return cmd_ask(args.arm, args.port)
    return cmd_grade(args.container) if args.cmd == "grade" else cmd_report()


if __name__ == "__main__":
    raise SystemExit(main())
