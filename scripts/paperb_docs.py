#!/usr/bin/env python3
"""
scripts/paperb_docs.py
Paper B, Part D (docs/research/detect_then_fallback_study.md v2, §4): FinanceBench answers through the full
pipeline, with the re-ranker off and on, graded against FinanceBench's answers.

  ask --label dense|bge   the 150 questions through POST /query, in a workspace linked only to the RAG v3
                          FinanceBench folder (financebench_ingest_paragraph.json), dataset "olist" as the
                          retrieval harness. Refuses to run unless the orchestrator's live config has
                          [pipeline] rerank = off (dense) or bge (bge), the answer writer and light roles on
                          Cheaper Inference, and every other [pipeline] flag as committed. Resumable; the
                          workspace is deleted at the end. An answer whose trace shows a model call that
                          failed on the network (TRANSPORT_ERRORS: the product never saw the question
                          through) is moved to `transport_failures` and asked again on the next run
                          (protocol change 7). Each answer records when it was asked; for bge, the
                          re-ranker's and the MCP's logs over that window give its re-ranked searches,
                          their milliseconds and any re-ranker failure (the product then silently keeps
                          the dense order), and an answer with a failure is moved to `rerank_failures`
                          and asked again (change 8).
  grade                   Gemini 3.8 Flash (Zed) grades all 300 answers in one shuffled order (seed
                          20261005), blind to the configuration, with the fixed GRADER prompt below.
  rerank-check            recompute each D-bge answer's re-ranked searches and failures from its window
                          (change 8) and list document answers with none on record.
  owner-sheet             30 answers at random from the 300 for the owner's blind check (no verdict, no
                          configuration); the key is kept in a separate file.
  analyze                 accuracy per configuration and question type, exact McNemar (two-sided),
                          bootstrap interval, refusals, latency, tokens, and accuracy split by whether a
                          passage given to the answer writer is on a gold page (QD2).

    python3 scripts/paperb_docs.py ask --label dense
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import statistics
import subprocess
import sys
import time
import tomllib
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import eval_bird as eb  # noqa: E402

REPO = eb.REPO
EVAL = eb.EVAL
QUESTIONS = REPO / "data" / "external" / "financebench" / "data" / "financebench_open_source.jsonl"
INGEST = EVAL / "financebench_ingest_paragraph.json"
LABELS = {"dense": "off", "bge": "bge"}
# Part D v3 (docs/specs/routing_docs_fixes.md): the fixed system (front door, every fix on), re-ranker off
# and bge. Each label runs with exactly its generated flags file (research/paperb/make_flags_v3.py).
V3_FLAGS = {"v3_dense": REPO / "research" / "paperb" / "flags_partd_v3_final_dense.toml",
            "v3_bge": REPO / "research" / "paperb" / "flags_partd_v3_final_bge.toml"}
ALL_LABELS = sorted(LABELS) + sorted(V3_FLAGS)
WORKSPACE_DATASET = "olist"     # as scripts/eval_retrieval.py and eval_financebench.py
PAGE_OFFSET = 1                 # FinanceBench pages count from 0, StateSync's from 1
QUERY_TIMEOUT_S = 300           # noqa: literals - two re-ranked searches plus the answer writer
TRANSPORT_ERRORS = frozenset({"APIConnectionError", "APITimeoutError"})   # network, not the product (change 7)
MCP_CONTAINER = "statesync-mcp-1"
# The MCP calls the re-ranker on the Mac's GPU (scripts/run_reranker_host.sh; RERANKER_URL in
# docker-compose.override.yml), not the reranker container; it logs here, in local time.
RERANKER_LOG = Path.home() / "Library" / "Logs" / "statesync-reranker.log"
_RERANKED = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),(\d{3}) INFO reranked (\d+) passages with (\w+) in (\d+) ms")
_RERANK_FAILED = re.compile(r"rerank with \w+ failed")                     # mcp/tools/vector_tools.py _rerank
SEED = 20261005
RESAMPLES = 2000                # noqa: literals - pre-registered
OWNER_SAMPLE = 30               # noqa: literals - pre-registered human check
GRADER_ENDPOINT, GRADER_MODEL = "local_router", "zed-hosted/gemini-3.8-flash"
GRADER = (
    "You grade answers to questions about companies' SEC filings. You are given the question, the reference "
    "answer, the reference justification and a system's answer. Decide whether the system's answer is correct. "
    "Rules: a number is correct when it agrees with the reference within 1%, or within the rounding the reference "
    "shows; the same value in another unit or scale (1.5 billion and 1,500 million) is fine; extra correct detail is "
    "fine; an answer that gives a different value, contradicts the reference or answers a different question is "
    "incorrect; an answer that gets only part of a multi-part reference right is partly correct; an answer that "
    "declines, or says the information is not available, is refused. Reply with JSON only: "
    "{\"verdict\": \"correct\" | \"partly correct\" | \"incorrect\" | \"refused\", \"reason\": \"<one sentence>\"}.")
VERDICTS = ("correct", "partly correct", "incorrect", "refused")
COMMITTED_FLAGS = REPO / "config" / "flags.toml"
CONTAINER = "statesync-orchestrator-paperb"   # Part D's own orchestrator (research/paperb/partd_container.sh)
API_BASE = "http://127.0.0.1:8091"
EXPECTED_ROLES = {"synthesizer": "cheaperinference:gpt-oss-120b", "sql": "cheaperinference:gpt-oss-120b",
                  "normalizer": "cheaperinference:qwen-3-8-27b", "supervisor": "cheaperinference:qwen-3-8-27b",
                  "rewriter": "cheaperinference:qwen-3-8-27b", "decomposer": "cheaperinference:qwen-3-8-27b"}


def questions() -> list[dict]:
    return [json.loads(line) for line in QUESTIONS.read_text().splitlines() if line.strip()]


def answers_path(label: str) -> Path:
    return EVAL / f"paperb_docs_answers_{label}.json"


def live_flags(container: str) -> dict[str, Any]:
    out = subprocess.run(["docker", "exec", container, "python", "-c",
                          "import json, tomllib; print(json.dumps(tomllib.load(open('/app/config/flags.toml', 'rb'))))"],
                         capture_output=True, text=True, timeout=60)
    if out.returncode:
        raise RuntimeError(f"could not read flags inside {container}: {out.stderr[-300:]}")
    started = subprocess.run(["docker", "inspect", "-f", "{{.State.StartedAt}}", container],
                             capture_output=True, text=True, timeout=30).stdout.strip()
    return {"flags": json.loads(out.stdout), "started": started}


def check_config(label: str, container: str = CONTAINER) -> dict[str, Any]:
    """The run's configuration, refused unless it is Part D's (v2 §4); for a v3 label, unless the live
    [pipeline] flags are exactly its generated flags file's."""
    live = live_flags(container)
    f = live["flags"]
    if label in V3_FLAGS:
        want = tomllib.loads(V3_FLAGS[label].read_text())
        problems = [f"[pipeline] {k} is {f['pipeline'].get(k)!r}, expected {v!r}" for k, v in want["pipeline"].items()
                    if f["pipeline"].get(k) != v]
        problems += [f"[pipeline] {k} is set but not expected" for k in f["pipeline"] if k not in want["pipeline"]]
        if f["llm"].get("provider") != "openai_compat":
            problems.append(f"provider is {f['llm'].get('provider')!r}")
        roles = f["llm"].get("models", {}).get("openai_compat", {})
        problems += [f"{r} is {roles.get(r)!r}, not {w!r}" for r, w in EXPECTED_ROLES.items() if roles.get(r) != w]
        if problems:
            raise RuntimeError(f"not {label}'s configuration: " + "; ".join(problems))
        return {"container": container, "pipeline": f["pipeline"], "roles": roles, "orchestrator_started": live["started"],
                "flags_file": str(V3_FLAGS[label].relative_to(REPO))}
    committed = tomllib.loads(subprocess.run(["git", "-C", str(REPO), "show", "HEAD:config/flags.toml"],
                                             capture_output=True, text=True, check=True).stdout)
    problems = []
    if f["llm"].get("provider") != "openai_compat":
        problems.append(f"provider is {f['llm'].get('provider')!r}")
    roles = f["llm"].get("models", {}).get("openai_compat", {})
    problems += [f"{r} is {roles.get(r)!r}, not {want!r}" for r, want in EXPECTED_ROLES.items() if roles.get(r) != want]
    if f["pipeline"].get("rerank") != LABELS[label]:
        problems.append(f"rerank is {f['pipeline'].get('rerank')!r}, not {LABELS[label]!r}")
    for k, v in committed["pipeline"].items():
        if k != "rerank" and f["pipeline"].get(k) != v:
            problems.append(f"[pipeline] {k} is {f['pipeline'].get(k)!r}, committed {v!r}")
    if problems:
        raise RuntimeError("not Part D's configuration: " + "; ".join(problems))
    return {"container": container, "pipeline": f["pipeline"], "roles": roles, "orchestrator_started": live["started"],
            "temperature_omitted_for": f["llm"].get("endpoints", {}).get("cheaperinference", {}).get("omit_temperature", [])}


