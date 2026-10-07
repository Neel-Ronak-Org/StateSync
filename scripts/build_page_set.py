#!/usr/bin/env python3
"""
scripts/build_page_set.py
The held-out page-finding set of docs/research/jev_pages_confirmatory.md (pre-registered 2026-10-07, commit 55fb6c1).
  1. Every page of the FinanceBench library's 84 filings, its chunks joined in order, read from the library
     database (the read-only role, the evaluations' tenant).
  2. Eligible: at least 600 characters; not a FinanceBench evidence page or next to one (+-1).
  3. Sampled with seed 20261007, in that order, at most 4 per document, until CANDIDATES pages.
  4. Writer (gemini-3.8-flash, local_router): one question and a short answer from the page (prompt WRITER).
  5. Checker (gpt-oss-120b, cheaperinference): does the page answer it, specifically (prompt CHECKER).
  6. The first 200 kept, in sampling order, form docs/eval/page_set.json (dropped ones are recorded with why).

    python3 scripts/build_page_set.py              the first set (above)
    python3 scripts/build_page_set.py --set r1     rerun R1 (docs/research/paperb_reruns_prereg.md): seed 20261008,
                                                   writer glm-5.3-flash (Cheaper Inference), 420 candidates, 300 kept,
                                                   the first set's pages (sampled or kept) and their neighbours
                                                   excluded too; written to docs/eval/page_set_r1.json
"""

from __future__ import annotations

import json
import argparse
import random
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import eval_bird as eb  # noqa: E402
import paperb_docs as pd  # noqa: E402
from eval_writer_window import TENANT  # noqa: E402

OUT = REPO / "docs" / "eval" / "page_set.json"
SEED = 20261007
N_KEEP = 200                # noqa: literals - pre-registered
CANDIDATES = 280            # noqa: literals - sampled before the checker drops some
MIN_CHARS = 600             # noqa: literals - pre-registered: a page with less text is not eligible
PER_DOCUMENT = 4            # noqa: literals - pre-registered cap per filing
PAGE_CHARS = 6000           # noqa: literals - page text given to the writer and the checker
CONTAINER = "statesync-orchestrator-paperb"
WRITER = ("gemini-3.8-flash", "local_router")
CHECKER = ("gpt-oss-120b", "cheaperinference")

WRITER_PROMPT = (
    "You write evaluation questions for a search system over company filings. You are given the filing's name "
    "(company, year, type) and one page of it. Write ONE question a financial analyst would ask that this page "
    "alone answers, and its short answer. The question must name the company and the fiscal year or period, as in "
    "'What was 3M's total debt at the end of fiscal 2018?'. Do not copy sentences or distinctive phrases from the "
    "page; ask in your own words. Ask about a specific fact, figure or statement on the page, not about the page "
    "itself. Reply with JSON only: {\"question\": \"...\", \"answer\": \"...\"}.")
CHECKER_PROMPT = (
    "You check an evaluation question against one page of a company filing. Answer two things: does the page "
    "contain the answer to the question (not merely the topic), and is the question specific (it names the "
    "company and the period, and has one clear answer)? Reply with JSON only: "
    "{\"page_answers\": true|false, \"specific\": true|false}.")

_PAGES = r"""
import json, sys, psycopg
from db import _build_dsn
req = json.load(sys.stdin)
with psycopg.connect(_build_dsn()) as conn:
    conn.execute("SELECT set_config('statesync.tenant_id', %s, false)", (req["tenant"],))
    rows = conn.execute("SELECT document_id, page, string_agg(content_md, E'\n' ORDER BY ordinal) FROM workspace_docs.chunks "
                        "WHERE document_id = ANY(%s) AND page IS NOT NULL GROUP BY document_id, page", (req["ids"],)).fetchall()
print(json.dumps([[r[0], r[1], r[2]] for r in rows]))
"""


def llm(model_endpoint: tuple[str, str], system: str, items: list[dict]) -> dict[str, str]:
    req = {"endpoint": model_endpoint[1], "model": model_endpoint[0], "system": system, "max_tokens": eb.JUDGE_MAX_TOKENS,
           "spacing_s": 0.5, "items": items}
    res = subprocess.run(["docker", "exec", "-i", CONTAINER, "python", "-c", eb._JUDGE_SNIPPET],  # noqa: SLF001
                         input=json.dumps(req), capture_output=True, text=True, timeout=max(600, len(items) * 120))
    if res.returncode:
        raise SystemExit(res.stderr[-600:])
    return {v["id"]: v.get("text") or "" for v in json.loads(res.stdout.strip().splitlines()[-1])}


