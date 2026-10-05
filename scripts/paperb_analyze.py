#!/usr/bin/env python3
"""
scripts/paperb_analyze.py
Paper B, Part S analysis (docs/research/detect_then_fallback_study.md v2, §5; changes 4-5). No model calls.

Inputs (docs/eval/): paperb_pool.json (21 labelled, clustered candidates per question, in pool order:
0-5 GPT-5 mini, 0 = its pipeline answer; 6-11 gpt-oss, 6 = its pipeline answer; 12-20 standalone),
paperb_judge_gemini.json, paperb_scores_*.json (verifiers in rotating rounds; Laya zero-shot; Jev), and
paperb_examples.json (QS3). Scores missing from a file are skipped, and the file is listed as missing.

Definitions (fixed before any result):
- A selector scores each result CLUSTER: vote = its size; judge = the judge's score of the cluster;
  verifier-max = the best member's score (that member is the selected candidate); verifier + vote = the
  members' summed scores. Ties between the top clusters count as the expected accuracy under uniform
  random tie-breaking. A candidate that did not run has no cluster and is never selected; a question with
  no cluster scores 0.
- "The judge doubts" = the judge's score of the first answer's cluster is below 0.5 (50 of 100); a first
  answer that did not run counts as doubted.
- P6's tau, per round: the value (from -1, the candidates' risks on the calibration fold, and 1) that
  maximises the calibration fold's accuracy, the largest such value on a tie; fallback when risk > tau.
- Verified label: per round, the calibration fold's scores of the first answer, by fixed-sequence testing
  from the highest score down, stopping at the first threshold whose one-sided 95% Clopper-Pearson upper
  error bound exceeds the target; applied to the test fold.
- H1-H3 (change 5): one-sided paired sign-flip permutation test on per-question expected correctness
  (100,000 draws, seed 20261005); with no fractional outcome it is exact McNemar, and it is then computed
  exactly. Holm across the three.
- Intervals: paired bootstrap stratified by database, 2,000 resamples, seed 20261005.

    python3 scripts/paperb_analyze.py [--out docs/eval/paperb_analysis.json]
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyze_bird as ab  # noqa: E402
from paperb_folds import SEED, folds, rounds  # noqa: E402

EVAL = Path(__file__).resolve().parents[1] / "docs" / "eval"
RESAMPLES = 2000            # noqa: literals - pre-registered
PERMUTATIONS = 100_000      # noqa: literals - change 5
DOUBT = 0.5                 # noqa: literals - pre-registered: a judge score below 50 of 100
TARGETS = (0.05, 0.10)      # noqa: literals - verified-label error targets (5% primary)
FIRST, OSS_FIRST = 0, 6     # pool positions of GPT-5 mini's and gpt-oss's pipeline answers
GPT5_SLICE = range(0, 6)    # noqa: literals - GPT-5 mini's answer and its 5 samples
ROTATION = {"modernbert_real": "paperb_scores_modernbert_real_rotation.json",
            "modernbert_mutation": "paperb_scores_modernbert_mutation_rotation.json",
            "laya_finetuned": "paperb_scores_laya_finetuned_rotation.json"}
ZERO_SHOT = {"laya_zeroshot": "paperb_scores_laya_zeroshot.json", "jev_zeroshot": "paperb_scores_jev_zeroshot.json"}


# ── selection with exact tie expectation ───────────────────────────────────────

def clusters(q: dict, members: range | None = None) -> dict[int, list[int]]:
    out: dict[int, list[int]] = {}
    for i, c in enumerate(q["candidates"]):
        if c["cluster"] is not None and (members is None or i in members):
            out.setdefault(c["cluster"], []).append(i)
    return out


def expected(q: dict, score: Callable[[int, list[int]], tuple[float, int] | None], members: range | None = None) -> float:
    """Expected correctness of picking a top cluster at random; `score(cluster, member_ids)` gives the
    cluster's score and its representative candidate, or None to skip the cluster."""
    scored = []
    for k, ids in clusters(q, members).items():
        s = score(k, ids)
        if s is not None:
            scored.append(s)
    if not scored:
        return 0.0
    best = max(s for s, _ in scored)
    top = [rep for s, rep in scored if s == best]
    return sum(bool(q["candidates"][rep]["correct"]) for rep in top) / len(top)


