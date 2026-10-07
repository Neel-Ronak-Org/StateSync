#!/usr/bin/env python3
"""
scripts/paperb_numbers.py
Every number Paper B's LaTeX draft prints, computed from the committed result files and written as macros to
docs/paperb/latex/numbers.tex. The paper uses \\N{key} (defined in main.tex); nothing in the text is typed by hand,
so a rerun of any experiment updates the paper by rerunning this script. A key that cannot be computed fails loudly.

    python3 scripts/paperb_numbers.py
"""

from __future__ import annotations

import json
import math
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
E = REPO / "docs" / "eval"
OUT = REPO / "docs" / "paperb" / "latex" / "numbers.tex"


def j(name: str) -> dict:
    return json.loads((E / name).read_text())


def pct(x: float, d: int = 1) -> str:
    return f"{100 * x:.{d}f}\\%"


def fx(x: float, d: int = 3) -> str:
    return f"{x:.{d}f}"


def pval(p: float) -> str:
    if p <= 0:      # a permutation p at its resolution (100,000 draws) or an underflow
        return "$p<10^{-5}$"
    if p < 1e-4:
        e = math.floor(math.log10(p))
        return f"$p<10^{{{e + 1}}}$"
    return f"$p={p:.3f}$" if p < 0.1 else f"$p={p:.2f}$"


def mcnemar(b: int, c: int) -> float:
    n = b + c
    return 1.0 if n == 0 else min(1.0, 2 * sum(math.comb(n, i) for i in range(min(b, c) + 1)) / 2 ** n)


def routing() -> dict[str, str]:
    rs = j("routing_set.json")
    acc = {i["id"]: i["acceptable"] for i in rs["items"]}
    tacc = {f"{c['id']}|{t['turn']}": t["acceptable"] for c in rs["conversations"] for t in c["turns"]}
    d = j("routing_runs.json")
    R, S = d["runs"], d["summary"]
    out = {}
    names = {"llm|test|0": "rtLLM", "shipped|test|0": "rtShip", "llm@raw|test|0": "rtLLMraw",
             "jev_choice@raw+v2|test|0": "rtJevC", "jev_choice@raw|test|0": "rtJevCvone", "jev|test|0": "rtJevYN",
             "jev_rw|test|0": "rtJevRW", "llm|dev|0": "rtLLMdev", "shipped|dev|0": "rtShipdev"}
    for k, n in names.items():
        s = S[k]
        out[f"{n}Acc"] = pct(s["route_accuracy"])
        out[f"{n}Macro"] = pct(s["macro_class_accuracy"])
        out[f"{n}Turns"] = pct(s["turn_route_accuracy"])
        out[f"{n}Hybrid"] = s["by_class"]["hybrid"]
        out[f"{n}Docs"] = s["by_class"]["docs"]
        out[f"{n}Neither"] = s["by_class"]["neither"]
        out[f"{n}Sql"] = s["by_class"]["sql"]
        out[f"{n}Covers"] = pct(s["covers_needed_source"])
        out[f"{n}Ms"] = f"{s['p50_ms'] / 1000:.1f}"
        out[f"{n}Calls"] = f"{s['mean_llm_calls']:.0f}"

    def pred(r: dict) -> str | None:
        return None if r.get("http_status") != 200 else ("neither" if r.get("out_of_scope") else r.get("route"))

    for tag, a, b in (("LLMvShip", "llm", "shipped"), ("LLMrawvShip", "llm@raw", "shipped"),
                      ("JevCvLLM", "jev_choice@raw+v2", "llm@raw"), ("JevCvShip", "jev_choice@raw+v2", "shipped"),
                      ("JevYNvLLM", "jev", "llm@raw"), ("JevRWvLLM", "jev_rw", "llm@raw")):
        A, B = R[f"{a}|test|0"]["items"], R[f"{b}|test|0"]["items"]
        ids = [i for i in A if i in B]
        w = sum(pred(A[i]) in acc[i] and pred(B[i]) not in acc[i] for i in ids)
        l_ = sum(pred(B[i]) in acc[i] and pred(A[i]) not in acc[i] for i in ids)
        out[f"rt{tag}W"], out[f"rt{tag}L"], out[f"rt{tag}P"] = str(w), str(l_), pval(mcnemar(w, l_))
    out["rtN"] = str(S["llm|test|0"]["questions"])
    out["rtTurnsN"] = str(S["llm|test|0"]["turns"])
    return out


