#!/usr/bin/env python3
"""
scripts/analyze_bird.py
The analysis of protocol v2 (docs/research/evidence_tiers_study.md §5-§6), fixed before any held-out
run. No model calls. The pilot's v1 analysis stays in scripts/analyze_bird_pilot.py, unchanged.

    python3 scripts/analyze_bird.py --scores docs/eval/bird_scores_<label>_v2_corrected.json --label <label>

Inputs: a v2 scores file (eval_bird.py score --labels v2) and the judge files for the same label
(bird_judge_<label>_*.json). PRIMARY_JUDGE is the comparator named in the protocol (Gemini 3.8 Flash).

What it computes:
- per signal: flagged, precision, recall, false-alarm rate, AUROC;
- MATCHED FLAG RATE (primary): for each yes/no check flagging k answers, the wrong answers it
  catches against the expected number among the judge's k most suspect answers, ties broken at
  random (computed exactly, not sampled);
- risk-coverage: AURC and selective accuracy at 80% and 90% coverage, ties broken at random (exact
  expectation), for the judge, "checks then judge" (any data check first, then judge risk), and
  self-consistency when present;
- H2: AURC(judge) - AURC(checks then judge); H3: figure grounding's false alarms on answers whose
  text is correct, one-sided 95% upper bound; H4: fixed / broken by the answer label, sign test,
  accuracy gain, with its precondition (>= H4_MIN_TRIGGERS repairs);
- paired bootstrap, BOOTSTRAP_RESAMPLES resamples stratified by database, seed SEED; Holm across
  the confirmatory tests present. H1 is computed on the fan-out set (--fanout-set), with its
  precondition (>= H1_MIN_ERRORS wrong answers on the risky questions); with --heldout-analysis, the
  final Holm step over H1 (fan-out set) and H2-H4 (held-out);
- run-to-run variation (--repeat-of): the label and signal flip rates between two runs of the same
  questions.
Questions marked excluded, or whose gold failed, are left out.
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import random
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parents[1]
EVAL = REPO / "docs" / "eval"
SEED = 20261003
BOOTSTRAP_RESAMPLES = 2000          # noqa: literals - protocol v2 §6
ALPHA = 0.05                        # noqa: literals - protocol v2 §6
COVERAGES = (0.8, 0.9)              # noqa: literals - protocol v2 §5
H3_MAX_FALSE_ALARM = 0.05           # noqa: literals - protocol v2 H3
H4_MIN_GAIN = 0.02                  # noqa: literals - protocol v2 H4, 2 points
H4_MIN_TRIGGERS = 10                # noqa: literals - protocol v2 H4 precondition
H1_MIN_ERRORS = 10                  # noqa: literals - protocol v2 H1 precondition
PRIMARY_JUDGE = "zed-hosted/gemini-3.8-flash"
DATA_CHECKS = ("fanout_retry", "truncation", "null_heavy", "grounding_before_repair", "any_data_check")
BASELINES = ("no_sql", "empty_result", "any_sql_retry", "refusal")
REPEAT_LABELS = ("correct", "correct_strict", "answer_correct_before")


# ── exact expectations under random tie-breaking ───────────────────────────────

def _groups(risk: list[float], wrong: list[bool], descending: bool) -> list[tuple[int, int]]:
    """(size, wrong) per tie group of equal risk, in order (most suspect first when descending)."""
    by: dict[float, list[int]] = {}
    for r, w in zip(risk, wrong):
        by.setdefault(r, [0, 0])
        by[r][0] += 1
        by[r][1] += int(w)
    return [(by[r][0], by[r][1]) for r in sorted(by, reverse=descending)]


def expected_wrong_in_first(groups: list[tuple[int, int]], m: int) -> float:
    """Expected wrong answers among the first m, ties broken uniformly at random."""
    taken, wrong = 0, 0.0
    for size, w in groups:
        if taken + size <= m:
            taken, wrong = taken + size, wrong + w
        else:
            return wrong + (m - taken) * w / size
    return wrong


def matched_k_caught(risk: list[float], wrong: list[bool], k: int) -> float:
    """Expected wrong answers among the k most suspect (highest risk)."""
    return expected_wrong_in_first(_groups(risk, wrong, descending=True), k)


def selective_accuracy(risk: list[float], wrong: list[bool], coverage: float) -> float:
    """Expected accuracy on the round(coverage*n) least suspect answers."""
    m = max(1, round(coverage * len(risk)))
    return 1 - expected_wrong_in_first(_groups(risk, wrong, descending=False), m) / m


def aurc(risk: list[float], wrong: list[bool]) -> float:
    """Area under the risk-coverage curve: the mean, over m = 1..n, of the expected error rate on the
    m least suspect answers (lower is better)."""
    groups = _groups(risk, wrong, descending=False)
    n = len(risk)
    total, taken, cum = 0.0, 0, 0.0
    for size, w in groups:
        for j in range(1, size + 1):
            total += (cum + j * w / size) / (taken + j)
        taken, cum = taken + size, cum + w
    return total / n if n else float("nan")


def auroc(scores: list[float], wrong: list[bool]) -> float | None:
    """Probability a wrong answer gets a higher risk than a right one (ties count half). For a yes/no
    flag this equals 0.5 + (recall - false-alarm rate) / 2, so a rarely-firing flag stays near 0.5."""
    pos = [s for s, w in zip(scores, wrong) if w]
    neg = [s for s, w in zip(scores, wrong) if not w]
    if not pos or not neg:
        return None
    wins = sum((p > q) + 0.5 * (p == q) for p in pos for q in neg)
    return wins / (len(pos) * len(neg))


# ── exact binomial tools (no scipy) ───────────────────────────────────────────

def binom_cdf(x: int, n: int, p: float) -> float:
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(0, x + 1))


def cp_upper(x: int, n: int, alpha: float = ALPHA) -> float:
    """One-sided Clopper-Pearson upper bound for a rate with x events in n."""
    if n == 0:
        return 1.0
    if x >= n:
        return 1.0
    lo, hi = x / n, 1.0
    for _ in range(100):  # noqa: literals - bisection steps
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if binom_cdf(x, n, mid) > alpha else (lo, mid)
    return hi


def sign_test_p(fixed: int, broken: int) -> float:
    """One-sided exact p that fixes outnumber breaks (changed answers only)."""
    n = fixed + broken
    if n == 0:
        return 1.0
    return 1 - binom_cdf(fixed - 1, n, 0.5)


def holm(pvalues: dict[str, float], alpha: float = ALPHA) -> dict[str, bool]:
    """Holm's step-down: which hypotheses are rejected at family-wise level alpha."""
    order = sorted(pvalues, key=pvalues.get)
    out, still = {}, True
    for i, name in enumerate(order):
        still = still and pvalues[name] <= alpha / (len(order) - i)
        out[name] = still
    return out