def api(base: str, method: str, path: str, token: str, body: dict | None = None,
        timeout: float = 120) -> tuple[int, dict]:   # noqa: literals - default HTTP timeout
    import urllib.error
    import urllib.request
    req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except json.JSONDecodeError:
            return exc.code, {}


def transport_failure(rec: dict) -> bool:
    """A model call in the trace failed on the network (the answer is the error, not the product's)."""
    return any(c.get("error") in TRANSPORT_ERRORS for c in rec.get("calls") or [])


def rerank_failure(rec: dict) -> bool:
    """A re-ranked search failed while this answer was asked, so it searched in the dense order (change 8)."""
    return bool((rec.get("rerank") or {}).get("failed"))


def rerank_log(since: float, until: float) -> dict:
    """The re-ranked searches between two Unix times, from the re-ranker's log and the MCP's (failures): the
    answer trace does not carry them, and Part D asks one question at a time, so the window is one answer's."""
    ms = []
    for line in RERANKER_LOG.read_text().splitlines():
        m = _RERANKED.match(line)
        if m and since <= time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")) + int(m.group(2)) / 1000 <= until:
            ms.append(int(m.group(5)))
    res = subprocess.run(["docker", "logs", MCP_CONTAINER, "--since", f"{since:.3f}", "--until", f"{until:.3f}"],
                         capture_output=True, text=True, timeout=60)   # noqa: literals - docker logs timeout
    failed = sum(1 for line in (res.stdout + res.stderr).splitlines() if _RERANK_FAILED.search(line))
    return {"searches": len(ms), "ms": ms, "failed": failed}


