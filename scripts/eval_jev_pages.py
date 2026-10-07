#!/usr/bin/env python3
"""
scripts/eval_jev_pages.py
Part D, EXPLORATORY (2026-10-07; FinanceBench is the development set): Jev as a passage re-ranker for page finding.
For each FinanceBench question, the 25 passages the documents route's search returned are taken as recorded by
scripts/eval_writer_window.py (docs/eval/partd_writer_window.json), from two runs:
  - financebench|rel_off         dense search, relevance order, no re-ranker;
  - financebench|rel_bge_titles  the evaluated setting: bge re-ranker reading titles, relevance order.
Each passage (its text read from the library database by chunk id, so exactly what the search returned; title,
page and the first 3,000 characters) gets one Jev noul question, "Does this passage contain the information needed
to answer the question?", and the 25 are re-ordered by Jev's P(yes) (ties keep the recorded order). Measured, as
eval_writer_window scores them: right page in the writer's window (the first semantic_top_k = 8), right page
first, right document first; paired exact McNemar against the recorded order.

    python3 scripts/eval_jev_pages.py score [--workers 4]     resumable; writes docs/eval/jev_pages.json
    python3 scripts/eval_jev_pages.py report
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import paperb_docs as pd  # noqa: E402
from eval_writer_window import TENANT  # noqa: E402  (the tenant the recorded searches ran as)

RUNS = REPO / "docs" / "eval" / "partd_writer_window.json"
# --set financebench: the exploratory run (development set); --set pages_heldout: the pre-registered confirmatory
# test (docs/research/jev_pages_confirmatory.md) on docs/eval/page_set.json. Same Jev text, model and method.
SETS = {"financebench": REPO / "docs" / "eval" / "jev_pages.json",
        "pages_heldout": REPO / "docs" / "eval" / "jev_pages_heldout.json"}
SET_NAME = "financebench"
PAGE_SET = REPO / "docs" / "eval" / "page_set.json"


def out_path() -> Path:
    return SETS[SET_NAME]


def configs() -> tuple[str, str]:
    return (f"{SET_NAME}|rel_off", f"{SET_NAME}|rel_bge_titles")


def questions() -> dict[str, dict]:
    """id -> {"question", "gold": {(doc_name, page), ...}, "doc_name"}."""
    if SET_NAME == "pages_heldout":
        return {q["id"]: {"question": q["question"], "gold": {(q["doc_name"], q["page"])}, "doc_name": q["doc_name"]}
                for q in json.loads(PAGE_SET.read_text())["items"]}
    return {q["financebench_id"]: {"question": q["question"], "gold": pd.gold_pages(q), "doc_name": q["doc_name"]}
            for q in pd.questions()}
URL = "https://opencode.ai/zen/v1/systemone"
MODEL = "jev-1.13"
INSTRUCTIONS = "Does this passage contain the information needed to answer the question?"
TEXT_CHARS = 3000           # noqa: literals - characters of each passage given to Jev
RETRIES = 4                 # noqa: literals - 429/5xx back-off attempts
SAVE_EVERY = 100            # noqa: literals - scores between saves
USD_PER_M_INPUT = 0.042     # noqa: literals - OpenCode Zen's jev-1.13 input price (2026-10-06)

_FETCH = r"""
import json, sys, psycopg
from db import _build_dsn
req = json.load(sys.stdin)
ids = req["ids"]
with psycopg.connect(_build_dsn()) as conn:          # the read-only role, as docs.get_passage reads chunks
    conn.execute("SELECT set_config('statesync.tenant_id', %s, false)", (req["tenant"],))   # row-level security
    rows = conn.execute("SELECT c.chunk_id, d.title, c.page, c.content_md FROM workspace_docs.chunks c "
                        "JOIN workspace_docs.documents d ON d.document_id = c.document_id WHERE c.chunk_id = ANY(%s)",
                        (ids,)).fetchall()
