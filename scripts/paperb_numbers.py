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
                      ("JevCvonevLLM", "jev_choice@raw", "llm@raw"), ("JevYNvLLM", "jev", "llm@raw"), ("JevRWvLLM", "jev_rw", "llm@raw")):
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
    # Holm over the three pre-registered hypotheses: the table prints adjusted one-sided p values.
    order = sorted(("H1", "H2", "H3"), key=lambda h: t[h]["p_one_sided"])
    adj, run = {}, 0.0
    for i, h in enumerate(order):
        run = max(run, min(1.0, (len(order) - i) * t[h]["p_one_sided"]))
        adj[h] = run
    assert all((adj[h] < 0.05) == a["holm"][h] for h in adj), "Holm adjustment disagrees with the analysis file"
    cnt = lambda x: f"{x:.0f}" if float(x).is_integer() else f"{x:.1f}"   # ties score fractional wins
    for h in ("H1", "H2", "H3"):
        N[f"s{h}P"] = f"$p_{{\\mathrm{{Holm}}}}={adj[h]:.4f}$"
        N[f"s{h}Pt"] = f"$p={adj[h]:.3f}$"          # the table's short form (its caption says Holm-adjusted)
        N[f"s{h}W"], N[f"s{h}L"] = cnt(t[h]["a_better"]), cnt(t[h]["b_better"])
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
        ci = jc[key]["vs_first"]["ci95"]
        N[f"s{name}CI"] = f"[{100 * ci[0]:+.1f}, {100 * ci[1]:+.1f}]"
        if "vs_P6" in jc[key]:
            ci = jc[key]["vs_P6"]["ci95"]
            N[f"s{name}vsPsixCI"] = f"[{100 * ci[0]:+.1f}, {100 * ci[1]:+.1f}]"
            N[f"s{name}vsPsixDiff"] = f"{100 * jc[key]['vs_P6']['difference']:+.1f}"
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
    for key, tag in (("judge (Gemini, 0-100) - jev_zeroshot", "JudgeJev"), ("jev_zeroshot - modernbert_real", "JevMB")):
        g = a["detection_gaps"][key]
        N[f"sGap{tag}"] = f"{g['difference']:+.3f}"
        N[f"sGap{tag}CI"] = f"[{g['ci95'][0]:+.3f}, {g['ci95'][1]:+.3f}]"
    cpq = a["cost_per_question"]
    other = cpq["the other 20 candidates"]
    N["sCostFirstTok"] = f"{cpq['first answer (GPT-5 mini pipeline)']['tokens'] / 1000:.1f}k"
    N["sCostOtherTok"] = f"{other['tokens'] / 1000:.1f}k"
    N["sPsixExtraTok"] = f"{a['P6_fallback_share'] * other['tokens'] / 1000:.1f}k"     # expected, generating on fallback only
    N["sJthreeExtraTok"] = f"{jc['J3 Jev doubt at 0.5, ModernBERT + vote fallback']['fallback_share'] * other['tokens'] / 1000:.1f}k"
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
    # ── rerun R3: answerability with hard negatives (docs/research/paperb_reruns_prereg.md) ──
    r3 = j("jev_answerable_r3.json")["summary"]
    N["rthreeN"] = str(r3["questions"])
    for form, tag in (("hint", ""), ("no_hint", "NoHint")):
        f = r3[form]
        for neg, nm in (("N_column", "Col"), ("N_table", "Tab")):
            a, lo, hi = f[f"auroc_P_vs_{neg}"]
            N[f"rthreeAuc{nm}{tag}"], N[f"rthreeAuc{nm}{tag}CI"] = fx(a), f"[{lo:.3f}, {hi:.3f}]"
        cf = f["cross_fitted"]
        N[f"rthreeFalse{tag}"] = cf["P"].split()[0]
        N[f"rthreeCatchCol{tag}"] = cf["N_column"].split()[0]
        N[f"rthreeCatchTab{tag}"] = cf["N_table"].split()[0]
    for tag, src in (("FrozenFalse", "P"), ("FrozenCol", "N_column"), ("FrozenTab", "N_table")):
        N[f"rthree{tag}"] = r3["hint"]["at_frozen_0.11"][src].split()[0]
    lex = j("jev_answerable_r3.json").get("lexical_baseline_exploratory")
    if lex:
        for k, tag in (("hint: P vs N_column", "Col"), ("hint: P vs N_table", "Tab"), ("no_hint: P vs N_column", "ColNoHint"),
                       ("no_hint: P vs N_table", "TabNoHint")):
            N[f"rthreeLex{tag}"] = fx(lex[k])
    # ── rerun R2: fresh routing set (only once its runs exist) ──
    r2runs = E / "routing_runs_r2.json"
    if r2runs.exists():
        import random
        items = {i["id"]: i for i in j("routing_set_r2.json")["items"]}
        R2 = json.loads(r2runs.read_text())["runs"]
        sem = j("routing_semantic_r2.json")["results"] if (E / "routing_semantic_r2.json").exists() else {}

        def pred2(r: dict) -> str | None:
            return None if r.get("http_status") != 200 else ("neither" if r.get("out_of_scope") else r.get("route"))
        arms = {"LLM": "llm|r2|0", "JevOne": "jev_choice|r2|0", "JevTwo": "jev_choice+v2|r2|0", "JevYN": "jev|r2|0"}
        ok: dict[str, dict[str, bool]] = {}
        for nm, key in arms.items():
            if key in R2:
                ok[nm] = {i: pred2(R2[key]["items"][i]) in items[i]["acceptable"] for i in items if i in R2[key]["items"]}
                lat = sorted(r["ms"] for r in R2[key]["items"].values() if r.get("ms") is not None)
                N[f"rtwo{nm}Ms"] = f"{lat[len(lat) // 2] / 1000:.1f}"
        if sem:
            ok["Sem"] = {i: sem[i]["correct"] for i in items}
        summ = json.loads(r2runs.read_text()).get("summary", {})
        for nm, key in arms.items():
            if key in summ:
                N[f"rtwo{nm}Macro"] = pct(summ[key]["macro_class_accuracy"])
                N[f"rtwo{nm}Covers"] = pct(summ[key]["covers_needed_source"])
        yn = R2.get("jev|r2|0", {}).get("items", {})
        N["rtwoYNHybToDocs"] = str(sum(items[i]["class"] == "hybrid" and pred2(r) == "docs" for i, r in yn.items()))
        N["rtwoYNHybToSql"] = str(sum(items[i]["class"] == "hybrid" and pred2(r) == "sql" for i, r in yn.items()))
        N["rtwoYNEitherToHyb"] = str(sum(items[i]["class"] == "either" and pred2(r) == "hybrid" for i, r in yn.items()))
        N["rtwoN"] = str(len(items))
        for nm, o in ok.items():
            N[f"rtwo{nm}Acc"] = pct(sum(o.values()) / len(o))
            for cls, col in (("docs", "Docs"), ("either", "Either"), ("sql", "Sql"), ("hybrid", "Hybrid"), ("neither", "Neither")):
                ids = [i for i in o if items[i]["class"] == cls]
                N[f"rtwo{nm}{col}"] = f"{sum(o[i] for i in ids)}/{len(ids)}"
        rng = random.Random(20261008)
        tests = {}
        for tag, a2, b2 in (("Hone", "JevTwo", "LLM"), ("Htwo", "JevTwo", "JevYN"), ("Hthree", "JevOne", "LLM"), ("Hfour", "Sem", "LLM")):
            if a2 in ok and b2 in ok:
                ids = sorted(set(ok[a2]) & set(ok[b2]))
                x = [ok[a2][i] - ok[b2][i] for i in ids]
                w, l_ = sum(v > 0 for v in x), sum(v < 0 for v in x)
                boots = sorted(sum(rng.choice(x) for _ in x) / len(x) for _ in range(2000))
                tests[tag] = {"w": w, "l": l_, "p": mcnemar(w, l_), "d": 100 * sum(x) / len(x),
                              "ci": (100 * boots[50], 100 * boots[1949])}
        sec = sorted((t for t in tests if t != "Hone"), key=lambda t: tests[t]["p"])
        run = 0.0
        for k, t in enumerate(sec):
            run = max(run, min(1.0, (len(sec) - k) * tests[t]["p"]))
            tests[t]["p"] = run
        for tag, t in tests.items():
            N[f"rtwo{tag}W"], N[f"rtwo{tag}L"], N[f"rtwo{tag}P"] = str(t["w"]), str(t["l"]), pval(t["p"])
            N[f"rtwo{tag}Diff"], N[f"rtwo{tag}CI"] = f"{t['d']:+.1f}", f"[{t['ci'][0]:+.1f}, {t['ci'][1]:+.1f}]"
    # ── earlier page sets re-analysed with the pre-registered R1 orders (development / superseded evidence) ──
    import sys as _sys
    _sys.path.insert(0, str(REPO / "scripts"))
    import eval_jev_pages as ejp
    for set_name, tag in (("financebench", "Fb"), ("pages_heldout", "Old")):
        ejp.SET_NAME = set_name
        orders, _ = ejp.r1_orders()
        qs = ejp.questions()
        for arm in ("D", "B", "JD", "JB"):
            N[f"dev{tag}{arm}One"] = pct(sum(orders[q][arm][0] in qs[q]["gold"] for q in orders) / len(orders))
        w = sum(orders[q]["JB"][0] in qs[q]["gold"] and orders[q]["B"][0] not in qs[q]["gold"] for q in orders)
        l_ = sum(orders[q]["B"][0] in qs[q]["gold"] and orders[q]["JB"][0] not in qs[q]["gold"] for q in orders)
        N[f"dev{tag}N"], N[f"dev{tag}W"], N[f"dev{tag}L"], N[f"dev{tag}P"] = str(len(orders)), str(w), str(l_), pval(mcnemar(w, l_))
    # ── rerun R4: the router's coverage line (only once its runs exist) ──
    r4runs = E / "routing_runs_r4.json"
    if r4runs.exists():
        import random
        it4 = {i["id"]: i for i in j("routing_set_r4.json")["items"]}
        R4 = json.loads(r4runs.read_text())["runs"]

        def p4(r: dict) -> str | None:
            return None if r.get("http_status") != 200 else ("neither" if r.get("out_of_scope") else r.get("route"))
        arms4 = {"LLMOff": "llm|r4|0", "LLMOn": "llm+cov|r4|0", "JevOff": "jev_choice+v2|r4|0", "JevOn": "jev_choice+v2cov|r4|0"}
        ok4 = {nm: {i: p4(R4[k]["items"][i]) in it4[i]["acceptable"] for i in it4 if i in R4[k]["items"]}
               for nm, k in arms4.items() if k in R4}
        groups = {"Neither": [i for i in it4 if it4[i]["class"] == "neither"], "Cov": [i for i in it4 if it4[i]["class"] != "neither"],
                  "Unknown": [i for i in it4 if it4[i]["subclass"] == "unknown_company"],
                  "Years": [i for i in it4 if it4[i]["subclass"] == "out_of_range_year"]}
        for nm, o in ok4.items():
            for g, ids in groups.items():
                N[f"rfour{nm}{g}"] = f"{sum(o[i] for i in ids if i in o)}/{len(ids)}"
            N[f"rfour{nm}Acc"] = pct(sum(o.values()) / len(o))
        rng = random.Random(20261010)
        t4 = {}
        for tag, on, off, g in (("Hone", "JevOn", "JevOff", "Neither"), ("Htwo", "LLMOn", "LLMOff", "Neither"),
                                ("Hthree", "JevOn", "JevOff", "Cov"), ("Hfour", "LLMOn", "LLMOff", "Cov")):
            if on in ok4 and off in ok4:
                ids = [i for i in groups[g] if i in ok4[on] and i in ok4[off]]
                x = [ok4[on][i] - ok4[off][i] for i in ids]
                w, l_ = sum(v > 0 for v in x), sum(v < 0 for v in x)
                boots = sorted(sum(rng.choice(x) for _ in x) / len(x) for _ in range(2000))
                t4[tag] = {"w": w, "l": l_, "p": mcnemar(w, l_), "d": 100 * sum(x) / len(x), "ci": (100 * boots[50], 100 * boots[1949])}
        sec4 = sorted((t for t in t4 if t != "Hone"), key=lambda t: t4[t]["p"])
        run4 = 0.0
        for k, t in enumerate(sec4):
            run4 = max(run4, min(1.0, (len(sec4) - k) * t4[t]["p"]))
            t4[t]["p"] = run4
        for tag, t in t4.items():
            N[f"rfour{tag}W"], N[f"rfour{tag}L"], N[f"rfour{tag}P"] = str(t["w"]), str(t["l"]), pval(t["p"])
            N[f"rfour{tag}Diff"], N[f"rfour{tag}CI"] = f"{t['d']:+.1f}", f"[{t['ci'][0]:+.1f}, {t['ci'][1]:+.1f}]"
        # R2 no-harm check (descriptive)
        R2b = json.loads((E / "routing_runs_r2.json").read_text())["runs"]
        items2 = {i["id"]: i for i in j("routing_set_r2.json")["items"]}
        for nm, k in (("LLM", "llm+cov|r2|0"), ("JevTwo", "jev_choice+v2cov|r2|0")):
            if k in R2b:
                o = {i: p4(R2b[k]["items"][i]) in items2[i]["acceptable"] for i in items2 if i in R2b[k]["items"]}
                N[f"rtwo{nm}CovAcc"] = pct(sum(o.values()) / len(o))
                nei = [i for i in items2 if items2[i]["class"] == "neither"]
                N[f"rtwo{nm}CovNeither"] = f"{sum(o[i] for i in nei if i in o)}/{len(nei)}"
                base = {"LLM": "llm|r2|0", "JevTwo": "jev_choice+v2|r2|0"}[nm]
                ob = {i: p4(R2b[base]["items"][i]) in items2[i]["acceptable"] for i in items2}
                for cls, col in (("docs", "Docs"), ("either", "Either"), ("sql", "Sql"), ("hybrid", "Hybrid"), ("neither", "Neither")):
                    ids = [i for i in items2 if items2[i]["class"] == cls]
                    N[f"rtwo{nm}Cov{col}"] = f"{sum(o[i] for i in ids)}/{len(ids)}"
                N[f"rtwo{nm}CovW"] = str(sum(o[i] and not ob[i] for i in o))
                N[f"rtwo{nm}CovL"] = str(sum(ob[i] and not o[i] for i in o))
    # ── rerun R1: page finding (only once its report exists) ──
    r1p = E / "jev_pages_r1.json"
    if r1p.exists() and "r1_summary" in json.loads(r1p.read_text()):
        rec = json.loads(r1p.read_text())
        s1 = rec["r1_summary"]
        N["roneN"] = str(s1["n"])
        for arm in ("D", "B", "JD", "JB", "D_display", "B_display"):
            nm = arm.replace("_display", "Disp")
            for m, mm in (("page@1", "One"), ("page@3", "Three"), ("page@8", "Eight")):
                N[f"rone{nm}{mm}"] = pct(s1[arm][m])
        for key, tag in (("R1-H1 JB vs B page@1", "Hone"), ("R1-H2 JD vs B page@1", "Htwo"),
                         ("R1-H3 JD vs D page@1", "Hthree"), ("R1-H4 JB vs B page@3", "Hfour")):
            t = s1["tests"][key]
            N[f"rone{tag}W"], N[f"rone{tag}L"] = str(t["wins"]), str(t["losses"])
            N[f"rone{tag}P"] = pval(t.get("p_holm", t["p"]))
            N[f"rone{tag}Diff"] = f"{t['diff_points']:+.1f}"
            N[f"rone{tag}CI"] = f"[{t['ci95'][0]:+.1f}, {t['ci95'][1]:+.1f}]"
        if "r1_latency" in rec:
            lt = rec["r1_latency"]
            N["roneJevMs"] = str(lt["jev_25_passages_ms"]["p50"])
            N["roneBgeMs"] = str(lt["two_round_search_ms"]["rel_bge_titles"]["p50"])
            N["roneDenseMs"] = str(lt["two_round_search_ms"]["rel_off"]["p50"])
            N["roneCost"] = f"\\${lt['jev_cost_per_question_usd']:.4f}"

    OUT.parent.mkdir(parents=True, exist_ok=True)
    lines = ["% GENERATED by scripts/paperb_numbers.py from docs/eval/*.json -- do not edit by hand."]
    for k, v in sorted(N.items()):
        lines.append(f"\\expandafter\\def\\csname pb@{k}\\endcsname{{{v}}}")
    OUT.write_text("\n".join(lines) + "\n")
    print(f"{len(N)} numbers -> {OUT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
