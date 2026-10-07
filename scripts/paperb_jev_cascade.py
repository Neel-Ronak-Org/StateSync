#!/usr/bin/env python3
"""
scripts/paperb_jev_cascade.py
Paper B Part S, EXPLORATORY (not pre-registered; decided 2026-10-07 after the pre-registered analysis): Jev as the
runtime trigger of the SQL fallback. The same 393 held-out questions, pool, rounds and tie rules as
paperb_analyze.py's P6 (the confirmed H3 policy), with Jev's zero-shot P(correct) of the first answer as the
doubt signal:
  - J1  Jev-triggered cascade, fallback = Jev-max over the pool; tau per round from that round's calibration
        fold, exactly as P6 chooses it (largest tau maximising calibration accuracy; fallback when risk > tau);
  - J2  Jev-triggered cascade, fallback = ModernBERT + vote (P5v, the best selector); same tau rule;
  - J3  Jev at a fixed 0.5 (no tuning): doubt when P(correct) < 0.5, fallback = ModernBERT + vote.
Each is compared with P0 (first answer) and P6 by paperb_analyze's paired sign-flip test and stratified
bootstrap interval. Jev's scores do not depend on the round (zero-shot), so only tau is fitted.

    python3 scripts/paperb_jev_cascade.py        writes docs/eval/paperb_jev_cascade.json and prints the summary
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paperb_analyze as pa  # noqa: E402

FIXED_DOUBT = 0.5           # noqa: literals - J3's untuned threshold on P(correct)


def main() -> int:
    pool = json.loads((pa.EVAL / "paperb_pool.json").read_text())["questions"]
    rot, zero, _ = pa.load_scores()
    jev, mb = zero["jev_zeroshot"], rot["modernbert_real"]
    rnds = pa.rounds(pa.folds(pool))
    strata = [q["db_id"] for q in pool]
    first = [float(q["candidates"][pa.FIRST]["correct"]) for q in pool]
    index = {q["question_id"]: n for n, q in enumerate(pool)}

    def jev_of(q: dict) -> callable:
        return lambda i: jev.get(f"{q['question_id']}|{i}")

    def mb_vote(q: dict, scores: dict) -> float:
        return pa.cluster_sum(q, lambda i: scores.get(f"{q['question_id']}|{i}"))

    def risk(q: dict) -> float:
        return 1 - (jev.get(f"{q['question_id']}|{pa.FIRST}") or 0.0)

    out: dict[str, list[float]] = {}
    taus: dict[str, dict] = {}
    for name, select in (("J1 Jev-triggered cascade, Jev-max fallback", lambda q, split, rnd: pa.cluster_max(q, jev_of(q))),
                         ("J2 Jev-triggered cascade, ModernBERT + vote fallback",
                          lambda q, split, rnd: mb_vote(q, mb["calib"][str(rnd)] if split == "calib" else mb["test"]))):
        vals, fell, taus[name] = [0.0] * len(pool), 0, {}
        for rnd in rnds:
            cal_q = [q for q in pool if q["question_id"] in rnd["calib"]]
            risk_c = {q["question_id"]: risk(q) for q in cal_q}
            sel_c = {q["question_id"]: select(q, "calib", rnd["round"]) for q in cal_q}
            first_c = {q["question_id"]: float(q["candidates"][pa.FIRST]["correct"]) for q in cal_q}

            def acc(tau: float) -> float:
                return statistics.fmean(sel_c[x] if risk_c[x] > tau else first_c[x] for x in risk_c)
            tau = max(sorted({-1.0, 1.0} | set(risk_c.values())), key=lambda t: (acc(t), t))
            taus[name][str(rnd["round"])] = round(tau, 4)
            for qid in rnd["test"]:
                q = pool[index[qid]]
                if risk(q) > tau:
                    fell += 1
                    vals[index[qid]] = select(q, "test", rnd["round"])
                else:
                    vals[index[qid]] = first[index[qid]]
        out[name] = vals
        taus[name]["fallback_share"] = round(fell / len(pool), 4)

    # J3: fixed threshold, no tuning; the fallback selector's scores come from the question's own test round.
    j3, fell3 = [], 0
    for n, q in enumerate(pool):
        if 1 - risk(q) < FIXED_DOUBT:
            fell3 += 1
            j3.append(mb_vote(q, mb["test"]))
        else:
            j3.append(first[n])
    out["J3 Jev doubt at 0.5, ModernBERT + vote fallback"] = j3
    taus["J3 Jev doubt at 0.5, ModernBERT + vote fallback"] = {"fallback_share": round(fell3 / len(pool), 4)}

    analysis = json.loads((pa.EVAL / "paperb_analysis.json").read_text())
    p6_name = "P6 ModernBERT-triggered cascade"
    # P6 per question, recomputed with paperb_analyze's own code path for the paired comparison
    p6 = recompute_p6(pool, rot, rnds, index)
    summary = {"P0 first answer": round(statistics.fmean(first), 4), p6_name: round(statistics.fmean(p6), 4),
               "P6 as reported": analysis["policies"].get(p6_name)}
    for name, vals in out.items():
        summary[name] = {"accuracy": round(statistics.fmean(vals), 4), **taus[name],
                         "vs_first": pa.diff_interval(strata, vals, first), "vs_first_test": pa.sign_flip_p(vals, first),
                         "vs_P6": pa.diff_interval(strata, vals, p6), "vs_P6_test": pa.sign_flip_p(vals, p6)}
    (pa.EVAL / "paperb_jev_cascade.json").write_text(json.dumps({"exploratory": True, "summary": summary}, indent=1) + "\n")
    print(json.dumps(summary, indent=1))
    return 0


def recompute_p6(pool: list[dict], rot: dict, rnds: list[dict], index: dict) -> list[float]:
    rec = rot["modernbert_real"]
    p6 = [0.0] * len(pool)
    for rnd in rnds:
        cal = rec["calib"][str(rnd["round"])]
        cal_q = [q for q in pool if q["question_id"] in rnd["calib"]]
        risk_c = {q["question_id"]: 1 - (cal.get(f"{q['question_id']}|{pa.FIRST}") or 0.0) for q in cal_q}
        sel_c = {q["question_id"]: pa.cluster_max(q, lambda i, q=q: cal.get(f"{q['question_id']}|{i}")) for q in cal_q}
        first_c = {q["question_id"]: float(q["candidates"][pa.FIRST]["correct"]) for q in cal_q}

        def acc(tau: float) -> float:
            return statistics.fmean(sel_c[x] if risk_c[x] > tau else first_c[x] for x in risk_c)
        tau = max(sorted({-1.0, 1.0} | set(risk_c.values())), key=lambda t: (acc(t), t))
        for qid in rnd["test"]:
            q = pool[index[qid]]
            if 1 - (rec["test"].get(f"{qid}|{pa.FIRST}") or 0.0) > tau:
                p6[index[qid]] = pa.cluster_max(q, lambda i, q=q: rec["test"].get(f"{q['question_id']}|{i}"))
            else:
                p6[index[qid]] = float(q["candidates"][pa.FIRST]["correct"])
    return p6


if __name__ == "__main__":
    raise SystemExit(main())
