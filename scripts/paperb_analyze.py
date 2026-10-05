#!/usr/bin/env python3
"""
scripts/paperb_analyze.py
Paper B's analysis (docs/research/detect_then_fallback_study.md): selection accuracy of every selector
over the 21-candidate pools, H1-H3 (McNemar one-sided, Holm), the verifier-triggered cascade, detection
of the first answer's errors, and the conformal "verified" label. No model calls. Selectors whose scores
are missing are skipped and listed.

    python3 scripts/paperb_analyze.py
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyze_bird as ab  # noqa: E402
from paperb_verifiers import EVAL, folds  # noqa: E402

OUT = EVAL / "paperb_analysis.json"
TARGET_ERROR = 0.05         # noqa: literals - pre-registered verified-label error target
H1_VERIFIER = "modernbert_real_5fold"
SCORE_FILES = {"modernbert_real_5fold": "paperb_scores_modernbert_real_5fold.json",
               "modernbert_mutation_5fold": "paperb_scores_modernbert_mutation_5fold.json",
               "qwen_real_5fold": "paperb_scores_qwen_real_5fold.json",
               "modernbert_real_lodo": "paperb_scores_modernbert_real_lodo.json",
               "laya_zeroshot": "paperb_scores_laya_zeroshot.json",
               "jev_zeroshot": "paperb_scores_jev_zeroshot.json"}


def mcnemar_one_sided(a: list[bool], b: list[bool]) -> dict[str, Any]:
    """H: a is right more often than b. Exact binomial on the discordant pairs."""
    win = sum(1 for x, y in zip(a, b) if x and not y)
    loss = sum(1 for x, y in zip(a, b) if y and not x)
    n = win + loss
    p = sum(math.comb(n, k) for k in range(win, n + 1)) / 2 ** n if n else 1.0
    return {"a_only": win, "b_only": loss, "p_one_sided": round(p, 4)}


def first_of(q: dict, cluster: int) -> int:
    return next(i for i, c in enumerate(q["candidates"]) if c["cluster"] == cluster)


def select_vote(q: dict) -> int:
    sizes: dict[int, int] = {}
    for c in q["candidates"]:
        if c["cluster"] is not None:
            sizes[c["cluster"]] = sizes.get(c["cluster"], 0) + 1
    if not sizes:
        return 0
    best = max(sizes.values())
    top = [k for k, v in sizes.items() if v == best]
    k = q["candidates"][0]["cluster"] if q["candidates"][0]["cluster"] in top else min(top)
    return first_of(q, k)


def select_max(q: dict, score: Callable[[int], float | None]) -> int:
    best, idx = -1.0, 0
    for i in range(len(q["candidates"])):
        s = score(i)
        if s is not None and s > best:
            best, idx = s, i
    return idx


def select_cluster_sum(q: dict, score: Callable[[int], float | None]) -> int:
    sums: dict[int, float] = {}
    for i, c in enumerate(q["candidates"]):
        if c["cluster"] is not None:
            sums[c["cluster"]] = sums.get(c["cluster"], 0.0) + (score(i) or 0.0)
    if not sums:
        return 0
    return first_of(q, max(sums, key=lambda k: (sums[k], -k)))


def conformal_threshold(pairs: list[tuple[float, bool]]) -> float:
    """Lowest threshold whose verified set (score >= t) has Clopper-Pearson upper error <= target."""
    best = 1.01
    for t in sorted({s for s, _ in pairs}, reverse=True):
        kept = [ok for s, ok in pairs if s >= t]
        if ab.cp_upper(sum(not ok for ok in kept), len(kept)) <= TARGET_ERROR:
            best = t
    return best


def main() -> int:
    pool = json.loads((EVAL / "paperb_pool.json").read_text())["questions"]
    scores = {name: json.loads((EVAL / f).read_text())["scores"] for name, f in SCORE_FILES.items() if (EVAL / f).exists()}
    judge_file = EVAL / "paperb_judge_gemini.json"
    judge = {k: v.get("p_correct") for k, v in json.loads(judge_file.read_text())["verdicts"].items()} if judge_file.exists() else {}
    fold_of = folds(pool, "5fold")
    report: dict[str, Any] = {"n": len(pool), "missing": [n for n in SCORE_FILES if n not in scores], "selection": {}}

    def right(q: dict, i: int) -> bool:
        return bool(q["candidates"][i]["correct"])

    picks: dict[str, list[int]] = {"first answer (GPT-5 mini)": [0] * len(pool),
                                   "majority vote": [select_vote(q) for q in pool]}
    if judge:
        picks["judge max (Gemini)"] = [select_max(q, lambda i, q=q: (judge.get(f"{q['question_id']}|{q['candidates'][i]['cluster']}")
                                                                     if q["candidates"][i]["cluster"] is not None else 0.0)) for q in pool]
    for name, s in scores.items():
        picks[f"{name} max"] = [select_max(q, lambda i, q=q, s=s: s.get(f"{q['question_id']}|{i}")) for q in pool]
        picks[f"{name} + vote"] = [select_cluster_sum(q, lambda i, q=q, s=s: s.get(f"{q['question_id']}|{i}")) for q in pool]
    outcome = {name: [right(q, i) for q, i in zip(pool, p)] for name, p in picks.items()}
    outcome["oracle (any candidate right)"] = [any(c["correct"] for c in q["candidates"]) for q in pool]
    for name, o in outcome.items():
        report["selection"][name] = round(sum(o) / len(o), 4)

    tests: dict[str, Any] = {}
    pvalues: dict[str, float] = {}
    if H1_VERIFIER in scores:
        tests["H1"] = mcnemar_one_sided(outcome[f"{H1_VERIFIER} max"], outcome["majority vote"])
        pvalues["H1"] = tests["H1"]["p_one_sided"]
    if "modernbert_mutation_5fold" in scores and H1_VERIFIER in scores:
        tests["H2"] = mcnemar_one_sided(outcome[f"{H1_VERIFIER} max"], outcome["modernbert_mutation_5fold max"])
        pvalues["H2"] = tests["H2"]["p_one_sided"]
    if H1_VERIFIER in scores:
        s = scores[H1_VERIFIER]
        risk0 = {q["question_id"]: 1 - s[f"{q['question_id']}|0"] for q in pool}
        sel = {q["question_id"]: right(q, i) for q, i in zip(pool, picks[f"{H1_VERIFIER} max"])}
        first = {q["question_id"]: right(q, 0) for q in pool}
        cascade, fell_back = [], 0
        for q in pool:
            others = [p["question_id"] for p in pool if fold_of[p["question_id"]] != fold_of[q["question_id"]]]
            def acc(tau: float, others: list[int] = others) -> float:
                return sum(sel[o] if risk0[o] > tau else first[o] for o in others) / len(others)
            taus = sorted({round(risk0[o], 3) for o in others} | {1.0})
            tau = max(taus, key=lambda t: (acc(t), t))
            fb = risk0[q["question_id"]] > tau
            fell_back += fb
            cascade.append(sel[q["question_id"]] if fb else first[q["question_id"]])
        report["selection"]["cascade (ModernBERT-triggered)"] = round(sum(cascade) / len(cascade), 4)
        report["cascade_fallback_share"] = round(fell_back / len(pool), 4)
        tests["H3"] = mcnemar_one_sided(cascade, [first[q["question_id"]] for q in pool])
        pvalues["H3"] = tests["H3"]["p_one_sided"]
        # Verified label on the first answer: threshold per fold from the other folds' scores.
        verified, wrong_verified = 0, 0
        for k in sorted(set(fold_of.values())):
            train = [(s[f"{q['question_id']}|0"], first[q["question_id"]]) for q in pool if fold_of[q["question_id"]] != k]
            t = conformal_threshold(train)
            for q in pool:
                if fold_of[q["question_id"]] == k and s[f"{q['question_id']}|0"] >= t:
                    verified += 1
                    wrong_verified += not first[q["question_id"]]
        report["verified_label_first_answer"] = {"target_error": TARGET_ERROR, "verified": verified, "of": len(pool),
                                                 "coverage": round(verified / len(pool), 4),
                                                 "observed_error": round(wrong_verified / verified, 4) if verified else None}
    report["tests"] = tests
    report["holm"] = {"pvalues": pvalues, "rejected": ab.holm(pvalues)}
    wrong0 = [not right(q, 0) for q in pool]
    report["detection_first_answer_auroc"] = {name: round(ab.auroc([1 - s.get(f"{q['question_id']}|0", 0.5) for q in pool], wrong0), 4)
                                              for name, s in scores.items()}
    if judge:
        report["detection_first_answer_auroc"]["judge (Gemini)"] = round(ab.auroc(
            [1 - (judge.get(f"{q['question_id']}|{q['candidates'][0]['cluster']}") or 0.0) for q in pool], wrong0), 4)
    OUT.write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
