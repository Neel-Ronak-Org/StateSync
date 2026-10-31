#!/usr/bin/env python3
"""
scripts/analyze_bird_pilot.py
The pilot's comparison (docs/research/evidence_tiers_study.md §5-§6): each signal's ability to warn
about wrong answers, on the same answers. Labels: bird_scores_<label>.json (relaxed = primary).
Judges: bird_judge_<label>_*.json (p_correct). Bootstrap 95% intervals by question (1,000 resamples,
seed fixed). No model calls.

    python3 scripts/analyze_bird_pilot.py --label pilot_gpt5mini
"""

from __future__ import annotations

import argparse
import glob
import json
import random
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EVAL = REPO / "docs" / "eval"
RESAMPLES, SEED = 1000, 20261003  # noqa: literals - fixed in the protocol (§6)


def auroc(scores: list[float], wrong: list[bool]) -> float | None:
    """Probability a wrong answer gets a higher 'risk' score than a right one (ties count half)."""
    pos = [s for s, w in zip(scores, wrong) if w]
    neg = [s for s, w in zip(scores, wrong) if not w]
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def warn_stats(flags: list[bool], wrong: list[bool]) -> dict:
    tp = sum(f and w for f, w in zip(flags, wrong))
    flagged, n_wrong = sum(flags), sum(wrong)
    return {"flagged": flagged, "precision": round(tp / flagged, 3) if flagged else None,
            "recall": round(tp / n_wrong, 3) if n_wrong else None,
            "false_alarms_on_right": sum(f and not w for f, w in zip(flags, wrong))}


def selective_accuracy(risk: list[float], wrong: list[bool], coverage: float) -> float:
    order = sorted(range(len(risk)), key=lambda i: risk[i])
    keep = order[: max(1, round(coverage * len(order)))]
    return sum(not wrong[i] for i in keep) / len(keep)


def bootstrap(fn, n: int) -> tuple[float, float] | None:
    rng = random.Random(SEED)
    vals = []
    for _ in range(RESAMPLES):
        idx = [rng.randrange(n) for _ in range(n)]
        v = fn(idx)
        if v is not None:
            vals.append(v)
    if not vals:
        return None
    vals.sort()
    return round(vals[int(0.025 * len(vals))], 3), round(vals[int(0.975 * len(vals)) - 1], 3)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    args = ap.parse_args()
    rows = json.loads((EVAL / f"bird_scores_{args.label}.json").read_text())["rows"]
    qids = [str(r["question_id"]) for r in rows]
    wrong = [not r["correct"] for r in rows]
    n = len(rows)
    judges = {}
    for f in sorted(glob.glob(str(EVAL / f"bird_judge_{args.label}_*.json"))):
        d = json.loads(Path(f).read_text())
        p = {v["id"]: v.get("p_correct") for v in d["verdicts"]}
        judges[d["model"]] = [1 - p[q] if p.get(q) is not None else 0.5 for q in qids]   # risk = 1 - p_correct
    any_check = [any(r["signals"][k] for k in ("tier_unchecked", "grounding_unmatched", "fanout_retry", "any_sql_retry")) for r in rows]
    signals = {"any data check": [1.0 if a else 0.0 for a in any_check]}
    signals.update({f"judge {m}": s for m, s in judges.items()})
    for m, s in judges.items():
        signals[f"checks OR judge {m}"] = [max(c, j) for c, j in zip(signals["any data check"], s)]
    report = {"label": args.label, "n": n, "wrong": sum(wrong), "accuracy": round(1 - sum(wrong) / n, 3),
              "accuracy_ci95": bootstrap(lambda idx: 1 - sum(wrong[i] for i in idx) / len(idx), n), "signals": {}}
    for name, risk in signals.items():
        report["signals"][name] = {
            "auroc": round(auroc(risk, wrong), 3) if auroc(risk, wrong) is not None else None,
            "auroc_ci95": bootstrap(lambda idx: auroc([risk[i] for i in idx], [wrong[i] for i in idx]), n),
            "warn_at_0.5": warn_stats([r >= 0.5 for r in risk], wrong),   # noqa: literals - the protocol's warning threshold
            "selective_accuracy_at_80pct": round(selective_accuracy(risk, wrong, 0.8), 3),   # noqa: literals - protocol coverage
        }
    out = EVAL / f"bird_analysis_{args.label}.json"
    out.write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