def main() -> int:
    N: dict[str, str] = {}
    # ── Part S ──
    a = j("paperb_analysis.json")
    P = a["policies"]
    N["sN"] = str(a["n"])
    N["sFirst"] = pct(P["P0 first answer"])
    N["sPsix"] = pct(P["P6 ModernBERT-triggered cascade"])
    N["sPfivev"] = pct(P["P5v pool ModernBERT + vote"])
    N["sOracle"] = pct(P["oracle (any candidate right)"])
    N["sJudgeMax"] = pct(P["P4 pool judge-max"])
    N["sJudgeCascade"] = pct(P["P7 judge-triggered cascade"])
    N["sVote"] = pct(P["P3 pool vote"])
    N["sJevMax"] = pct(P["pool Jev-max (zero-shot)"])
    t = a["tests"]
    for h in ("H1", "H2", "H3"):
        N[f"s{h}P"] = f"$p={t[h]['p_one_sided']:.4f}$".rstrip("0") if t[h]["p_one_sided"] >= 1e-4 else pval(t[h]["p_one_sided"])
        N[f"s{h}W"], N[f"s{h}L"] = str(int(t[h]["a_better"])), str(int(t[h]["b_better"]))
    D = a["detection_first_answer"]
    for key, name in (("judge (Gemini, 0-100)", "Judge"), ("modernbert_real", "MB"), ("modernbert_mutation", "MBmut"),
                      ("laya_finetuned", "LayaFT"), ("laya_zeroshot", "LayaZS"), ("jev_zeroshot", "Jev")):
        N[f"sAuroc{name}"] = fx(D[key]["auroc"])
    jc = j("paperb_jev_cascade.json")["summary"]
    for key, name in (("J2 Jev-triggered cascade, ModernBERT + vote fallback", "Jtwo"),
                      ("J3 Jev doubt at 0.5, ModernBERT + vote fallback", "Jthree"),
                      ("J1 Jev-triggered cascade, Jev-max fallback", "Jone")):
        N[f"s{name}"] = pct(jc[key]["accuracy"])
        N[f"s{name}Fall"] = pct(jc[key]["fallback_share"])
        N[f"s{name}P"] = pval(jc[key]["vs_first_test"]["p_one_sided"])
    ex = a["approved_examples"]
    for k in ("E0", "E1", "E2"):
        N[f"sEx{k}"] = pct(ex["accuracy"][k])
    for k, name in (("E1 - E0", "ExOne"), ("E2 - E0", "ExTwo")):
        d = ex[k]
        N[f"s{name}Diff"] = f"{100 * d['difference']:+.1f}"
        N[f"s{name}CI"] = f"[{100 * d['ci95'][0]:+.1f}, {100 * d['ci95'][1]:+.1f}]"
    N["sPsixFall"] = pct(a["P6_fallback_share"])
    d = a["vs_first"]["P6 ModernBERT-triggered cascade"]
    N["sPsixCI"] = f"[{100 * d['ci95'][0]:+.1f}, {100 * d['ci95'][1]:+.1f}]"
    N["sJudgeDoubts"] = pct(a["judge_doubts_first"])
    N["sPfive"] = pct(P["P5 pool ModernBERT-max (real)"])
    N["sPfivem"] = pct(P["P5m pool ModernBERT-max (mutation)"])
    N["sLayaMax"] = pct(P["P5L pool Laya-max (fine-tuned)"])
    N["sLayaZSMax"] = pct(P["pool Laya-max (zero-shot)"])
    N["sReVote"] = pct(P["P1 judge-triggered re-vote (GPT-5 mini's 6)"])
    N["sEscalate"] = pct(P["P2 judge-triggered escalation (gpt-oss answer)"])
    rt = j("paperb_jev_retest.json")["summary"]
    N["sJevRetest"] = fx(rt["spearman"])
    N["sJevCost"] = f"\\${j('paperb_scores_jev_zeroshot.json')['cost']:.2f}"
    # ── Part D: FinanceBench end to end ──
    def correct(path: str, label: str) -> tuple[int, int]:
        v = {k.split("|", 1)[1]: x.get("verdict") for k, x in j(path)["verdicts"].items() if k.startswith(label + "|")}
        return sum(x == "correct" for x in v.values()), len(v)
    c0, n0 = correct("paperb_docs_grades_preliminary_dense.json", "dense")
    c1, n1 = correct("paperb_docs_grades_v3.json", "v3_dense")
    c2, n2 = correct("paperb_docs_grades_v3.json", "v3_bge")
    N["fbShip"], N["fbDense"], N["fbBge"], N["fbN"] = pct(c0 / n0), pct(c1 / n1), pct(c2 / n2), str(n0)
    # paired FinanceBench counts and the shipped system's routes (the diagnosis)
    def verdicts(path: str, label: str) -> dict[str, str]:
        return {k.split("|", 1)[1]: x.get("verdict") for k, x in j(path)["verdicts"].items() if k.startswith(label + "|")}
    vs = verdicts("paperb_docs_grades_preliminary_dense.json", "dense")
    for name, lab in (("Dense", "v3_dense"), ("Bge", "v3_bge")):
        vv = verdicts("paperb_docs_grades_v3.json", lab)
        w = sum(vv[i] == "correct" and vs.get(i) != "correct" for i in vv)
        l_ = sum(vs.get(i) == "correct" and vv[i] != "correct" for i in vv)
        N[f"fb{name}W"], N[f"fb{name}L"], N[f"fb{name}P"] = str(w), str(l_), pval(mcnemar(w, l_))
    ship = j("paperb_docs_answers_dense.json")["answers"]
    routes = {k: v["route"] for k, v in ship.items()}
    N["fbShipToSql"] = str(sum(r == "sql" for r in routes.values()))
    reached = [k for k, r in routes.items() if r in ("docs", "hybrid")]
    N["fbShipReached"] = str(len(reached))
    N["fbShipReachedCorrect"] = pct(sum(vs.get(k) == "correct" for k in reached) / len(reached))
    N["fbShipRefused"] = str(sum(v == "refused" for v in vs.values()))
    sem = j("routing_semantic.json")["summary"]["semantic|test|item"]
    N["rtSemAcc"], N["rtSemHybrid"], N["rtSemNeither"], N["rtSemDocs"] = (pct(sem["route_accuracy"]), sem["by_class"]["hybrid"],
                                                                         sem["by_class"]["neither"], sem["by_class"]["docs"])
    # ── writer window ──
    w = j("partd_writer_window.json")["summary"]
    for k, name in (("financebench|doc_off", "Doc"), ("financebench|rel_off", "Rel"), ("financebench|rel_bge_titles", "BgeT")):
        N[f"ww{name}"] = pct(w[k]["window"])
        N[f"ww{name}Any"] = pct(w[k]["anywhere"])
    # ── G1 ──
    g = j("partd_g1_bird_t2c_vs_off1.json")["summary"]
    N["gOne"] = pct(g["accuracy"]["on"])
    N["gFalseNA"] = f"{g['false_not_answerable']['on']}/{g['questions_in_both']}"
    nf = j("partd_g1_noise_floor.json")["summary"]
    N["gNoiseA"], N["gNoiseB"] = pct(nf["accuracy"]["off"]), pct(nf["accuracy"]["on"])
    # ── routing ──
    N.update(routing())
    # ── hybrid held-out ──
    h = j("partd_hybrid_heldout.json")["summary"]
    for arm, name in (("shipped", "Ship"), ("v3", "Vthree"), ("v3_entities", "Ent")):
        N[f"hy{name}"] = str(h[arm]["all"]["both"])
        N[f"hy{name}Dep"] = str(h[arm]["dependent"]["both"])
    N["hyMainP"] = pval(h["v3_vs_shipped"]["p_mcnemar"])
    N["hyMainW"], N["hyMainL"] = str(h["v3_vs_shipped"]["wins"]), str(h["v3_vs_shipped"]["losses"])
    sc = j("partd_hybrid_heldout.json")["scored"]
    dep = [i for i, x in sc["v3"].items() if x["dependent"]]
    w = sum(sc["v3_entities"][i]["both"] and not sc["v3"][i]["both"] for i in dep)
    l_ = sum(sc["v3"][i]["both"] and not sc["v3_entities"][i]["both"] for i in dep)
    N["hyEntW"], N["hyEntL"], N["hyEntP"] = str(w), str(l_), pval(mcnemar(w, l_))
    N["hyVthreeToSql"] = str(sum(sc["v3"][i]["route"] == "sql" for i in dep))
    N["hyDbRight"] = str(h["v3"]["all"]["database_correct"])
    # ── follow-up turns ──
    tt = j("turns_e2e.json")["summary"]
    for arm, name in (("llm", "LLM"), ("jev_choice_carry", "Carry"), ("shipped", "Ship"), ("jev_choice", "NoCtx")):
        N[f"tu{name}"] = str(tt[arm]["correct"])
    N["tuN"] = str(tt["llm"]["graded"])
    G = j("turns_e2e.json")["grades"]
    gid = sorted({k.split("|", 1)[1] for k in G})
    ok = lambda arm, i: (G.get(f"{arm}|{i}") or {}).get("verdict") == "correct"
    for tag, a2, b2 in (("CarryNo", "jev_choice_carry", "jev_choice"), ("CarryLLM", "jev_choice_carry", "llm"),
                        ("LLMShip", "llm", "shipped"), ("CarryShip", "jev_choice_carry", "shipped")):
        w = sum(ok(a2, i) and not ok(b2, i) for i in gid)
        l_ = sum(ok(b2, i) and not ok(a2, i) for i in gid)
        N[f"tu{tag}W"], N[f"tu{tag}L"], N[f"tu{tag}P"] = str(w), str(l_), pval(mcnemar(w, l_))
    first_g1 = j("partd_g1_bird_firstrun.json")["summary"]
    N["gFirstFalseNA"] = f"{first_g1['false_not_answerable']['on']}/{first_g1['questions_in_both']}"
    cc = j("context_carry_offline.json")["summary"]
    N["ccDetected"] = f"{cc['detected']}/{cc['follow_ups']}"
    N["ccBoth"] = cc["both_right_on_follow_ups"]
    # ── Jev pages, confirmatory ──
    jp = j("jev_pages_heldout.json")
    s = jp["summary"]
    N["pgN"] = str(s["pages_heldout|rel_bge_titles|recorded"]["n"])
    for k, name in (("pages_heldout|rel_off|recorded", "D"), ("pages_heldout|rel_off|jev", "JD"),
                    ("pages_heldout|rel_bge_titles|recorded", "B"), ("pages_heldout|rel_bge_titles|jev", "JB")):
        N[f"pg{name}First"], N[f"pg{name}Win"] = pct(s[k]["page_first"]), pct(s[k]["window"])
    c = jp["comparisons"]["H-P1 JB vs B [page_first]"]
    N["pgPoneW"], N["pgPoneL"], N["pgPoneP"] = str(c["wins"]), str(c["losses"]), pval(mcnemar(c["wins"], c["losses"]))   # exact, not the stored rounded p
    # ── Jev not-answerable gate ──
    ja = j("jev_answerable.json")["summary"]
    N["naAuroc"] = fx(ja["auroc_all"])
    N["naFalse"] = ja["test_half_at_fitted"]["false_refusals"]
    N["naCaught"] = ja["test_half_at_fitted"]["unanswerable_refused"]
    # ── Jev document checker ──
    N["dcAuroc"] = fx(j("jev_doc_checker.json")["summary"]["pooled"]["auroc"])

    OUT.parent.mkdir(parents=True, exist_ok=True)
    lines = ["% GENERATED by scripts/paperb_numbers.py from docs/eval/*.json -- do not edit by hand."]
    for k, v in sorted(N.items()):
        lines.append(f"\\expandafter\\def\\csname pb@{k}\\endcsname{{{v}}}")
    OUT.write_text("\n".join(lines) + "\n")
    print(f"{len(N)} numbers -> {OUT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
