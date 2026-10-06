#!/usr/bin/env python3
"""
scripts/eval_turns.py
Multi-turn context end to end (docs/specs/partd_tasks/T4d_context_carry.md, F3): the routing set's follow-up turns
(turn 2 and later; 26, of which 24 have a gold answer) are asked in full, as a user would, in the routing workspace
(SEC database + filings). Each turn's earlier turns go in through debug_history AS TYPED, with their gold answers
(what the product would have stored after answering them), the same for every arm:
  - jev_choice_carry   flags_partd_v3_route_jev_choice_carry.toml  (Jev choice, T4d context carry on: no LLM call)
  - jev_choice         flags_partd_v3_route_jev_choice.toml        (Jev choice, no context handling)
  - llm                flags_partd_v3_route_llm.toml               (the LLM front door resolves context in its call)
  - shipped            flags_partd_v3_route_shipped.toml           (normaliser, LLM rewriter, router)
Answers are graded against the gold answer by paperb_docs' grader (same prompt, model and seed), with the turn's
gold standalone question as the question. The two out-of-scope turns have no gold answer; they are reported by
route only. n is small (24); results are descriptive.

    python3 scripts/eval_turns.py ask --arm jev_choice_carry --port 8091
    python3 scripts/eval_turns.py grade
    python3 scripts/eval_turns.py report
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import tomllib
import uuid
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import eval_bird as eb  # noqa: E402
import eval_routing as er  # noqa: E402
import paperb_docs as pd  # noqa: E402

FLAGS = {"jev_choice_carry": "flags_partd_v3_route_jev_choice_carry.toml", "jev_choice": "flags_partd_v3_route_jev_choice.toml",
         "llm": "flags_partd_v3_route_llm.toml", "shipped": "flags_partd_v3_route_shipped.toml"}
CONTAINERS = {8091: "statesync-orchestrator-paperb", 8092: "statesync-orchestrator-paperb-off",
              8093: "statesync-orchestrator-paperb-route"}
OUT = REPO / "docs" / "eval" / "turns_e2e.json"
QUERY_TIMEOUT_S = 600       # noqa: literals - a full answer


def run_path(arm: str) -> Path:
    return REPO / "docs" / "eval" / f"turns_e2e_answers_{arm}.json"


def turns() -> list[tuple[dict, dict]]:
    rs = json.loads(er.SET.read_text())
    return [(c, t) for c in rs["conversations"] for t in c["turns"] if t["turn"] > 1]


def check_flags(arm: str, port: int) -> dict:
    want = tomllib.loads((REPO / "research" / "paperb" / FLAGS[arm]).read_text())["pipeline"]
    got = subprocess.run(["docker", "exec", CONTAINERS[port], "cat", "/app/config/flags.toml"],
                         capture_output=True, text=True, check=True).stdout
    if tomllib.loads(got)["pipeline"] != want:
        raise SystemExit(f"{CONTAINERS[port]} does not run {FLAGS[arm]}")
    built = json.loads((REPO / "research" / "paperb" / "partd_images.json").read_text())
    for name, key in ((CONTAINERS[port], "orchestrator_image"), ("statesync-mcp-paperb", "mcp_image")):
        image = subprocess.run(["docker", "inspect", "-f", "{{.Image}}", name], capture_output=True, text=True).stdout.strip()
        if image != built[key]:
            raise SystemExit(f"{name} is not on the images built at {built['commit']}; restart it")
    return want


def cmd_ask(arm: str, port: int) -> int:
    from api_auth import api_token
    flags = check_flags(arm, port)
    workspace = json.loads(er.SETUP.read_text())["workspace_id"]
    path = run_path(arm)
    run = json.loads(path.read_text()) if path.exists() else {
        "flags_file": FLAGS[arm], "pipeline": flags, "port": port,
        "images": json.loads((REPO / "research" / "paperb" / "partd_images.json").read_text()), "answers": {}}
    for conv, t in turns():
        tid = f"{conv['id']}|{t['turn']}"
        if run["answers"].get(tid, {}).get("http_status") == 200:
            continue
        history = [{"question": p["question"], "route": er.HISTORY_ROUTE[p["class"]], "answered": p["class"] != "neither",
                    "answer": str(p.get("gold_answer") or "")} for p in conv["turns"] if p["turn"] < t["turn"]]
        t0 = time.monotonic()
        try:
            st, body = pd.api(f"http://127.0.0.1:{port}", "POST", "/query", api_token(),
                              {"query": t["question"], "session_id": str(uuid.uuid4()), "workspace_id": workspace,
                               "debug_history": history}, timeout=QUERY_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 - recorded and asked again on resume
            st, body = None, {"detail": f"{type(exc).__name__}: {exc}"[:300]}
        trace = body.get("state_trace") or {}
        fd = trace.get("front_door") or {}
        run["answers"][tid] = {"http_status": st, "detail": body.get("detail"), "answer_text": body.get("answer_text"),
                               "route": trace.get("route"), "out_of_scope": trace.get("out_of_scope"),
                               "normalized_query": trace.get("normalized_query"), "context_carry": fd.get("context_carry"),
                               "citations": body.get("citations"), "metrics": trace.get("metrics"),
                               "ms": round((time.monotonic() - t0) * 1000)}
        if (fd.get("card_status")) == "unavailable":
            raise SystemExit("the front door had no database card (is statesync-sec-db running?); stopping")
        path.write_text(json.dumps(run, indent=1) + "\n")
        print(f"{arm} {tid} HTTP {st} route={run['answers'][tid]['route']}", flush=True)
    return 0


def cmd_grade(container: str) -> int:
    record = json.loads(OUT.read_text()) if OUT.exists() else {"grades": {}}
    gold = {f"{c['id']}|{t['turn']}": t for c, t in turns()}
    todo = []
    for arm in FLAGS:
        if not run_path(arm).exists():
            continue
        for tid, a in json.loads(run_path(arm).read_text())["answers"].items():
            g = gold[tid]
            key = f"{arm}|{tid}"
            if a.get("http_status") == 200 and g.get("gold_answer") and record["grades"].get(key, {}).get("verdict") is None:
                todo.append({"id": key, "prompt": (f"Question:\n{g['standalone']}\n\nReference answer:\n{g['gold_answer']}\n\n"
                                                   f"Reference justification:\n(none)\n\nSystem's answer:\n{a.get('answer_text') or '(empty)'}")})
    req = {"endpoint": pd.GRADER_ENDPOINT, "model": pd.GRADER_MODEL, "system": pd.GRADER,
           "max_tokens": eb.JUDGE_MAX_TOKENS, "spacing_s": 1.0, "items": todo}
    res = subprocess.run(["docker", "exec", "-i", container, "python", "-c", eb._JUDGE_SNIPPET],  # noqa: SLF001
                         input=json.dumps(req), capture_output=True, text=True, timeout=max(600, len(todo) * 120))
    if res.returncode:
        raise SystemExit(res.stderr[-600:])
    for v in json.loads(res.stdout.strip().splitlines()[-1]):
        v["verdict"] = pd.parse_verdict(v.get("text", ""))
        record["grades"][v["id"]] = v
    OUT.write_text(json.dumps(record, indent=1) + "\n")
    print(f"{len(todo)} graded; unparsed: {[k for k, v in record['grades'].items() if v.get('verdict') is None]}")
    return 0


def cmd_report() -> int:
    record = json.loads(OUT.read_text())
    gold = {f"{c['id']}|{t['turn']}": t for c, t in turns()}
    summary = {}
    for arm in FLAGS:
        if not run_path(arm).exists():
            continue
        answers = json.loads(run_path(arm).read_text())["answers"]
        verdicts = Counter((record["grades"].get(f"{arm}|{tid}") or {}).get("verdict") for tid in answers if gold[tid].get("gold_answer"))
        graded = sum(verdicts.values())
        route_ok = sum(er.predicted(a) in gold[tid]["acceptable"] for tid, a in answers.items())
        calls = [((a.get("metrics") or {}).get("llm_calls") or 0) for a in answers.values()]
        summary[arm] = {"turns": len(answers), "graded": graded, "correct": verdicts.get("correct", 0),
                        "partly": verdicts.get("partly correct", 0), "verdicts": dict(verdicts),
                        "route_correct": f"{route_ok}/{len(answers)}",
                        "carried": sum(bool((a.get("context_carry") or {}).get("follow_up")) for a in answers.values()),
                        "mean_llm_calls": round(sum(calls) / len(calls), 2) if calls else None}
        print(arm, json.dumps(summary[arm]))
    record["summary"] = summary
    OUT.write_text(json.dumps(record, indent=1) + "\n")
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