# ── bootstrap ─────────────────────────────────────────────────────────────────

def stratified_bootstrap(strata: list[str], stat: Callable[[list[int]], float | None],
                         resamples: int = BOOTSTRAP_RESAMPLES, seed: int = SEED) -> list[float]:
    """The statistic on `resamples` resamples, each drawing with replacement within each stratum."""
    rng = random.Random(seed)
    by: dict[str, list[int]] = {}
    for i, s in enumerate(strata):
        by.setdefault(s, []).append(i)
    keys = sorted(by)
    out = []
    for _ in range(resamples):
        idx = [rng.choice(by[k]) for k in keys for _ in by[k]]
        v = stat(idx)
        if v is not None:
            out.append(v)
    return out


def interval(values: list[float]) -> tuple[float, float] | None:
    if not values:
        return None
    v = sorted(values)
    return round(v[int(ALPHA / 2 * len(v))], 4), round(v[min(len(v) - 1, int((1 - ALPHA / 2) * len(v)))], 4)


def one_sided_p(values: list[float]) -> float:
    """Share of resamples where the difference is not positive (H0: no improvement)."""
    return sum(v <= 0 for v in values) / len(values) if values else 1.0


# ── the analysis ──────────────────────────────────────────────────────────────

def scored_rows(scores_path: Path) -> list[dict]:
    return [r for r in json.loads(scores_path.read_text())["rows"] if not r.get("excluded") and not r.get("gold_failed")]


def load(scores_path: Path, label: str) -> tuple[list[dict], dict[str, list[float]]]:
    rows = scored_rows(scores_path)
    judges: dict[str, list[float]] = {}
    for f in sorted(glob.glob(str(EVAL / f"bird_judge_{label}_*.json"))):
        d = json.loads(Path(f).read_text())
        p = {str(v["id"]): v.get("p_correct") for v in d["verdicts"]}
        if all(p.get(str(r["question_id"])) is not None for r in rows):
            judges[d["model"]] = [1 - p[str(r["question_id"])] for r in rows]
    return rows, judges


