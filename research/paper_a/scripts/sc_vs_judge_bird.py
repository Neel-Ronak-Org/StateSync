#!/usr/bin/env python3
"""
scripts/sc_vs_judge_bird.py
Self-consistency minus judge AUROC on the held-out runs, with analyze_bird's paired bootstrap stratified
by database (seed 20261003). Exploratory (protocol changes 17-18); no model calls.

    python3 scripts/sc_vs_judge_bird.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyze_bird as ab  # noqa: E402

LABELS = ("heldout_gpt5mini", "heldout_gptoss")


def main() -> int:
    for label in LABELS:
        rows, judges = ab.load(ab.EVAL / f"bird_scores_{label}_v2_corrected.json", label)
        judge = judges[ab.PRIMARY_JUDGE]
        sc = [float(r["self_consistency_risk"]) for r in rows]
        wrong = [not r["correct"] for r in rows]

        def diff(idx: list[int], sc: list[float] = sc, judge: list[float] = judge,
                 wrong: list[bool] = wrong) -> float | None:
            a = ab.auroc([sc[i] for i in idx], [wrong[i] for i in idx])
            b = ab.auroc([judge[i] for i in idx], [wrong[i] for i in idx])
            return None if a is None or b is None else a - b

        ci = ab.interval(ab.stratified_bootstrap([r["db_id"] for r in rows], diff))
        print(f"{label}: n={len(rows)} self-consistency {ab.auroc(sc, wrong):.4f} judge {ab.auroc(judge, wrong):.4f} "
              f"difference {diff(list(range(len(rows)))):+.4f} ci95 {ci}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