def vote(q: dict, members: range | None = None) -> float:
    return expected(q, lambda k, ids: (len(ids), ids[0]), members)


def cluster_max(q: dict, s: Callable[[int], float | None]) -> float:
    def score(k: int, ids: list[int]) -> tuple[float, int] | None:
        vals = [(s(i), i) for i in ids if s(i) is not None]
        if not vals:
            return None
        best = max(v for v, _ in vals)
        return best, next(i for v, i in vals if v == best)
    return expected(q, score)


def cluster_sum(q: dict, s: Callable[[int], float | None]) -> float:
    return expected(q, lambda k, ids: (sum(s(i) or 0.0 for i in ids), ids[0]))


def judge_select(q: dict, judge: dict[str, float]) -> float:
    return expected(q, lambda k, ids: (judge[f"{q['question_id']}|{k}"], ids[0])
                    if judge.get(f"{q['question_id']}|{k}") is not None else None)


def judge_of_first(q: dict, judge: dict[str, float]) -> float:
    k = q["candidates"][FIRST]["cluster"]
    return 0.0 if k is None else (judge.get(f"{q['question_id']}|{k}") or 0.0)


# ── statistics ─────────────────────────────────────────────────────────────────

def sign_flip_p(a: list[float], b: list[float]) -> dict[str, Any]:
    """One-sided: a is right more often than b (change 5)."""
    d = [x - y for x, y in zip(a, b) if x != y]
    obs = sum(d)
    out: dict[str, Any] = {"a_mean": round(statistics.fmean(a), 4), "b_mean": round(statistics.fmean(b), 4),
                           "discordant": len(d), "a_better": round(sum(x for x in d if x > 0), 3),
                           "b_better": round(-sum(x for x in d if x < 0), 3)}
    if not d:
        out["p_one_sided"] = 1.0
    elif all(abs(x) == 1 for x in d):            # binary outcomes: exact McNemar
        n, wins = len(d), sum(x > 0 for x in d)
        out["p_one_sided"] = round(sum(math.comb(n, k) for k in range(wins, n + 1)) / 2 ** n, 5)
        out["test"] = "exact McNemar"
    else:
        rng = random.Random(SEED)
        hits = sum(sum(x if rng.random() < 0.5 else -x for x in d) >= obs - 1e-12 for _ in range(PERMUTATIONS))
        out["p_one_sided"] = round((hits + 1) / (PERMUTATIONS + 1), 5)
        out["test"] = "sign-flip permutation"
    return out


def diff_interval(strata: list[str], a: list[float], b: list[float]) -> dict[str, Any]:
    boots = ab.stratified_bootstrap(strata, lambda idx: statistics.fmean(a[i] - b[i] for i in idx), RESAMPLES, SEED)
    return {"difference": round(statistics.fmean(x - y for x, y in zip(a, b)), 4), "ci95": ab.interval(boots)}


def min_verified(target: float) -> int:
    """The smallest verified set whose one-sided 95% Clopper-Pearson bound can reach `target` (no errors)."""
    n = 1
    while ab.cp_upper(0, n) > target:
        n += 1
    return n


def fixed_sequence_threshold(pairs: list[tuple[float, bool]], target: float) -> float:
    """Fixed-sequence testing over thresholds from the highest score down (change 5): thresholds whose
    verified set is smaller than min_verified(target) can never pass and are skipped (that depends on the
    scores only, not the labels); from the first testable one, keep lowering while the bound holds and
    stop at the first failure. +inf when nothing passes."""
    need, chosen = min_verified(target), math.inf
    for t in sorted({s for s, _ in pairs}, reverse=True):
        kept = [ok for s, ok in pairs if s >= t]
        if len(kept) < need:
            continue
        if ab.cp_upper(sum(not ok for ok in kept), len(kept)) > target:
            break
        chosen = t
    return chosen