def flag_stats(flags: list[bool], wrong: list[bool]) -> dict[str, Any]:
    tp = sum(f and w for f, w in zip(flags, wrong))
    k, n_wrong, n_right = sum(flags), sum(wrong), len(wrong) - sum(wrong)
    a = auroc([float(f) for f in flags], wrong)
    return {"flagged": k, "caught": tp, "precision": round(tp / k, 4) if k else None,
            "recall": round(tp / n_wrong, 4) if n_wrong else None,
            "false_alarm_rate": round((k - tp) / n_right, 4) if n_right else None,
            "auroc": round(a, 4) if a is not None else None}


def analyse(rows: list[dict], judges: dict[str, list[float]], fanout_ids: set[int] | None = None) -> dict[str, Any]:
    wrong = [not r["correct"] for r in rows]
    strata = [r["db_id"] for r in rows]
    n = len(rows)
    out: dict[str, Any] = {"n": n, "wrong": sum(wrong), "accuracy": round(1 - sum(wrong) / n, 4) if n else None,
                           "judges": sorted(judges), "signals": {}, "matched_k": {}, "risk_coverage": {}, "tests": {}}
    for name in DATA_CHECKS + BASELINES:
        out["signals"][name] = flag_stats([bool(r["signals"].get(name)) for r in rows], wrong)
    judge = judges.get(PRIMARY_JUDGE)
    scored: dict[str, list[float]] = {f"judge {m}": s for m, s in judges.items()}
    if rows and all("self_consistency_risk" in r for r in rows) and any(r.get("self_consistency_sampled") for r in rows):
        scored["self-consistency"] = [float(r["self_consistency_risk"]) for r in rows]
        out["self_consistency_unsampled"] = sum(not r.get("self_consistency_sampled") for r in rows)
    if judge is not None:
        checks = [float(bool(r["signals"].get("any_data_check"))) for r in rows]
        scored["checks then judge"] = [2 * c + j for c, j in zip(checks, judge)]   # flagged first, then by judge risk
        for name in DATA_CHECKS + BASELINES:
            flags = [bool(r["signals"].get(name)) for r in rows]
            k = sum(flags)
            if not k:
                continue
            caught = sum(f and w for f, w in zip(flags, wrong))
            judge_caught = matched_k_caught(judge, wrong, k)

            def diff(idx: list[int], flags: list[bool] = flags, k_name: str = name) -> float | None:
                f = [flags[i] for i in idx]
                w = [wrong[i] for i in idx]
                kk = sum(f)
                return None if not kk else sum(a and b for a, b in zip(f, w)) - matched_k_caught([judge[i] for i in idx], w, kk)
            boots = stratified_bootstrap(strata, diff)
            out["matched_k"][name] = {"k": k, "check_caught": caught, "judge_caught_expected": round(judge_caught, 2),
                                      "difference": round(caught - judge_caught, 2), "difference_ci95": interval(boots),
                                      "p_one_sided": round(one_sided_p(boots), 4)}
    for name, risk in scored.items():
        a = auroc(risk, wrong)
        out["risk_coverage"][name] = {"auroc": round(a, 4) if a is not None else None, "aurc": round(aurc(risk, wrong), 4),
                                      **{f"selective_accuracy_{int(c * 100)}": round(selective_accuracy(risk, wrong, c), 4)
                                         for c in COVERAGES}}
    pvalues: dict[str, float] = {}
    # H2: does ordering by the checks first lower the judge's AURC?
    if judge is not None:
        combo = scored["checks then judge"]
        gain = aurc(judge, wrong) - aurc(combo, wrong)
        boots = stratified_bootstrap(strata, lambda idx: aurc([judge[i] for i in idx], [wrong[i] for i in idx])
                                     - aurc([combo[i] for i in idx], [wrong[i] for i in idx]))
        out["tests"]["H2"] = {"aurc_judge_minus_combined": round(gain, 4), "ci95": interval(boots),
                              "p_one_sided": round(one_sided_p(boots), 4)}
        pvalues["H2"] = one_sided_p(boots)
    # H3: grounding's false alarms on answers whose text is correct.
    text_right = [r for r in rows if r.get("answer_correct_before") is True]
    if text_right:
        x = sum(bool(r["signals"].get("grounding_before_repair")) for r in text_right)
        m = len(text_right)
        p3 = binom_cdf(x, m, H3_MAX_FALSE_ALARM)       # H0: rate >= 5%; small x is evidence against it
        out["tests"]["H3"] = {"false_alarms": x, "of": m, "rate": round(x / m, 4),
                              "upper_95": round(cp_upper(x, m), 4), "p_one_sided": round(p3, 4)}
        pvalues["H3"] = p3
    # H4: the repair, by the answer label (arm a = before, arm b = after).
    triggered = [r for r in rows if r["signals"].get("repair_triggered")]
    labelled = [r for r in rows if r.get("answer_correct_before") is not None and r.get("answer_correct_after") is not None]
    fixed = sum(1 for r in labelled if not r["answer_correct_before"] and r["answer_correct_after"])
    broken = sum(1 for r in labelled if r["answer_correct_before"] and not r["answer_correct_after"])
    gain4 = (fixed - broken) / len(labelled) if labelled else 0.0
    h4: dict[str, Any] = {"triggered": len(triggered), "labelled": len(labelled), "fixed": fixed, "broken": broken,
                          "accuracy_gain": round(gain4, 4)}
    if len(triggered) < H4_MIN_TRIGGERS:
        h4["status"] = f"untestable: {len(triggered)} repairs triggered (precondition {H4_MIN_TRIGGERS})"
    else:
        h4["p_one_sided"] = round(sign_test_p(fixed, broken), 4)
        h4["gain_meets_2_points"] = gain4 >= H4_MIN_GAIN
        pvalues["H4"] = sign_test_p(fixed, broken)
    out["tests"]["H4"] = h4
    # H1: on the fan-out set only.
    if fanout_ids is not None:
        # The precondition counts double-counting errors: wrong risky answers whose result is the naive
        # join's (eval_bird.cmd_score_fanout; a lower bound).
        risky_wrong = sum(1 for r, w in zip(rows, wrong) if w and r["question_id"] in fanout_ids)
        fanout_errors = sum(1 for r in rows if r.get("fanout_error"))
        h1: dict[str, Any] = {"risky_wrong": risky_wrong, "fanout_errors": fanout_errors,
                              **out["matched_k"].get("fanout_retry", {"k": 0, "check_caught": 0})}
        if fanout_errors < H1_MIN_ERRORS:
            h1["status"] = f"untestable: {fanout_errors} double-counting errors (precondition {H1_MIN_ERRORS})"
        elif "fanout_retry" not in out["matched_k"]:
            h1["status"] = "the fan-out check never fired"
            pvalues["H1"] = 1.0
        else:
            pvalues["H1"] = out["matched_k"]["fanout_retry"]["p_one_sided"]
        out["tests"]["H1"] = h1
    out["holm"] = {"pvalues": {k: round(v, 4) for k, v in pvalues.items()}, "rejected": holm(pvalues)}
    return out


