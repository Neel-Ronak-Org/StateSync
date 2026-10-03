"""
research/verifier/evaluate.py
Metrics for every scored file (README.md): AUROC with a bootstrap 95% interval, ECE (10 bins),
Brier, and "verified" precision -- the share of candidates labelled verified at a threshold, and
how many of those are wrong.

    python evaluate.py out/score_*.json
"""

from __future__ import annotations

import json
import random
import sys

from sklearn.metrics import roc_auc_score

BOOT, SEED, BINS = 2000, 20261002, 10


def auroc_ci(y: list[int], p: list[float]) -> tuple[float, float, float] | None:
    if len(set(y)) < 2:
        return None
    point = roc_auc_score(y, p)
    rng, vals, n = random.Random(SEED), [], len(y)
    for _ in range(BOOT):
        idx = [rng.randrange(n) for _ in range(n)]
        ys = [y[i] for i in idx]
        if len(set(ys)) == 2:
            vals.append(roc_auc_score(ys, [p[i] for i in idx]))
    vals.sort()
    return point, vals[int(0.025 * len(vals))], vals[int(0.975 * len(vals))]


def ece(y: list[int], p: list[float]) -> float:
    total, err = len(y), 0.0
    for b in range(BINS):
        lo, hi = b / BINS, (b + 1) / BINS
        idx = [i for i, v in enumerate(p) if lo <= v < hi or (b == BINS - 1 and v == 1.0)]
        if idx:
            err += len(idx) / total * abs(sum(y[i] for i in idx) / len(idx) - sum(p[i] for i in idx) / len(idx))
    return err


def verified_at(y: list[int], p: list[float], threshold: float) -> tuple[float, int, int]:
    """(share labelled verified, how many verified, how many of those are wrong)."""
    picked = [i for i, v in enumerate(p) if v >= threshold]
    return len(picked) / len(y), len(picked), sum(1 for i in picked if not y[i])


def report(path: str) -> dict:
    d = json.load(open(path))
    rows = d["rows"]
    y = [int(r["correct"]) for r in rows]
    p = [float(r["p_correct"]) for r in rows]
    a = auroc_ci(y, p)
    out = {"file": path, "n": len(y), "correct_share": round(sum(y) / len(y), 3),
           "auroc": None if a is None else [round(v, 3) for v in a], "ece": round(ece(y, p), 3),
           "brier": round(sum((pi - yi) ** 2 for pi, yi in zip(p, y)) / len(y), 3)}
    for t in (0.5, 0.8, 0.9):
        share, n_ver, n_wrong = verified_at(y, p, t)
        out[f"verified@{t}"] = f"{share:.0%} of answers, {n_wrong}/{n_ver} wrong"
    if "ms_per_item_batched" in d:
        out["ms_per_item"] = d["ms_per_item_batched"]
    return out


if __name__ == "__main__":
    for f in sys.argv[1:]:
        print(json.dumps(report(f)))