# ── loading ────────────────────────────────────────────────────────────────────

def load_scores() -> tuple[dict[str, dict], dict[str, dict], list[str]]:
    rot, zero, missing = {}, {}, []
    for name, f in ROTATION.items():
        p = EVAL / f
        if p.exists() and len(json.loads(p.read_text()).get("rounds", {})) == 5:   # noqa: literals - all rounds
            rot[name] = json.loads(p.read_text())
        else:
            missing.append(name)
    for name, f in ZERO_SHOT.items():
        p = EVAL / f
        if p.exists():
            d = json.loads(p.read_text())
            zero[name] = d.get("test") or d.get("scores") or {}
        else:
            missing.append(name)
    return rot, zero, missing


def cost_inputs() -> dict[str, Any]:
    """Tokens, calls and milliseconds per question for each source, from the run records."""
    import eval_bird as eb
    out: dict[str, Any] = {}
    for model, label in (("gpt-5-mini", "heldout_gpt5mini"), ("gpt-oss-120b", "heldout_gptoss")):
        ans = json.loads(eb.answers_path(label).read_text())["answers"]
        per = {}
        for qid, a in ans.items():
            calls = a.get("calls") or []
            sc = a.get("self_consistency") or {}
            per[int(qid)] = {"pipeline_calls": len(calls), "pipeline_in": sum(c.get("tokens_in") or 0 for c in calls),
                             "pipeline_out": sum(c.get("tokens_out") or 0 for c in calls),
                             "pipeline_ms": sum(c.get("ms") or 0 for c in calls),
                             "samples": sc.get("k") or 0, "samples_in": sc.get("tokens_in") or 0,
                             "samples_out": sc.get("tokens_out") or 0}
        out[model] = per
    std = json.loads((EVAL / "paperb_candidates.json").read_text())["candidates"]
    per_q: dict[int, dict[str, float]] = {}
    for key, rec in std.items():
        q = per_q.setdefault(int(key.split("|")[0]), {"calls": 0, "in": 0, "out": 0, "max_ms": 0})
        q["calls"] += 1
        q["in"] += rec.get("tokens_in") or 0
        q["out"] += rec.get("tokens_out") or 0
        q["max_ms"] = max(q["max_ms"], rec.get("ms") or 0)
    out["standalone"] = per_q
    return out


# ── the analysis ───────────────────────────────────────────────────────────────

