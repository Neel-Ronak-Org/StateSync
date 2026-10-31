#!/usr/bin/env python3
"""
scripts/calibrate_abstention.py
Chooses the two abstention thresholds of docs/specs/rag_v2.md Phase E from a saved
harness run (docs/eval/retrieval_v2_<label>.json), by a fixed rule, and prints the
evidence. Stdlib only; it reads a file and asks nothing of any service.

THE RULE IT CALIBRATES. A question is abstained when its best dense score is missing
or < min_dense AND its best keyword score is missing or < min_keyword (strict <; the
first retrieval round's scores, which the harness saves as best_dense_score and
best_keyword_score). The harness records no fused score for this: an RRF score is
about 0.03 for answerable and unanswerable questions alike.

THE CHOICE IS DETERMINISTIC, so it can be reproduced and audited:
  candidate dense thresholds    0 and every observed best_dense_score
  candidate keyword thresholds  0, every observed best_keyword_score, and +infinity
  choose the pair that abstains the most unanswerable questions, subject to at most
  MAX_ANSWERABLE_ABSTAINED answerable ones abstained; ties by fewer answerable
  abstained, then the lowest dense threshold, then the lowest keyword threshold.
If no pair abstains any unanswerable question within that budget, the result is
min_dense = 0 (never abstains) and the output says so.

WHAT THIS IS NOT. The thresholds are fitted to the same frozen set they are then
reported on, so the counts it prints are an in-sample fit, not a held-out estimate.
Say so wherever a number from here is quoted.

USAGE (host, from the repo root):
  /opt/anaconda3/bin/python3 scripts/calibrate_abstention.py layout_hybrid_scoped
  /opt/anaconda3/bin/python3 scripts/calibrate_abstention.py layout_dense_scoped --apply 0.7 15
  --apply D K   do not choose: count what the pair (min_dense = D, min_keyword = K)
                abstains on this run (K may be "inf")
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = REPO_ROOT / "docs" / "eval"
MAX_ANSWERABLE_ABSTAINED = 2   # the spec's budget: answerable recall loss <= 2 questions


def percentile(values: list[float], pct: float) -> float | None:
    """Linear-interpolated percentile (pct in 0..100), as scripts/eval_retrieval.py computes it."""
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * pct / 100.0
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def abstains(best_dense: float | None, best_keyword: float | None, min_dense: float, min_keyword: float) -> bool:
    """The rule: both legs missing or strictly below their thresholds."""
    return (best_dense is None or best_dense < min_dense) and (best_keyword is None or best_keyword < min_keyword)


def count(rows: list[dict], min_dense: float, min_keyword: float) -> int:
    return sum(abstains(r["best_dense_score"], r["best_keyword_score"], min_dense, min_keyword) for r in rows)


def choose(unanswerable: list[dict], answerable: list[dict]) -> dict:
    """The (min_dense, min_keyword) pair by the deterministic rule in the module docstring."""
    everything = unanswerable + answerable
    dense_candidates = sorted({0.0} | {r["best_dense_score"] for r in everything if r["best_dense_score"] is not None})
    keyword_candidates = sorted({0.0, math.inf} | {r["best_keyword_score"] for r in everything
                                                  if r["best_keyword_score"] is not None})
    best: tuple | None = None
    for td in dense_candidates:
        for tk in keyword_candidates:
            lost = count(answerable, td, tk)
            if lost > MAX_ANSWERABLE_ABSTAINED:
                continue
            caught = count(unanswerable, td, tk)
            key = (-caught, lost, td, tk)          # more caught, then fewer lost, then the lowest thresholds
            if best is None or key < best:
                best = key
    caught, lost, td, tk = -best[0], best[1], best[2], best[3]
    if caught == 0:
        td, tk = 0.0, 0.0                           # nothing is gained: a pair that never abstains
    return {"min_dense": td, "min_keyword": tk, "unanswerable_abstained": caught, "answerable_abstained": lost,
            "never_abstains": caught == 0}


def fmt(value: float | None) -> str:
    return "None" if value is None else ("inf" if value == math.inf else f"{value:.4f}")


def spread(rows: list[dict], field: str) -> str:
    values = [r[field] for r in rows if r[field] is not None]
    if not values:
        return "n/a (no question has this score)"
    return (f"min {fmt(min(values))}  p10 {fmt(percentile(values, 10))}  p25 {fmt(percentile(values, 25))}  "
            f"median {fmt(percentile(values, 50))}   (n={len(values)} of {len(rows)})")


def main() -> int:
    parser = argparse.ArgumentParser(description="Choose the abstention thresholds from a saved harness run.")
    parser.add_argument("label", help="reads docs/eval/retrieval_v2_<label>.json")
    parser.add_argument("--apply", nargs=2, metavar=("DENSE", "KEYWORD"),
                        help="count what this pair abstains instead of choosing one")
    args = parser.parse_args()
    path = EVAL_DIR / f"retrieval_v2_{args.label}.json"
    if not path.exists():
        print(f"no such run: {path}", file=sys.stderr)
        return 1
    run = json.loads(path.read_text())
    questions = run["questions"]
    for q in questions:
        q.setdefault("best_dense_score", None)
        q.setdefault("best_keyword_score", None)
    unanswerable = [q for q in questions if q["category"] == "unanswerable"]
    answerable = [q for q in questions if q["category"] != "unanswerable"]

    print(f"run {args.label}: params {run.get('params')}, {len(questions)} questions "
          f"({len(answerable)} answerable, {len(unanswerable)} unanswerable)")
    print("\nunanswerable questions (best scores of the first retrieval round):")
    print(f"  {'id':<10} {'corpus':<8} {'best_dense_score':>17} {'best_keyword_score':>19}")
    for q in sorted(unanswerable, key=lambda r: r["id"]):
        print(f"  {q['id']:<10} {q['corpus']:<8} {fmt(q['best_dense_score']):>17} {fmt(q['best_keyword_score']):>19}")
    print("\nanswerable distribution:")
    print(f"  best_dense_score    {spread(answerable, 'best_dense_score')}")
    print(f"  best_keyword_score  {spread(answerable, 'best_keyword_score')}")

    if args.apply:
        td, tk = float(args.apply[0]), float(args.apply[1])
        print(f"\nthresholds applied: min_dense {fmt(td)}  min_keyword {fmt(tk)}")
        print(f"  unanswerable abstained: {count(unanswerable, td, tk)} of {len(unanswerable)}")
        print(f"  answerable abstained:   {count(answerable, td, tk)} of {len(answerable)}")
        return 0

    result = choose(unanswerable, answerable)
    print(f"\nchosen (budget: at most {MAX_ANSWERABLE_ABSTAINED} answerable abstained):")
    print(f"  min_dense {fmt(result['min_dense'])}  min_keyword {fmt(result['min_keyword'])}"
          f"   (exact: {result['min_dense']!r}, {result['min_keyword']!r})")
    print(f"  unanswerable abstained: {result['unanswerable_abstained']} of {len(unanswerable)}")
    print(f"  answerable abstained:   {result['answerable_abstained']} of {len(answerable)}")
    if result["never_abstains"]:
        print("  NO pair abstains any unanswerable question within the budget: "
              "thresholds that never abstain (min_dense 0) are the result")
    print("\nIn-sample: these thresholds were chosen on this frozen test set and are not a held-out estimate.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