print(json.dumps({r[0]: {"title": r[1], "page": r[2], "text": r[3]} for r in rows}))
"""


def load() -> dict:
    return json.loads(out_path().read_text()) if out_path().exists() else {"model": MODEL, "instructions": INSTRUCTIONS,
                                                             "scores": {}, "cost": 0.0}


def cmd_score(workers: int) -> int:
    runs = json.loads(RUNS.read_text())["runs"]
    qs = questions()
    pairs = {(qid, p["chunk_id"]) for c in configs() for qid, r in runs[c]["results"].items() if not r.get("error")
             for p in r["passages"]}
    record = load()
    todo = sorted(p for p in pairs if f"{p[0]}|{p[1]}" not in record["scores"])
    ids = sorted({c for _, c in todo})
    res = subprocess.run(["docker", "exec", "-i", "statesync-mcp-paperb", "python", "-c", _FETCH],
                         input=json.dumps({"ids": ids, "tenant": TENANT}), capture_output=True, text=True, timeout=600)
    if res.returncode:
        raise SystemExit(res.stderr[-600:])
    chunks = json.loads(res.stdout.strip().splitlines()[-1])
    missing = [c for c in ids if c not in chunks]
    print(f"{len(pairs)} (question, passage) pairs, {len(todo)} to score; {len(missing)} chunk ids not found", flush=True)
    key = (REPO / "secrets" / "opencode_api_key").read_text().strip()
    lock = threading.Lock()

    def one(qid: str, cid: str) -> tuple[str, float | None, int]:
        c = chunks.get(cid)
        if c is None:
            return f"{qid}|{cid}", None, 0
        state = (f"Question: {qs[qid]['question']}\n\nPassage from {c['title']}, page {c['page']}:\n"
                 f"{(c['text'] or '')[:TEXT_CHARS]}")
        body = json.dumps({"model": MODEL, "state": state,
                           "questions": {"contains": {"type": "noul", "instructions": INSTRUCTIONS}}}).encode()
        for attempt in range(RETRIES):
            req = urllib.request.Request(URL, data=body, headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                                                                  "User-Agent": "statesync-research/1.0"})
            try:
                r = json.load(urllib.request.urlopen(req, timeout=60))
                return f"{qid}|{cid}", float(r["answers"]["contains"]["noul"]), int((r.get("usage") or {}).get("input_tokens") or 0)
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503) and attempt < RETRIES - 1:
                    time.sleep(5 * (attempt + 1))
                    continue
                return f"{qid}|{cid}", None, 0
            except Exception:  # noqa: BLE001 - asked again on resume
                time.sleep(2)
        return f"{qid}|{cid}", None, 0

    done = 0
    with ThreadPoolExecutor(workers) as ex:
        for fut in as_completed([ex.submit(one, q, c) for q, c in todo]):
            k, p, tokens = fut.result()
            with lock:
                done += 1
                if p is not None:
                    record["scores"][k] = p
                    record["cost"] += tokens * USD_PER_M_INPUT / 1e6
                if done % SAVE_EVERY == 0 or done == len(todo):
                    out_path().write_text(json.dumps(record, indent=1) + "\n")
                    print(f"{done}/{len(todo)}; cost ${record['cost']:.4f}", flush=True)
    out_path().write_text(json.dumps(record, indent=1) + "\n")
    return 0


def mcnemar(b: int, c: int) -> float:
    n = b + c
    return 1.0 if n == 0 else min(1.0, 2 * sum(math.comb(n, i) for i in range(min(b, c) + 1)) / 2 ** n)


def cmd_report() -> int:
    runs = json.loads(RUNS.read_text())["runs"]
    record = load()
    qs = questions()
    ingest = json.loads(pd.INGEST.read_text())
    name_of = {d["document_id"]: d["doc_name"] for d in ingest["documents"] if d.get("document_id")}
    summary, per = {}, {}
    for conf in configs():
        window = runs[conf]["window"]
        for order in ("recorded", "jev"):
            rows = {}
            for qid, r in runs[conf]["results"].items():
                if r.get("error"):
                    continue
                ps = r["passages"]
                if order == "jev":
                    scored = [(record["scores"].get(f"{qid}|{p['chunk_id']}"), n, p) for n, p in enumerate(ps)]
                    if any(s is None for s, _, _ in scored):
                        continue
                    ps = [p for _, _, p in sorted(scored, key=lambda x: (-x[0], x[1]))]
                q, gold = qs[qid], qs[qid]["gold"]
                got = [(name_of.get(p["document_id"]), p.get("page")) for p in ps]
                rows[qid] = {"window": any(g in gold for g in got[:window]), "page_first": bool(got) and got[0] in gold,
                             "doc_first": bool(got) and got[0][0] == q["doc_name"], "anywhere": any(g in gold for g in got)}
            per[f"{conf}|{order}"] = rows
            n = len(rows)
            summary[f"{conf}|{order}"] = {"n": n, **{m: round(sum(r[m] for r in rows.values()) / n, 3) if n else None
                                                     for m in ("window", "page_first", "doc_first", "anywhere")}}
            print(f"{conf}|{order}", json.dumps(summary[f"{conf}|{order}"]))
    comparisons = {}
    c0, c1 = configs()
    D, B, JD, JB = f"{c0}|recorded", f"{c1}|recorded", f"{c0}|jev", f"{c1}|jev"
    # both configurations must have the item (a failed search drops it from every comparison)
    common = set.intersection(*(set(per[k]) for k in (D, B, JD, JB)))
    tests = [("H-P1 JB vs B [page_first]", JB, B, "page_first"), ("H-S1 JB vs B [window]", JB, B, "window"),
             ("H-S2 JD vs B [page_first]", JD, B, "page_first"), ("H-S3 JD vs B [window]", JD, B, "window"),
             ("JD vs D [page_first]", JD, D, "page_first"), ("JD vs D [window]", JD, D, "window")]
    for name, a, b, m in tests:
        ids = sorted(common)
        w = sum(per[a][i][m] and not per[b][i][m] for i in ids)
        l_ = sum(per[b][i][m] and not per[a][i][m] for i in ids)
        comparisons[name] = {"n": len(ids), "wins": w, "losses": l_, "p": round(mcnemar(w, l_), 6)}
    secondary = sorted((k for k in comparisons if k.startswith("H-S")), key=lambda k: comparisons[k]["p"])
    for rank, k in enumerate(secondary):           # Holm over the three secondary hypotheses
        comparisons[k]["p_holm"] = round(min(1.0, max(comparisons[s2]["p"] * (len(secondary) - r2)
                                                     for r2, s2 in enumerate(secondary[:rank + 1]))), 6)
    comparisons["dropped_items"] = len(qs) - len(common)
    for k, v in comparisons.items():
        print(k, v)
    record["summary"], record["comparisons"] = summary, comparisons
    out_path().write_text(json.dumps(record, indent=1) + "\n")
    return 0


def main() -> int:
    global SET_NAME
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--set", choices=sorted(SETS), default="financebench")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("score")
    s.add_argument("--workers", type=int, default=4)
    sub.add_parser("report")
    args = ap.parse_args()
    SET_NAME = args.set
    return cmd_score(args.workers) if args.cmd == "score" else cmd_report()


if __name__ == "__main__":
    raise SystemExit(main())
