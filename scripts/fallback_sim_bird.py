#!/usr/bin/env python3
"""
scripts/fallback_sim_bird.py
Exploratory, not pre-registered (decided 2026-10-05, after the evidence-tiers results). Does a
fallback triggered when a warning fires raise accuracy? Offline, from the held-out run's records:
each answer has its own SQL plus the self-consistency samples (5 more SQL candidates from the same
prompt). A fallback runs every candidate and keeps the majority result (ties keep the original). No
model calls: candidates are re-executed on PostgreSQL and scored against the corrected gold on
SQLite, as in eval_bird.py score --labels v2.

    python3 scripts/fallback_sim_bird.py

Policies: never (as shipped), always vote, and vote only when a trigger fires (self-consistency
disagreement, the Gemini judge's doubt, a process signal, or either). "oracle" counts an answer right
when ANY candidate is right: the ceiling of any selection rule over these candidates.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyze_bird as ab  # noqa: E402
import eval_bird as eb  # noqa: E402

LABEL = "heldout_gpt5mini"
SCORES = eb.EVAL / f"bird_scores_{LABEL}_v2_corrected.json"
OUT = eb.EVAL / "bird_fallback_sim_heldout_gpt5mini.json"
JUDGE_DOUBT = 0.5           # noqa: literals - the judge's p(correct) below which it doubts an answer
PROCESS = ("no_sql", "empty_result", "any_sql_retry")


def same(a: list | None, b: list | None) -> bool:
    """Two results agree under the relaxed rule in either direction (self_consistency.same_result)."""
    return a is not None and b is not None and (eb.rows_match_relaxed(a, b, norm=eb.norm_v2)
                                                 or eb.rows_match_relaxed(b, a, norm=eb.norm_v2))


def vote(results: list[list | None]) -> int | None:
    """Index of the majority result among candidates (index 0 is the original answer); ties keep the
    cluster holding the original, else the earliest. None when no candidate ran."""
    clusters: list[list[int]] = []
    for i, r in enumerate(results):
        if r is None:
            continue
        for c in clusters:
            if same(results[c[0]], r):
                c.append(i)
                break
        else:
            clusters.append([i])
    if not clusters:
        return None
    best = max(len(c) for c in clusters)
    top = [c for c in clusters if len(c) == best]
    return next((c[0] for c in top if 0 in c), top[0][0])


def main() -> int:
    answers = json.loads(eb.answers_path(LABEL).read_text())["answers"]
    rows, judges = ab.load(SCORES, LABEL)
    judge = dict(zip((str(r["question_id"]) for r in rows), judges[ab.PRIMARY_JUDGE]))
    gold_sql = {int(q["question_id"]): q["SQL"] for q in json.loads(eb.CORRECTED.read_text())}
    by_id = {q["question_id"]: q for q in eb.questions()}
    conn = eb.bird_connection()
    per: list[dict[str, Any]] = []
    for r in rows:
        qid = str(r["question_id"])
        a, q = answers[qid], by_id[int(qid)]
        gold = eb.run_sqlite(q["db_id"], gold_sql[int(qid)])
        sqls = [a.get("executed_sql")] + [s["sql"] for s in (a.get("self_consistency") or {}).get("samples", [])]
        results = [eb._run(conn, q["db_id"], eb.unqualify(s)) if s else None for s in sqls]
        correct = [gold is not None and eb.rows_match_relaxed(res, gold, norm=eb.norm_v2) for res in results]
        winner = vote(results)
        per.append({"question_id": int(qid), "db_id": q["db_id"], "original_correct": bool(r["correct"]),
                    "voted_correct": bool(correct[winner]) if winner is not None else bool(r["correct"]),
                    "candidates": len(sqls), "candidates_correct": sum(correct), "oracle": any(correct),
                    "sc_risk": r["self_consistency_risk"], "judge_doubt": 1 - judge[qid] < JUDGE_DOUBT,
                    "process": any(r["signals"].get(s) for s in PROCESS)})
        if per[-1]["original_correct"] != bool(r["correct"]):
            raise SystemExit(f"q{qid}: recomputed label differs from the scores file")
    triggers = {
        "never (as run)": lambda p: False,
        "always vote": lambda p: True,
        "self-consistency risk > 0": lambda p: p["sc_risk"] > 0,
        "judge doubts (p < 0.5)": lambda p: p["judge_doubt"],
        "process signal": lambda p: p["process"],
        "judge or process": lambda p: p["judge_doubt"] or p["process"],
    }
    n = len(per)
    report: dict[str, Any] = {"n": n, "policies": {}}
    for name, fires in triggers.items():
        final = [p["voted_correct"] if fires(p) else p["original_correct"] for p in per]
        fixed = sum(1 for p, f in zip(per, final) if f and not p["original_correct"])
        broken = sum(1 for p, f in zip(per, final) if p["original_correct"] and not f)
        fired = sum(1 for p in per if fires(p))
        report["policies"][name] = {"accuracy": round(sum(final) / n, 4), "fired": fired, "fixed": fixed, "broken": broken,
                                    "sign_test_p": round(ab.sign_test_p(fixed, broken), 4) if fixed + broken else None}
    report["oracle_any_candidate"] = round(sum(p["oracle"] for p in per) / n, 4)
    OUT.write_text(json.dumps({"purpose": __doc__.strip().splitlines()[1], "report": report, "per_question": per}, indent=1) + "\n")
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