def parse(text: str) -> dict | None:
    m = re.search(r"\{.*\}", re.sub(r"```(?:json)?", "", text or ""), re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        return None


SETS = {   # name -> (out, seed, writer, candidates, keep, exclude the first set's pages)
    "first": (OUT, SEED, WRITER, CANDIDATES, N_KEEP, False),
    "r1": (REPO / "docs" / "eval" / "page_set_r1.json", 20261008, ("glm-5.3-flash", "cheaperinference"), 420, 300, True),
}


def first_set_pages() -> set[tuple[str, int]]:
    d = json.loads(OUT.read_text())
    return {(x["doc_name"], int(x["page"]) + k) for x in d["items"] + d["dropped"] for k in (-1, 0, 1)}


def main() -> int:
    global OUT, SEED, WRITER, CANDIDATES, N_KEEP
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", choices=sorted(SETS), default="first")
    args = ap.parse_args()
    out, seed, writer, candidates, keep, exclude_first = SETS[args.set]
    extra_excluded = first_set_pages() if exclude_first else set()
    if out.exists():
        raise SystemExit(f"{out} exists; a pre-registered set is built once")
    OUT, SEED, WRITER, CANDIDATES, N_KEEP = out, seed, writer, candidates, keep
    ingest = json.loads(pd.INGEST.read_text())
    docs = {d["document_id"]: d["doc_name"] for d in ingest["documents"] if d.get("status") == "ready" and d.get("document_id")}
    res = subprocess.run(["docker", "exec", "-i", "statesync-mcp-paperb", "python", "-c", _PAGES],
                         input=json.dumps({"ids": sorted(docs), "tenant": TENANT}), capture_output=True, text=True, timeout=600)
    if res.returncode:
        raise SystemExit(res.stderr[-600:])
    pages = json.loads(res.stdout.strip().splitlines()[-1])
    excluded = {(name, p + d) for q in pd.questions() for name, p in pd.gold_pages(q) for d in (-1, 0, 1)} | extra_excluded
    eligible = sorted((docs[i], int(p), t) for i, p, t in pages
                      if len(t or "") >= MIN_CHARS and (docs[i], int(p)) not in excluded)
    random.Random(SEED).shuffle(eligible)
    per_doc: Counter = Counter()
    sampled = []
    for name, page, text in eligible:
        if per_doc[name] < PER_DOCUMENT:
            per_doc[name] += 1
            sampled.append({"id": f"{'pg' if args.set == 'first' else args.set + 'p'}{len(sampled) + 1:03d}", "doc_name": name, "page": page, "text": text[:PAGE_CHARS]})
        if len(sampled) == CANDIDATES:
            break
    print(f"{len(pages)} pages, {len(eligible)} eligible, {len(sampled)} sampled from {len(per_doc)} filings", flush=True)
    written = llm(WRITER, WRITER_PROMPT, [{"id": s["id"], "prompt": f"Filing: {s['doc_name']}\n\nPage {s['page']}:\n{s['text']}"}
                                          for s in sampled])
    for s in sampled:
        s["written"] = parse(written.get(s["id"], ""))
    to_check = [s for s in sampled if s["written"] and s["written"].get("question")]
    checked = llm(CHECKER, CHECKER_PROMPT, [{"id": s["id"], "prompt": f"Page {s['page']} of {s['doc_name']}:\n{s['text']}\n\n"
                                                                     f"Question: {s['written']['question']}"} for s in to_check])
    kept, dropped = [], []
    for s in sampled:
        verdict = parse(checked.get(s["id"], "")) if s["written"] else None
        why = ("writer reply did not parse" if not s["written"] or not s["written"].get("question")
               else "checker reply did not parse" if verdict is None
               else "page does not answer it" if not verdict.get("page_answers")
               else "not specific" if not verdict.get("specific") else None)
        rec = {"id": s["id"], "doc_name": s["doc_name"], "page": s["page"],
               "question": (s["written"] or {}).get("question"), "answer": (s["written"] or {}).get("answer")}
        (dropped if why else kept).append({**rec, **({"dropped": why} if why else {})})
    items = kept[:N_KEEP]
    OUT.write_text(json.dumps({"preregistration": ("docs/research/jev_pages_confirmatory.md" if args.set == "first" else "docs/research/paperb_reruns_prereg.md"), "seed": SEED, "writer": WRITER,
                               "checker": CHECKER, "eligible_pages": len(eligible), "sampled": len(sampled),
                               "kept": len(kept), "used": len(items), "dropped": dropped, "items": items}, indent=1) + "\n")
    print(f"kept {len(kept)}, dropped {len(dropped)} ({Counter(d['dropped'] for d in dropped)}); using {len(items)}")
    if len(items) < N_KEEP:
        print(f"WARNING: fewer than {N_KEEP} items; log the shortfall in the pre-registration's change log")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