def analyse(pool: list[dict], judge: dict[str, float], rot: dict[str, dict], zero: dict[str, dict],
            missing: list[str], examples: dict[str, Any] | None, costs: dict[str, Any] | None) -> dict[str, Any]:
    fold_of = folds(pool)
    rnds = rounds(fold_of)
    strata = [q["db_id"] for q in pool]
    right = [bool(q["candidates"][FIRST]["correct"]) for q in pool]
    report: dict[str, Any] = {"n": len(pool), "missing": missing, "policies": {}, "vs_first": {},
                              "pool_sources": {}}
    out: dict[str, list[float]] = {"P0 first answer": [float(x) for x in right]}

    def test_score(name: str) -> Callable[[dict], Callable[[int], float | None]]:
        src = rot[name]["test"] if name in rot else zero[name]
        return lambda q: (lambda i: src.get(f"{q['question_id']}|{i}"))

    # Judge-based policies.
    if judge:
        doubted = [judge_of_first(q, judge) < DOUBT for q in pool]
        out["P1 judge-triggered re-vote (GPT-5 mini's 6)"] = [vote(q, GPT5_SLICE) if d else float(r)
                                                             for q, d, r in zip(pool, doubted, right)]
        out["P2 judge-triggered escalation (gpt-oss answer)"] = [float(q["candidates"][OSS_FIRST]["correct"]) if d else float(r)
                                                                for q, d, r in zip(pool, doubted, right)]
        p4 = [judge_select(q, judge) for q in pool]
        out["P4 pool judge-max"] = p4
        out["P7 judge-triggered cascade"] = [s if d else float(r) for s, d, r in zip(p4, doubted, right)]
        report["judge_doubts_first"] = round(sum(doubted) / len(pool), 4)
    out["P3 pool vote"] = [vote(q) for q in pool]
    for name, label in (("modernbert_real", "P5 pool ModernBERT-max (real)"),
                        ("modernbert_mutation", "P5m pool ModernBERT-max (mutation)"),
                        ("laya_finetuned", "P5L pool Laya-max (fine-tuned)"),
                        ("laya_zeroshot", "pool Laya-max (zero-shot)"), ("jev_zeroshot", "pool Jev-max (zero-shot)")):
        if name in rot or name in zero:
            ts = test_score(name)
            out[label] = [cluster_max(q, ts(q)) for q in pool]
            if name == "modernbert_real":
                out["P5v pool ModernBERT + vote"] = [cluster_sum(q, ts(q)) for q in pool]
    # P6: ModernBERT-triggered cascade, tau from each round's calibration fold.
    if "modernbert_real" in rot:
        rec = rot["modernbert_real"]
        p6 = [0.0] * len(pool)
        fell, taus = 0, {}
        index = {q["question_id"]: n for n, q in enumerate(pool)}
        for rnd in rnds:
            cal = rec["calib"][str(rnd["round"])]
            cal_q = [q for q in pool if q["question_id"] in rnd["calib"]]
            risk_c = {q["question_id"]: 1 - (cal.get(f"{q['question_id']}|{FIRST}") or 0.0) for q in cal_q}
            sel_c = {q["question_id"]: cluster_max(q, lambda i, q=q: cal.get(f"{q['question_id']}|{i}")) for q in cal_q}
            first_c = {q["question_id"]: float(q["candidates"][FIRST]["correct"]) for q in cal_q}

            def acc(tau: float) -> float:
                return statistics.fmean(sel_c[x] if risk_c[x] > tau else first_c[x] for x in risk_c)
            tau = max(sorted({-1.0, 1.0} | set(risk_c.values())), key=lambda t: (acc(t), t))
            taus[str(rnd["round"])] = round(tau, 5)
            for qid in rnd["test"]:
                q = pool[index[qid]]
                risk = 1 - (rec["test"].get(f"{qid}|{FIRST}") or 0.0)
                if risk > tau:
                    fell += 1
                    p6[index[qid]] = cluster_max(q, lambda i, q=q: rec["test"].get(f"{q['question_id']}|{i}"))
                else:
                    p6[index[qid]] = float(q["candidates"][FIRST]["correct"])
        out["P6 ModernBERT-triggered cascade"] = p6
        report["P6_fallback_share"] = round(fell / len(pool), 4)
        report["P6_tau_by_round"] = taus
    out["oracle (any candidate right)"] = [float(any(c["correct"] for c in q["candidates"])) for q in pool]

    for name, vals in out.items():
        report["policies"][name] = round(statistics.fmean(vals), 4)
        if name != "P0 first answer":
            report["vs_first"][name] = diff_interval(strata, vals, out["P0 first answer"])

    # Pre-specified comparisons (H1-H3, change 5), Holm.
    tests: dict[str, Any] = {}
    pairs = {"H1": ("P5 pool ModernBERT-max (real)", "P3 pool vote"),
             "H2": ("P5 pool ModernBERT-max (real)", "P5m pool ModernBERT-max (mutation)"),
             "H3": ("P6 ModernBERT-triggered cascade", "P0 first answer")}
    for h, (a, b) in pairs.items():
        if a in out and b in out:
            tests[h] = {"a": a, "b": b, **sign_flip_p(out[a], out[b])}
    report["tests"] = tests
    report["holm"] = ab.holm({h: t["p_one_sided"] for h, t in tests.items()})

    # Detection of the first answer's errors.
    wrong = [not r for r in right]
    det: dict[str, Any] = {}
    gates: dict[str, list[float | None]] = {}
    if judge:
        gates["judge (Gemini, 0-100)"] = [judge_of_first(q, judge) for q in pool]
    for name in list(rot) + list(zero):
        src = rot[name]["test"] if name in rot else zero[name]
        gates[name] = [src.get(f"{q['question_id']}|{FIRST}") for q in pool]
    for name, s in gates.items():
        risk = [1 - v if v is not None else 0.5 for v in s]
        det[name] = {"auroc": round(ab.auroc(risk, wrong), 4), "aurc": round(ab.aurc(risk, wrong), 4),
                     "missing": sum(v is None for v in s)}
    report["detection_first_answer"] = det

    # Verified label (QS4).
    ver: dict[str, Any] = {}
    sources: dict[str, Callable[[int, int, str], float | None]] = {}
    for name in ("modernbert_real", "laya_finetuned"):
        if name in rot:
            rec = rot[name]
            sources[name] = (lambda qid, r, role, rec=rec: rec["test"].get(f"{qid}|{FIRST}") if role == "test"
                             else rec["calib"][str(r)].get(f"{qid}|{FIRST}"))
    if judge:
        jf = {q["question_id"]: judge_of_first(q, judge) for q in pool}
        sources["judge"] = lambda qid, r, role: jf[qid]
    first_right = {q["question_id"]: bool(q["candidates"][FIRST]["correct"]) for q in pool}
    for name, get in sources.items():
        for target in TARGETS:
            verified = wrong_v = 0
            thresholds = {}
            for rnd in rnds:
                pairs_c = [(get(qid, rnd["round"], "calib"), first_right[qid]) for qid in rnd["calib"]]
                pairs_c = [(s, ok) for s, ok in pairs_c if s is not None]
                t = fixed_sequence_threshold(pairs_c, target)
                thresholds[str(rnd["round"])] = None if math.isinf(t) else round(t, 5)
                for qid in rnd["test"]:
                    s = get(qid, rnd["round"], "test")
                    if s is not None and s >= t:
                        verified += 1
                        wrong_v += not first_right[qid]
            ver[f"{name} @ {int(target * 100)}%"] = {
                "verified": verified, "of": len(pool), "coverage": round(verified / len(pool), 4),
                "observed_error": round(wrong_v / verified, 4) if verified else None,
                "error_upper_95": round(ab.cp_upper(wrong_v, verified), 4) if verified else None,
                "thresholds_by_round": thresholds}
    report["verified_label"] = ver

    # Pool sources.
    for lo, hi, name in ((0, 6, "GPT-5 mini (pipeline answer + 5 samples)"), (6, 12, "gpt-oss-120b (pipeline answer + 5 samples)"),
                         (12, 15, "deepseek-v4.1-flash"), (15, 18, "qwen3-coder-30b-a3b-instruct"), (18, 21, "glm-5.3-flash")):
        cs = [c for q in pool for c in q["candidates"][lo:hi]]
        report["pool_sources"][name] = {"candidates": len(cs), "correct": round(sum(c["correct"] for c in cs) / len(cs), 4),
                                        "ran": round(sum(c["ran"] for c in cs) / len(cs), 4)}

    # QS3: approved examples.
    if examples:
        by_q: dict[str, dict[int, list[bool]]] = {}
        for key, rec in examples["candidates"].items():
            qid, arm, _ = key.split("|")
            by_q.setdefault(arm, {}).setdefault(int(qid), []).append(bool(rec.get("correct")))
        qs = [q["question_id"] for q in pool]
        acc = {arm: [statistics.fmean(by_q.get(arm, {}).get(q, [False])) for q in qs] for arm in ("E0", "E1", "E2")}
        report["approved_examples"] = {
            "accuracy": {arm: round(statistics.fmean(v), 4) for arm, v in acc.items()},
            "samples_missing": {arm: sum(3 - len(by_q.get(arm, {}).get(q, [])) for q in qs) for arm in acc},   # noqa: literals - 3 samples
            "E1 - E0": diff_interval(strata, acc["E1"], acc["E0"]),
            "E2 - E0": diff_interval(strata, acc["E2"], acc["E0"])}
        if "P5 pool ModernBERT-max (real)" in out:
            report["approved_examples"]["verifier gains, same folds"] = {
                "P5 - P3": report["vs_first"]["P5 pool ModernBERT-max (real)"]["difference"] - report["vs_first"]["P3 pool vote"]["difference"],
                "P6 - P0": report["vs_first"].get("P6 ModernBERT-triggered cascade", {}).get("difference")}

    if costs:
        report["cost_per_question"] = cost_summary(pool, costs, judge)
    return report


