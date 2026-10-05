#!/usr/bin/env python3
"""
scripts/paperb_pool.py
Paper B candidate pool with labels (docs/research/detect_then_fallback_study.md). For each of the 393
held-out questions, 21 SQL candidates in a fixed order:
- GPT-5 mini: the pipeline answer and its 5 self-consistency samples (Paper A);
- gpt-oss-120b: the same (Paper A's replication);
- 3 samples each from the three standalone models (scripts/paperb_candidates.py).

Each candidate is run on the PostgreSQL copy (30 s timeout, as eval_bird._run) and labelled against the
corrected gold on SQLite with Paper A's relaxed rule. It is clustered with the question's other
candidates whose results agree under that rule in either direction, and gets a result preview for the
verifiers (row count, columns, first rows). No model calls.

    python3 scripts/paperb_pool.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import eval_bird as eb  # noqa: E402

OUT = eb.EVAL / "paperb_pool.json"
PREVIEW_ROWS = 3            # noqa: literals - pre-registered verifier input
CELL_CHARS = 60             # noqa: literals - preview cell truncation
TIMEOUT_MS = 30000          # noqa: literals - as eval_bird._run
PIPELINE_RUNS = (("gpt-5-mini", "heldout_gpt5mini"), ("gpt-oss-120b", "heldout_gptoss"))


def run(conn: Any, schema: str, sql: str | None) -> tuple[list[str], list[tuple]] | None:
    if not sql:
        return None
    try:
        conn.execute(f"SET statement_timeout = {TIMEOUT_MS}")
        conn.execute(f'SET search_path = "{schema}"')
        cur = conn.execute(sql)
        return [d.name for d in cur.description or []], [tuple(r) for r in cur.fetchall()]
    except Exception:  # noqa: BLE001 -- a candidate that fails is simply wrong
        return None


def same(a: list | None, b: list | None) -> bool:
    return a is not None and b is not None and (eb.rows_match_relaxed(a, b, norm=eb.norm_v2)
                                                 or eb.rows_match_relaxed(b, a, norm=eb.norm_v2))


def preview(result: tuple[list[str], list[tuple]] | None) -> dict[str, Any] | None:
    if result is None:
        return None
    cols, rows = result
    return {"row_count": len(rows), "columns": cols,
            "rows": [[str(c)[:CELL_CHARS] for c in r] for r in rows[:PREVIEW_ROWS]]}


def main() -> int:
    ids = json.loads(eb.HELDOUT.read_text())["question_ids"]
    runs = {model: json.loads(eb.answers_path(label).read_text())["answers"] for model, label in PIPELINE_RUNS}
    standalone = json.loads((eb.EVAL / "paperb_candidates.json").read_text())
    gold_sql = {int(q["question_id"]): q["SQL"] for q in json.loads(eb.CORRECTED.read_text())}
    conn = eb.bird_connection()
    questions = []
    for n, qid in enumerate(ids, 1):
        first = runs["gpt-5-mini"][str(qid)]
        db = first["db_id"]
        gold = eb.run_sqlite(db, gold_sql[qid])
        cands: list[dict[str, Any]] = []
        for model, answers in runs.items():
            a = answers[str(qid)]
            sqls = [a.get("executed_sql")] + [s["sql"] for s in (a.get("self_consistency") or {}).get("samples", [])]
            for i, sql in enumerate(sqls):
                cands.append({"model": model, "source": "pipeline answer" if i == 0 else "pipeline sample",
                              "sql": eb.unqualify(sql) if sql else None})
        for model in standalone["models"]:
            for i in range(standalone["samples"]):
                rec = standalone["candidates"].get(f"{qid}|{model}|{i}") or {}
                cands.append({"model": model, "source": "standalone", "sql": rec.get("sql")})
        results = [run(conn, db, c["sql"]) for c in cands]
        clusters: list[int] = []
        reps: list[list] = []
        for c, res in zip(cands, results):
            rows = res[1] if res else None
            c["ran"] = res is not None
            c["correct"] = bool(gold is not None and res is not None and eb.rows_match_relaxed(rows, gold, norm=eb.norm_v2))
            c["preview"] = preview(res)
            if rows is None:
                c["cluster"] = None
                continue
            for k, rep in enumerate(reps):
                if same(rep, rows):
                    c["cluster"] = k
                    break
            else:
                reps.append(rows)
                c["cluster"] = len(reps) - 1
            clusters.append(c["cluster"])
        questions.append({"question_id": qid, "db_id": db, "question": first["question_asked"],
                          "gold_ran": gold is not None, "candidates": cands})
        if n % 25 == 0:
            print(f"{n}/{len(ids)}", flush=True)
    total = sum(len(q["candidates"]) for q in questions)
    correct = sum(c["correct"] for q in questions for c in q["candidates"])
    OUT.write_text(json.dumps({"questions": questions, "candidates_per_question": len(questions[0]["candidates"]),
                               "candidates": total, "correct": correct}, indent=1) + "\n")
    print(f"{total} candidates, {correct} correct ({correct / total:.1%}); oracle "
          f"{sum(any(c['correct'] for c in q['candidates']) for q in questions) / len(questions):.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
