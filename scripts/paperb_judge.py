#!/usr/bin/env python3
"""
scripts/paperb_judge.py
Paper B's LLM judge (docs/research/detect_then_fallback_study.md v2, §5): Gemini 3.8 Flash (Zed, via the
local router) scores each distinct result cluster of each question once, on the cluster's first
candidate, shown the question with its hint, the tables, the SQL and the first 10 rows
(eval_bird.preview). The system prompt is v2's, fixed in the protocol: a score from 0 to 100 that the
result answers the question (Paper A's judge, asked for a probability, gave 1.0 to 328 of 393 answers).
p_correct = score / 100. Candidates that failed to run get 0 without a call.
Resumable; runs SHARDS judge batches in parallel inside the orchestrator container.

    python3 scripts/paperb_judge.py [--shards 3]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import eval_bird as eb  # noqa: E402

ENDPOINT, MODEL = "local_router", "zed-hosted/gemini-3.8-flash"
OUT = eb.EVAL / "paperb_judge_gemini.json"
SPACING_S = 1.0             # noqa: literals - per batch; batches run in parallel
SYSTEM = ('You check SQL written for a question about a PostgreSQL database. You are given the question (with an '
          'optional hint), the database\'s tables and columns, the SQL, and the first rows of its result. Rate how '
          'likely it is that the result correctly answers the question, from 0 (certainly wrong) to 100 (certainly '
          'right). Reply with JSON only: {"score": <integer 0-100>, "reason": "<one sentence>"}.')
SCORE = re.compile(r'"score"\s*:\s*"?([0-9]+(?:\.[0-9]+)?)')


def parse_score(text: str) -> float | None:
    """p_correct = score / 100, clamped; None when no score can be read."""
    m = SCORE.search(text or "")
    try:
        return max(0.0, min(1.0, float(m.group(1)) / 100)) if m else None
    except ValueError:
        return None


def run_batch(items: list[dict]) -> list[dict]:
    req = {"endpoint": ENDPOINT, "model": MODEL, "system": SYSTEM, "max_tokens": eb.JUDGE_MAX_TOKENS,
           "spacing_s": SPACING_S, "items": items}
    out = subprocess.run(["docker", "exec", "-i", eb.ORCHESTRATOR, "python", "-c", eb._JUDGE_SNIPPET],
                         input=json.dumps(req), capture_output=True, text=True,
                         timeout=max(600, len(items) * 120))
    if out.returncode:
        print(out.stderr[-600:], flush=True)
        return []
    return json.loads(out.stdout.strip().splitlines()[-1])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--shards", type=int, default=3)
    args = ap.parse_args()
    pool = json.loads((eb.EVAL / "paperb_pool.json").read_text())["questions"]
    record = json.loads(OUT.read_text()) if OUT.exists() else {"endpoint": ENDPOINT, "model": MODEL,
                                                               "system": SYSTEM, "verdicts": {}}
    schemas: dict[str, str] = {}
    items = []
    for q in pool:
        firsts: dict[int, dict] = {}
        for c in q["candidates"]:
            if c["cluster"] is not None:
                firsts.setdefault(c["cluster"], c)
        for k, c in firsts.items():
            key = f"{q['question_id']}|{k}"
            if record["verdicts"].get(key, {}).get("p_correct") is not None:
                continue
            schemas.setdefault(q["db_id"], eb.schema_text(q["db_id"]))
            items.append({"id": key, "prompt": f"Question: {q['question']}\n\nTables:\n{schemas[q['db_id']]}\n\n"
                                               f"SQL:\n{c['sql']}\n\nFirst rows of the result:\n"
                                               f"{eb.preview(q['db_id'], c['sql'])}"})
    print(f"{len(items)} clusters to judge", flush=True)
    shards = [items[i::args.shards] for i in range(args.shards)]
    with ThreadPoolExecutor(args.shards) as pool_ex:
        for verdicts in pool_ex.map(run_batch, shards):
            for v in verdicts:
                v["p_correct"] = parse_score(v.get("text", ""))
                record["verdicts"][v["id"]] = v
            OUT.write_text(json.dumps(record, indent=1) + "\n")
    ok = sum(v.get("p_correct") is not None for v in record["verdicts"].values())
    print(f"{ok} clusters judged; served: {sorted({v.get('served_model') for v in record['verdicts'].values() if v.get('served_model')})}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