def flip_rate(first: list[dict], second: list[dict]) -> dict[str, Any]:
    """Run-to-run variation (protocol v2 §5): over the questions scored in both runs, how often each label
    and each signal differs between the first run and the second."""
    by_id = {str(r["question_id"]): r for r in first}
    pairs = [(by_id[str(r["question_id"])], r) for r in second if str(r["question_id"]) in by_id]

    def count(get: Callable[[dict], Any]) -> dict[str, Any]:
        both = [(get(a), get(b)) for a, b in pairs if get(a) is not None and get(b) is not None]
        flips = sum(x != y for x, y in both)
        return {"n": len(both), "flips": flips, "rate": round(flips / len(both), 4) if both else None}

    out: dict[str, Any] = {"questions": len(pairs), "labels": {}, "signals": {}}
    for key in REPEAT_LABELS:
        out["labels"][key] = count(lambda r, key=key: r.get(key))
    if pairs:
        out["labels"]["correct"].update(
            right_to_wrong=sum(1 for a, b in pairs if a["correct"] and not b["correct"]),
            wrong_to_right=sum(1 for a, b in pairs if not a["correct"] and b["correct"]),
            accuracy_first=round(sum(a["correct"] for a, _ in pairs) / len(pairs), 4),
            accuracy_second=round(sum(b["correct"] for _, b in pairs) / len(pairs), 4))
    for name in DATA_CHECKS + BASELINES:
        out["signals"][name] = count(lambda r, name=name: bool(r["signals"].get(name)))
    return out


