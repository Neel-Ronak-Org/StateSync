#!/usr/bin/env python3
"""
scripts/build_demo_data.py
Builds docs/paperb/demo/demo_data.json for the Paper B demo page (docs/paperb/demo/index.html) from committed result
files only: R1 page finding (a stratified sample of questions with their 25 passages, scores and gold page), R2/R4
routing examples with every router's decision and Jev's probabilities, and the summary numbers. Passage text is read
from the library database (read-only role) through the shared MCP container.

    /opt/anaconda3/bin/python scripts/build_demo_data.py
"""
from __future__ import annotations

import json
import random
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import eval_jev_pages as ejp  # noqa: E402
import paperb_docs as pd  # noqa: E402
from eval_writer_window import TENANT  # noqa: E402

E = REPO / "docs" / "eval"
OUT = REPO / "docs" / "paperb" / "demo" / "demo_data.json"
j = lambda n: json.loads((E / n).read_text())


def pages_sample() -> list[dict]:
    ejp.SET_NAME = "pages_r1"
    orders, _ = ejp.r1_orders()
    qs = ejp.questions()
    items = {i["id"]: i for i in j("page_set_r1.json")["items"]}
    runs = json.loads(ejp.RUNS.read_text())["runs"]
    jev = j("jev_pages_r1.json")["scores"]
    name_of = {d["document_id"]: d["doc_name"] for d in json.loads(pd.INGEST.read_text())["documents"] if d.get("document_id")}
    first = lambda q, arm: orders[q][arm][0] in qs[q]["gold"]
    groups = {"jev_fixes": [q for q in orders if first(q, "JB") and not first(q, "B")],
              "both": [q for q in orders if first(q, "JB") and first(q, "B")],
              "ce_only": [q for q in orders if first(q, "B") and not first(q, "JB")],
              "neither": [q for q in orders if not first(q, "B") and not first(q, "JB")]}
    rng = random.Random(20261008)
    pick = []
    for g, n in (("jev_fixes", 9), ("both", 6), ("ce_only", 4), ("neither", 3)):
        pick += [(g, q) for q in rng.sample(sorted(groups[g]), min(n, len(groups[g])))]
    ids = sorted({p["chunk_id"] for _, q in pick for c in ("rel_off", "rel_bge_titles") for p in runs[f"pages_r1|{c}"]["results"][q]["passages"]})
    res = subprocess.run(["docker", "exec", "-i", "statesync-mcp-1", "python", "-c", ejp._FETCH],  # noqa: SLF001
                         input=json.dumps({"ids": ids, "tenant": TENANT}), capture_output=True, text=True, timeout=600)
    if res.returncode:
        raise SystemExit(res.stderr[-600:])
    text = json.loads(res.stdout.strip().splitlines()[-1])
    out = []
    for g, q in pick:
        it = items[q]
        bge = runs["pages_r1|rel_bge_titles"]["results"][q]["passages"]
        dense = runs["pages_r1|rel_off"]["results"][q]["passages"]
        def rows(ps):
            return [{"id": p["chunk_id"], "doc": name_of.get(p["document_id"]), "page": p.get("page"),
                     "dense": round(float(p["score"]), 4), "ce": None if p.get("rerank_score") is None else round(float(p["rerank_score"]), 3),
                     "jev": jev.get(f"{q}|{p['chunk_id']}"), "gold": (name_of.get(p["document_id"]), p.get("page")) == (it["doc_name"], it["page"]),
                     "text": " ".join((text.get(p["chunk_id"], {}).get("text") or "").split())[:420]} for p in ps]
        out.append({"id": q, "group": g, "question": it["question"], "answer": it["answer"], "doc": it["doc_name"], "page": it["page"],
                    "bge_list": rows(bge), "bge_display": [p["chunk_id"] for p in bge],
                    "dense_list": rows(dense), "dense_display": [p["chunk_id"] for p in dense]})
    return out


def routing() -> dict:
    def pred(r):
        return None if r.get("http_status") != 200 else ("neither" if r.get("out_of_scope") else r.get("route"))
    r2 = {i["id"]: i for i in j("routing_set_r2.json")["items"]}
    R2 = j("routing_runs_r2.json")["runs"]
    sem = j("routing_semantic_r2.json")["results"]
    arms = {"llm": "llm|r2|0", "jev_choice": "jev_choice+v2|r2|0", "jev_yesno": "jev|r2|0",
            "llm_cov": "llm+cov|r2|0", "jev_choice_cov": "jev_choice+v2cov|r2|0"}
    rng = random.Random(7)
    pick = []
    for sub in ("docs_statement", "docs_figure", "either", "sql", "hybrid", "neither"):
        pick += rng.sample(sorted(i for i in r2 if r2[i]["subclass"] == sub), 3)
    ex = []
    for i in pick:
        row = {"id": i, "question": r2[i]["question"], "class": r2[i]["class"], "subclass": r2[i]["subclass"],
               "acceptable": r2[i]["acceptable"], "routes": {a: pred(R2[k]["items"][i]) for a, k in arms.items()},
               "semantic": sem[i]["route"]}
        jc = (R2["jev_choice+v2|r2|0"]["items"][i].get("front_door") or {}).get("jev") or {}
        row["jev_choice_p"] = jc.get("probabilities")
        jy = ((R2["jev|r2|0"]["items"][i].get("front_door") or {}).get("jev") or {}).get("probabilities", {}).get("first", {})
        row["jev_yesno_p"] = {k: jy.get(k) for k in ("needs_database", "needs_documents", "out_of_scope")}
        ex.append(row)
    r4 = {i["id"]: i for i in j("routing_set_r4.json")["items"]}
    R4 = j("routing_runs_r4.json")["runs"]
    a4 = {"llm": "llm|r4|0", "llm_cov": "llm+cov|r4|0", "jev": "jev_choice+v2|r4|0", "jev_cov": "jev_choice+v2cov|r4|0"}
    pick4 = []
    for sub in ("unknown_company", "out_of_range_year", "either", "sql"):
        pick4 += random.Random(11).sample(sorted(i for i in r4 if r4[i]["subclass"] == sub), 3)
    ex4 = [{"id": i, "question": r4[i]["question"], "subclass": r4[i]["subclass"], "acceptable": r4[i]["acceptable"],
            "routes": {a: pred(R4[k]["items"][i]) for a, k in a4.items()}} for i in pick4]
    return {"r2": ex, "r4": ex4}


def numbers() -> dict:
    lines = (REPO / "docs" / "paperb" / "latex" / "numbers.tex").read_text().splitlines()
    out = {}
    for line in lines:
        if "pb@" in line:
            k = line.split("pb@")[1].split("\\endcsname")[0]
            v = line.split("\\endcsname{", 1)[1].rsplit("}", 1)[0]
            out[k] = v.replace("\\%", "%").replace("\\$", "$").replace("$p<10^{", "p<1e").replace("}$", "").replace("$p=", "p=").replace("$p_{\\mathrm{Holm}}=", "p(Holm)=").replace("$", "")
    return out


def main() -> int:
    data = {"pages": pages_sample(), "routing": routing(), "n": numbers()}
    OUT.write_text(json.dumps(data, separators=(",", ":")))
    print(f"wrote {OUT.relative_to(REPO)}: {OUT.stat().st_size // 1024} KB, {len(data['pages'])} page questions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