def cmd_rerank_check() -> int:
    """Recompute every D-bge answer's re-rank record from its window (change 8), and list document answers
    with no re-ranked search on record."""
    out = answers_path("bge")
    record = json.loads(out.read_text())
    for rec in record["answers"].values():
        rec["rerank"] = rerank_log(rec["asked_at"], rec["finished_at"])
    out.write_text(json.dumps(record, indent=1) + "\n")
    ans = record["answers"]
    failed = [f for f, r in ans.items() if rerank_failure(r)]
    unseen = [f for f, r in ans.items() if r["route"] in ("docs", "hybrid") and not r["rerank"]["searches"]]
    ms = [m for r in ans.values() for m in r["rerank"]["ms"]]
    print(f"{len(ans)} answers; {len(ms)} re-ranked searches; failures in {len(failed)} {failed}; "
          f"document answers with no re-ranked search: {unseen}")
    return 0


def cmd_ask(label: str, spacing_s: float, limit: int | None, container: str = CONTAINER, base: str = API_BASE) -> int:
    from api_auth import api_token
    config = check_config(label, container)
    ingest = json.loads(INGEST.read_text())
    out = answers_path(label)
    record = json.loads(out.read_text()) if out.exists() else {"label": label, "config": config, "answers": {}}
    record.setdefault("configs_seen", []).append(config)
    moved = [fid for fid, rec in record["answers"].items() if transport_failure(rec) or rerank_failure(rec)]
    for fid in moved:                     # asked again below (protocol changes 7 and 8); the failed answer is kept
        why = "transport_failures" if transport_failure(record["answers"][fid]) else "rerank_failures"
        record.setdefault(why, {}).setdefault(fid, []).append(record["answers"].pop(fid))
    if moved:
        print(f"asking again after a network or re-ranker failure: {', '.join(moved)}", flush=True)
    token = api_token()
    status, body = api(base, "POST", "/workspaces", token, {"name": f"paperb-docs-{label}-{int(time.time())}",
                                                           "target_schema": WORKSPACE_DATASET})
    if status != 201:   # noqa: literals - HTTP Created
        raise RuntimeError(f"could not create the workspace: {status} {body}")
    workspace = body["workspace"]["workspace_id"]
    asked = 0
    try:
        status, body = api(base, "PUT", f"/workspaces/{workspace}/folders", token, {"folder_ids": [ingest["folder_id"]]})
        if status != 200:   # noqa: literals - HTTP OK
            raise RuntimeError(f"could not link the folder: {status} {body}")
        for q in questions():
            fid = q["financebench_id"]
            if fid in record["answers"] or (limit is not None and asked >= limit):
                continue
            t0 = time.monotonic()
            asked_at = time.time()
            st, body = api(base, "POST", "/query", token, {"query": q["question"], "session_id": str(uuid.uuid4()),
                                                          "workspace_id": workspace}, timeout=QUERY_TIMEOUT_S)
            trace = body.get("state_trace") or {}
            rec = {"http_status": st, "answer_text": body.get("answer_text"), "detail": body.get("detail"),
                   "route": trace.get("route"), "citations": body.get("citations"),
                   "retrievals": [{"provenance": r.get("provenance"), "chars": len(r.get("content") or "")}
                                  for r in trace.get("retrievals") or []],
                   "calls": trace.get("calls"), "metrics": trace.get("metrics"), "caveats": trace.get("caveats"),
                   "latency_ms": body.get("latency_ms") or round((time.monotonic() - t0) * 1000),
                   "asked_at": round(asked_at, 3), "finished_at": round(time.time(), 3)}
            if label == "bge" or (label in V3_FLAGS and config["pipeline"].get("rerank") == "bge"):
                rec["rerank"] = rerank_log(rec["asked_at"], rec["finished_at"])
            asked += 1
            if st == 200:   # noqa: literals - HTTP OK
                record["answers"][fid] = rec
            else:
                print(f"[{fid}] HTTP {st}: {body.get('detail')}", flush=True)
            out.write_text(json.dumps(record, indent=1) + "\n")
            print(f"[{len(record['answers'])}/150] {fid} HTTP {st} route={rec['route']} "
                  f"{rec['latency_ms'] / 1000:.1f} s" + (f" rerank={rec['rerank']}" if "rerank" in rec else ""),
                  flush=True)
            time.sleep(spacing_s)
    finally:
        st, _ = api(base, "DELETE", f"/workspaces/{workspace}", token)
        print(f"workspace {workspace} deleted: HTTP {st}")
    return 0


