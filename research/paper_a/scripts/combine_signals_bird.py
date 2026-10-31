#!/usr/bin/env python3
"""
scripts/combine_signals_bird.py
Exploratory, not pre-registered (protocol v2 change 17). Do the warning signals add up? An
L2-regularised logistic regression predicts "this answer is wrong" from the per-answer signals, and
every answer is scored by a model that did not see it (out-of-fold): 5 folds stratified by
correctness, and leave-one-database-out. No model calls.

    python3 scripts/combine_signals_bird.py --scores docs/eval/bird_scores_<label>_v2_corrected.json --label <label>

Feature sets: free signals (process + data checks), process only, all (free + judge + self-
consistency), all without the data checks, and the two model signals alone. Intervals: paired
stratified bootstrap over answers with the out-of-fold scores fixed (training variation is not
resampled).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import LeaveOneGroupOut, StratifiedKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyze_bird as ab  # noqa: E402

PROCESS = ("no_sql", "empty_result", "any_sql_retry", "refusal")
CHECKS = ("fanout_retry", "truncation", "null_heavy", "grounding_before_repair")
FOLDS = 5                   # noqa: literals - protocol v2 change 17
L2_C = 1.0                  # noqa: literals - sklearn's default regularisation strength


def features(rows: list[dict], judge: list[float], names: tuple[str, ...]) -> np.ndarray:
    cols = []
    for name in names:
        if name == "judge":
            cols.append(judge)
        elif name == "self_consistency":
            cols.append([float(r["self_consistency_risk"]) for r in rows])
        else:
            cols.append([float(bool(r["signals"].get(name))) for r in rows])
    return np.array(cols, dtype=float).T


def out_of_fold(x: np.ndarray, y: np.ndarray, splits) -> list[float]:
    risk = np.zeros(len(y))
    for train, test in splits:
        if len(set(y[train])) < 2:
            risk[test] = y[train].mean()
            continue
        model = make_pipeline(StandardScaler(), LogisticRegression(C=L2_C, max_iter=1000))
        model.fit(x[train], y[train])
        risk[test] = model.predict_proba(x[test])[:, 1]
    return risk.tolist()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--scores", type=Path, required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    rows, judges = ab.load(args.scores, args.label)
    judge = judges[ab.PRIMARY_JUDGE]
    y = np.array([not r["correct"] for r in rows], dtype=int)
    wrong = [bool(v) for v in y]
    strata = [r["db_id"] for r in rows]
    sets = {
        "free (process + data checks)": PROCESS + CHECKS,
        "process only": PROCESS,
        "all (free + judge + self-consistency)": PROCESS + CHECKS + ("judge", "self_consistency"),
        "all without data checks": PROCESS + ("judge", "self_consistency"),
        "judge + self-consistency": ("judge", "self_consistency"),
    }
    cv = {
        "5-fold": list(StratifiedKFold(FOLDS, shuffle=True, random_state=ab.SEED).split(np.zeros(len(y)), y)),
        "leave-one-database-out": list(LeaveOneGroupOut().split(np.zeros(len(y)), y, groups=strata)),
    }
    report: dict[str, Any] = {"scores": str(args.scores), "n": len(rows), "wrong": int(y.sum()), "cv": {}}
    singles = {"self-consistency alone": [float(r["self_consistency_risk"]) for r in rows], "judge alone": judge}
    for cv_name, splits in cv.items():
        risks = {name: out_of_fold(features(rows, judge, cols), y, splits) for name, cols in sets.items()}
        risks.update(singles)
        out: dict[str, Any] = {name: {"auroc": round(ab.auroc(r, wrong), 4), "aurc": round(ab.aurc(r, wrong), 4)}
                               for name, r in risks.items()}
        for a, b in (("all (free + judge + self-consistency)", "all without data checks"),
                     ("all (free + judge + self-consistency)", "self-consistency alone"),
                     ("free (process + data checks)", "process only"),
                     ("free (process + data checks)", "judge alone")):
            ra, rb = risks[a], risks[b]
            boots = ab.stratified_bootstrap(strata, lambda idx, ra=ra, rb=rb: ab.aurc([rb[i] for i in idx], [wrong[i] for i in idx])
                                            - ab.aurc([ra[i] for i in idx], [wrong[i] for i in idx]))
            out[f"AURC gain: {a} over {b}"] = {"gain": round(ab.aurc(rb, wrong) - ab.aurc(ra, wrong), 4),
                                               "ci95": ab.interval(boots)}
        report["cv"][cv_name] = out
    full = make_pipeline(StandardScaler(), LogisticRegression(C=L2_C, max_iter=1000))
    cols = sets["all (free + judge + self-consistency)"]
    full.fit(features(rows, judge, cols), y)
    report["coefficients_all_standardised"] = dict(zip(cols, (round(c, 3) for c in full[-1].coef_[0])))
    out_path = args.out or ab.EVAL / f"bird_combination_{args.scores.stem.removeprefix('bird_scores_')}.json"
    out_path.write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
