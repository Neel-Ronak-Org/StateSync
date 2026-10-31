#!/usr/bin/env python3
"""
scripts/paperb_mutations.py
Paper B's mutation regime (docs/research/detect_then_fallback_study.md): synthetic training examples
made at "setup" from reference SQL, with no model. For each of the 393 held-out questions, the BIRD
PostgreSQL gold SQL (the reference a customer would provide) and up to MAX_MUTANTS single-site
mutations of it (the verifier pilot's generator, research/verifier/data.py), each run on the
PostgreSQL copy and labelled against the corrected gold on SQLite with Paper A's relaxed rule. Seeded.

    python3 scripts/paperb_mutations.py
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research" / "verifier"))
import eval_bird as eb  # noqa: E402
import paperb_pool as pool  # noqa: E402
from data import mutations  # noqa: E402

OUT = eb.EVAL / "paperb_mutations.json"
MAX_MUTANTS = 12            # noqa: literals - the pilot's cap per item
SEED = 20261005


def main() -> int:
    rng = random.Random(SEED)
    ids = json.loads(eb.HELDOUT.read_text())["question_ids"]
    pg = {q["question_id"]: q for q in eb.questions()}
    gold_sql = {int(q["question_id"]): q["SQL"] for q in json.loads(eb.CORRECTED.read_text())}
    conn = eb.bird_connection()
    out = []
    for n, qid in enumerate(ids, 1):
        q = pg[qid]
        gold = eb.run_sqlite(q["db_id"], gold_sql[qid])
        ref = eb.unqualify(q["SQL"]).strip().rstrip(";")
        try:
            muts = mutations(ref)
        except Exception:  # noqa: BLE001 -- a reference the parser cannot read gives no mutants
            muts = []
        rng.shuffle(muts)
        items = [("reference", ref)] + muts[:MAX_MUTANTS]
        examples = []
        for op, sql in items:
            res = pool.run(conn, q["db_id"], sql)
            rows = res[1] if res else None
            examples.append({"op": op, "sql": sql, "ran": res is not None, "preview": pool.preview(res),
                             "correct": bool(gold is not None and rows is not None
                                             and eb.rows_match_relaxed(rows, gold, norm=eb.norm_v2))})
        out.append({"question_id": qid, "db_id": q["db_id"], "examples": examples})
        if n % 50 == 0:
            print(f"{n}/{len(ids)}", flush=True)
    total = sum(len(o["examples"]) for o in out)
    correct = sum(e["correct"] for o in out for e in o["examples"])
    OUT.write_text(json.dumps({"seed": SEED, "max_mutants": MAX_MUTANTS, "questions": out,
                               "examples": total, "correct": correct}, indent=1) + "\n")
    print(f"{total} examples, {correct} correct ({correct / total:.1%})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