def parse_verdict(text: str) -> str | None:
    m = re.search(r'"verdict"\s*:\s*"([a-z ]+)"', text or "", re.I)
    v = m.group(1).strip().lower() if m else None
    return v if v in VERDICTS else None


def grading_items(labels: tuple[str, ...] = tuple(LABELS)) -> list[dict]:
    qs = {q["financebench_id"]: q for q in questions()}
    items = []
    for label in labels:
        for fid, a in json.loads(answers_path(label).read_text())["answers"].items():
            q = qs[fid]
            items.append({"id": f"{label}|{fid}", "prompt": (
                f"Question:\n{q['question']}\n\nReference answer:\n{q['answer']}\n\n"
                f"Reference justification:\n{q.get('justification') or '(none)'}\n\n"
                f"System's answer:\n{a.get('answer_text') or '(empty)'}")})
    random.Random(SEED).shuffle(items)
    return items


def cmd_grade(shards: int, container: str = CONTAINER, preliminary: str | None = None, v3: bool = False) -> int:
    """All answers in one shuffled order (the registered grading), or with `preliminary`, one run's answers
    into their own file: an early look the owner asked for, logged in the protocol, that the registered
    grading never reads."""
    out = EVAL / ("paperb_docs_grades_v3.json" if v3 else "paperb_docs_grades.json" if preliminary is None
                  else f"paperb_docs_grades_preliminary_{preliminary}.json")
    record = json.loads(out.read_text()) if out.exists() else {"endpoint": GRADER_ENDPOINT, "model": GRADER_MODEL,
                                                               "system": GRADER, "seed": SEED, "verdicts": {}}
    labels = tuple(sorted(V3_FLAGS)) if v3 else tuple(LABELS) if preliminary is None else (preliminary,)
    items = [it for it in grading_items(labels) if record["verdicts"].get(it["id"], {}).get("verdict") is None]
    print(f"{len(items)} answers to grade", flush=True)

    def run(batch: list[dict]) -> list[dict]:
        req = {"endpoint": GRADER_ENDPOINT, "model": GRADER_MODEL, "system": GRADER,
               "max_tokens": eb.JUDGE_MAX_TOKENS, "spacing_s": 1.0, "items": batch}
        res = subprocess.run(["docker", "exec", "-i", container, "python", "-c", eb._JUDGE_SNIPPET],  # noqa: SLF001
                             input=json.dumps(req), capture_output=True, text=True, timeout=max(600, len(batch) * 120))
        if res.returncode:
            print(res.stderr[-600:], flush=True)
            return []
        return json.loads(res.stdout.strip().splitlines()[-1])

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(shards) as ex:
        for verdicts in ex.map(run, [items[i::shards] for i in range(shards)]):
            for v in verdicts:
                v["verdict"] = parse_verdict(v.get("text", ""))
                record["verdicts"][v["id"]] = v
            out.write_text(json.dumps(record, indent=1) + "\n")
    ok = sum(v.get("verdict") is not None for v in record["verdicts"].values())
    print(f"{ok} graded; served: {sorted({v.get('served_model') for v in record['verdicts'].values() if v.get('served_model')})}")
    return 0