def cost_summary(pool: list[dict], costs: dict[str, Any], judge: dict[str, float]) -> dict[str, Any]:
    """Mean extra model calls and tokens per question over the first answer, for each kind of fallback."""
    g5, oss, std = costs["gpt-5-mini"], costs["gpt-oss-120b"], costs["standalone"]
    jv = judge_verdicts_tokens()

    def mean(f: Callable[[int], float]) -> float:
        return round(statistics.fmean(f(q["question_id"]) for q in pool), 1)

    every_candidate = lambda q: (g5[q]["samples"] + oss[q]["pipeline_calls"] + oss[q]["samples"] + std.get(q, {}).get("calls", 0))  # noqa: E731
    every_token = lambda q: (g5[q]["samples_in"] + g5[q]["samples_out"] + oss[q]["pipeline_in"] + oss[q]["pipeline_out"]  # noqa: E731
                             + oss[q]["samples_in"] + oss[q]["samples_out"] + std.get(q, {}).get("in", 0) + std.get(q, {}).get("out", 0))
    return {"first answer (GPT-5 mini pipeline)": {"calls": mean(lambda q: g5[q]["pipeline_calls"]),
                                                  "tokens": mean(lambda q: g5[q]["pipeline_in"] + g5[q]["pipeline_out"])},
            "the other 20 candidates": {"calls": mean(every_candidate), "tokens": mean(every_token)},
            "judge, every cluster": {"calls": round(len(jv) / len(pool), 2),
                                     "tokens": round(sum(jv.values()) / len(pool), 1)},
            "re-vote: 5 GPT-5 mini samples": {"calls": mean(lambda q: g5[q]["samples"]),
                                             "tokens": mean(lambda q: g5[q]["samples_in"] + g5[q]["samples_out"])},
            "escalation: one gpt-oss pipeline run": {"calls": mean(lambda q: oss[q]["pipeline_calls"]),
                                                    "tokens": mean(lambda q: oss[q]["pipeline_in"] + oss[q]["pipeline_out"])}}


def judge_verdicts_tokens() -> dict[str, int]:
    p = EVAL / "paperb_judge_gemini.json"
    if not p.exists():
        return {}
    return {k: (v.get("tokens_in") or 0) + (v.get("tokens_out") or 0)
            for k, v in json.loads(p.read_text())["verdicts"].items() if v.get("p_correct") is not None}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=EVAL / "paperb_analysis.json")
    args = ap.parse_args()
    pool = json.loads((EVAL / "paperb_pool.json").read_text())["questions"]
    jp = EVAL / "paperb_judge_gemini.json"
    judge = ({k: v.get("p_correct") for k, v in json.loads(jp.read_text())["verdicts"].items()}
             if jp.exists() else {})
    # A failed candidate has no cluster and no verdict; record that the judge never saw it.
    rot, zero, missing = load_scores()
    if not judge:
        missing.append("judge")
    ep = EVAL / "paperb_examples.json"
    examples = json.loads(ep.read_text()) if ep.exists() else None
    report = analyse(pool, judge, rot, zero, missing, examples, cost_inputs())
    args.out.write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