def confirmatory_holm(fanout: dict[str, Any], heldout: dict[str, Any]) -> dict[str, Any]:
    """The final Holm step (protocol v2 §6): H1 from the fan-out set's analysis, H2-H4 from the held-out
    analysis; untestable tests carry no p-value and are left out. The p-values are the files' (4 places)."""
    pvalues = {k: v for k, v in heldout["holm"]["pvalues"].items() if k != "H1"}
    if "H1" in fanout["holm"]["pvalues"]:
        pvalues["H1"] = fanout["holm"]["pvalues"]["H1"]
    pvalues = dict(sorted(pvalues.items()))
    tests = {"H1": fanout["tests"].get("H1", {}), **{t: heldout["tests"].get(t, {}) for t in ("H2", "H3", "H4")}}
    return {"pvalues": pvalues, "rejected": holm(pvalues),
            "untestable": [t for t, v in tests.items() if str(v.get("status", "")).startswith("untestable")]}


FORMAT_ONLY = "output format only"      # annotate_bird.ERROR_TYPES: a scorer false negative


def catch_matrix(rows: list[dict], annotations: dict, signals: tuple[str, ...]) -> dict[str, Any]:
    """RQ2: per error type (blind annotation of every wrong answer), how many each signal flagged."""
    by_type: dict[str, list[dict]] = {}
    for r in rows:
        t = annotations.get(str(r["question_id"]))
        if t and not r["correct"]:
            by_type.setdefault(t, []).append(r)
    return {t: {"n": len(rs), **{s: sum(bool(r["signals"].get(s)) for r in rs) for s in signals}}
            for t, rs in sorted(by_type.items(), key=lambda kv: -len(kv[1]))}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--annotations", type=Path, help="the blind error-type worksheet (annotate_bird.py errors), for RQ2 "
                                                     "and the exploratory format-only sensitivity")
    ap.add_argument("--scores", type=Path, required=True)
    ap.add_argument("--label", required=True, help="the answers' label, to find the judge files")
    ap.add_argument("--fanout-set", type=Path, help="the frozen fan-out set (risky question ids), for H1")
    ap.add_argument("--heldout-analysis", type=Path, help="with --fanout-set: the held-out analysis file, for the final "
                                                         "Holm step over H1-H4")
    ap.add_argument("--repeat-of", type=Path, help="the first run's scores, when --scores is a repeat run: run-to-run "
                                                  "variation")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    rows, judges = load(args.scores, args.label)
    fanout = set(json.loads(args.fanout_set.read_text())["risky_ids"]) if args.fanout_set else None
    report = {"scores": str(args.scores), "label": args.label, **analyse(rows, judges, fanout)}
    if args.annotations:
        ann = {str(i["question_id"]): i["annotation"] for i in json.loads(args.annotations.read_text())["items"] if i["annotation"]}
        judge = judges.get(PRIMARY_JUDGE)
        extra = DATA_CHECKS + BASELINES
        report["rq2_catch_matrix"] = catch_matrix(rows, ann, extra)
        if judge is not None:   # the judge's "flag" at the same rate as any data check, for the matrix: its k most suspect
            k = sum(bool(r["signals"].get("any_data_check")) for r in rows)
            top = set(sorted(range(len(rows)), key=lambda i: -judge[i])[:k])
            for t in report["rq2_catch_matrix"]:
                report["rq2_catch_matrix"][t]["judge_top_k"] = sum(1 for i, r in enumerate(rows) if i in top and not r["correct"]
                                                                   and ann.get(str(r["question_id"])) == t)
        # Exploratory, not pre-registered as a test: answers whose only fault is output format count as correct.
        relabelled = [{**r, "correct": r["correct"] or ann.get(str(r["question_id"])) == FORMAT_ONLY} for r in rows]
        sens = analyse(relabelled, judges)
        report["sensitivity_format_only_correct"] = {k: sens[k] for k in ("n", "wrong", "accuracy", "signals", "matched_k", "risk_coverage")}
        report["sensitivity_format_only_correct"]["tests_exploratory"] = sens["tests"]
    if args.fanout_set and args.heldout_analysis:
        report["holm_confirmatory"] = {"heldout_analysis": str(args.heldout_analysis),
                                       **confirmatory_holm(report, json.loads(args.heldout_analysis.read_text()))}
    if args.repeat_of:
        report["run_to_run"] = {"first_run": str(args.repeat_of), **flip_rate(scored_rows(args.repeat_of), rows)}
    out = args.out or EVAL / f"bird_analysis_v2_{args.scores.stem.removeprefix('bird_scores_')}.json"
    out.write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