def cmd_owner_sheet(v3: bool = False) -> int:
    """30 answers for the owner's blind check. With v3: drawn from the three runs the paper compares (the shipped
    system, D-dense, and the fixed system's v3_dense and v3_bge), written to *_v3 files."""
    labels = ("dense", *sorted(V3_FLAGS)) if v3 else tuple(LABELS)
    suffix = "_v3" if v3 else ""
    items = grading_items(labels)
    pick = random.Random(SEED + 1).sample(items, OWNER_SAMPLE)
    key = {}
    sheet = []
    for n, it in enumerate(pick, 1):
        token = f"D{n:02d}"
        key[token] = it["id"]
        sheet.append({"item": token, "text": it["prompt"], "owner_verdict": None, "note": ""})
    (EVAL / f"paperb_docs_owner_sheet{suffix}.json").write_text(json.dumps({
        "instructions": "For each item, read the question, the reference answer and justification, and the system's "
                        "answer. Set owner_verdict to one of: correct, partly correct, incorrect, refused. A number "
                        "counts when it is within 1% of the reference or within its rounding. Do not look at any "
                        "grader output first.", "items": sheet}, indent=1) + "\n")
    (EVAL / f"paperb_docs_owner_key{suffix}.json").write_text(json.dumps(key, indent=1) + "\n")
    print(f"wrote {OWNER_SAMPLE} items to paperb_docs_owner_sheet{suffix}.json (key kept apart)")
    return 0


def gold_pages(q: dict) -> set[tuple[str, int]]:
    return {(e["doc_name"], int(e["evidence_page_num"]) + PAGE_OFFSET) for e in q["evidence"]}


