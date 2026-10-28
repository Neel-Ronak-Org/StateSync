#!/usr/bin/env python3
"""
scripts/sc_cost_bird.py
Self-consistency at matched cost (exploratory, after the confirmatory results): the risk computed from
m of the 5 recorded samples, m = 1..5, against the judge. Each question's risk with m samples is the
share of those m that disagree with the answer; the AUROC is averaged over all C(5, m) choices of
sample positions, so no sample order is favoured. Answers whose SQL failed drew no samples and keep
risk 1. Intervals: analyze_bird's paired bootstrap stratified by database (seed 20261003) on the
difference from the judge. No model calls.

    python3 scripts/sc_cost_bird.py
"""

from __future__ import annotations

import itertools
import json
import sys
from functools import partial
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyze_bird as ab  # noqa: E402
import eval_bird as eb  # noqa: E402

LABELS = ("heldout_gpt5mini", "heldout_gptoss")
K = 5                       # self_consistency_k of both runs
OUT = ab.EVAL / "bird_sc_cost_heldout.json"


def fast_auroc(scores: list[float], wrong: list[bool]) -> float | None:
    """analyze_bird.auroc (ties count half) by sorting: O(n log n), for the bootstrap."""
    n_pos = sum(wrong)
    n_neg = len(wrong) - n_pos
    if not n_pos or not n_neg:
        return None
    order = sorted(range(len(scores)), key=scores.__getitem__)
    rank_sum, i = 0.0, 0
    while i < len(order):
        j = i
        while j < len(order) and scores[order[j]] == scores[order[i]]:
            j += 1
        mid = (i + j + 1) / 2                   # mean 1-based rank of the tied block
        rank_sum += mid * sum(wrong[order[t]] for t in range(i, j))
        i = j
    return (rank_sum - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def mean_auroc(idx: list[int], risks: list[list[float]], wrong: list[bool]) -> float:
    w = [wrong[i] for i in idx]
    return sum(fast_auroc([r[i] for i in idx], w) for r in risks) / len(risks)


def minus_judge(idx: list[int], risks: list[list[float]], judge: list[float], wrong: list[bool]) -> float:
    return mean_auroc(idx, risks, wrong) - fast_auroc([judge[i] for i in idx], [wrong[i] for i in idx])


def main() -> int:
    out: dict[str, dict] = {}
    for label in LABELS:
        rows, judges = ab.load(ab.EVAL / f"bird_scores_{label}_v2_corrected.json", label)
        answers = json.loads(eb.answers_path(label).read_text())["answers"]
        judge = judges[ab.PRIMARY_JUDGE]
        wrong = [not r["correct"] for r in rows]
        same = [[bool(s.get("same")) for s in (answers[str(r["question_id"])].get("self_consistency") or {}).get("samples", [])]
                for r in rows]
        strata = [r["db_id"] for r in rows]
        everything = list(range(len(rows)))
        assert abs(fast_auroc(judge, wrong) - ab.auroc(judge, wrong)) < 1e-12
        out[label] = {"judge_auroc": round(fast_auroc(judge, wrong), 4), "by_samples": {}}
        for m in range(1, K + 1):
            risks = [[1 - sum(s[p] for p in sub) / m if s else 1.0 for s in same]
                     for sub in itertools.combinations(range(K), m)]
            ci = ab.interval(ab.stratified_bootstrap(strata, partial(minus_judge, risks=risks, judge=judge, wrong=wrong)))
            out[label]["by_samples"][m] = {"extra_calls": m, "auroc": round(mean_auroc(everything, risks, wrong), 4),
                                           "minus_judge": round(minus_judge(everything, risks, judge, wrong), 4),
                                           "minus_judge_ci95": [round(v, 4) for v in ci]}
            print(f"{label} m={m}: {out[label]['by_samples'][m]}", flush=True)
    OUT.write_text(json.dumps(out, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