def cmd_analyze() -> int:
    qs = {q["financebench_id"]: q for q in questions()}
    ingest = json.loads(INGEST.read_text())
    name_of = {d["document_id"]: d["doc_name"] for d in ingest["documents"] if d.get("document_id")}
    grades = json.loads((EVAL / "paperb_docs_grades.json").read_text())["verdicts"]
    runs = {label: json.loads(answers_path(label).read_text())["answers"] for label in LABELS}
    ids = sorted(set(runs["dense"]) & set(runs["bge"]))
    report: dict[str, Any] = {"questions_in_both": len(ids), "by_config": {}}
    correct = {}
    for label, ans in runs.items():
        v = [grades.get(f"{label}|{fid}", {}).get("verdict") for fid in ids]
        correct[label] = [x == "correct" for x in v]
        lat = sorted(ans[fid]["latency_ms"] for fid in ids)
        on_gold = [bool(gold_pages(qs[fid]) & {(name_of.get((r["provenance"] or {}).get("document_id")),
                                                 (r["provenance"] or {}).get("page")) for r in ans[fid]["retrievals"]})
                   for fid in ids]
        by_type: dict[str, list[bool]] = {}
        for fid, ok in zip(ids, correct[label]):
            by_type.setdefault(qs[fid]["question_type"], []).append(ok)
        report["by_config"][label] = {
            "accuracy": round(statistics.fmean(correct[label]), 4),
            "verdicts": {x: v.count(x) for x in VERDICTS + (None,)},
            "by_type": {t: round(statistics.fmean(o), 4) for t, o in sorted(by_type.items())},
            "routes": {r: [ans[fid]["route"] for fid in ids].count(r) for r in {ans[fid]["route"] for fid in ids}},
            "latency_s": {"p50": round(lat[len(lat) // 2] / 1000, 1), "p90": round(lat[int(0.9 * len(lat))] / 1000, 1)},
            "tokens_per_answer": round(statistics.fmean((ans[fid].get("metrics") or {}).get("tokens_in", 0)
                                                        + (ans[fid].get("metrics") or {}).get("tokens_out", 0) for fid in ids), 1),
            "gold_page_given": round(statistics.fmean(on_gold), 4),
            "accuracy_when_gold_page_given": round(statistics.fmean([c for c, g in zip(correct[label], on_gold) if g] or [0]), 4),
            "accuracy_when_not": round(statistics.fmean([c for c, g in zip(correct[label], on_gold) if not g] or [0]), 4)}
    b_only = sum(b and not d for b, d in zip(correct["bge"], correct["dense"]))
    d_only = sum(d and not b for b, d in zip(correct["bge"], correct["dense"]))
    n = b_only + d_only
    k = min(b_only, d_only)
    p = min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n) if n else 1.0
    rng = random.Random(SEED)
    diffs = [b - d for b, d in zip(correct["bge"], correct["dense"])]
    boots = sorted(statistics.fmean(diffs[rng.randrange(len(diffs))] for _ in diffs) for _ in range(RESAMPLES))
    report["bge_minus_dense"] = {"difference": round(statistics.fmean(diffs), 4), "bge_only": b_only, "dense_only": d_only,
                                 "mcnemar_two_sided_p": round(p, 4),
                                 "ci95": (round(boots[int(0.025 * RESAMPLES)], 4), round(boots[int(0.975 * RESAMPLES)], 4))}
    sheet_p, key_p = EVAL / "paperb_docs_owner_sheet.json", EVAL / "paperb_docs_owner_key.json"
    if sheet_p.exists() and key_p.exists():
        key = json.loads(key_p.read_text())
        owned = [(it["owner_verdict"], grades.get(key[it["item"]], {}).get("verdict"))
                 for it in json.loads(sheet_p.read_text())["items"] if it.get("owner_verdict")]
        if owned:
            report["owner_check"] = {"items": len(owned), "agree": sum(a == b for a, b in owned),
                                     "agree_on_correct_or_not": sum((a == "correct") == (b == "correct") for a, b in owned)}
    (EVAL / "paperb_docs_analysis.json").write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps(report, indent=1))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("ask")
    a.add_argument("--label", choices=ALL_LABELS, required=True)
    a.add_argument("--spacing", type=float, default=1.0)
    a.add_argument("--limit", type=int)
    a.add_argument("--container", default=CONTAINER)
    a.add_argument("--api", default=API_BASE)
    g = sub.add_parser("grade")
    g.add_argument("--shards", type=int, default=3)
    g.add_argument("--container", default=CONTAINER)
    g.add_argument("--preliminary", choices=sorted(LABELS), help="one run only, into its own file (an early look)")
    g.add_argument("--v3", action="store_true", help="the v3 runs (fixed system, re-ranker off and bge), in one shuffled order")
    sub.add_parser("rerank-check")
    os_ = sub.add_parser("owner-sheet")
    os_.add_argument("--v3", action="store_true", help="sample from the shipped run and the two v3 runs")
    sub.add_parser("analyze")
    args = ap.parse_args()
    if args.cmd == "ask":
        return cmd_ask(args.label, args.spacing, args.limit, args.container, args.api)
    if args.cmd == "grade":
        return cmd_grade(args.shards, args.container, args.preliminary, args.v3)
    if args.cmd == "rerank-check":
        return cmd_rerank_check()
    return cmd_owner_sheet(args.v3) if args.cmd == "owner-sheet" else cmd_analyze()


if __name__ == "__main__":
    raise SystemExit(main())
