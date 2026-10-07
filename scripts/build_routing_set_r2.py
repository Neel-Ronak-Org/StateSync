#!/usr/bin/env python3
"""
scripts/build_routing_set_r2.py
Paper B rerun R2 (pre-registered in docs/research/paperb_reruns_prereg.md, commit 82aff8d): a fresh routing set
of 180 single questions over one workspace with the SEC database and the 84-filing library. No FinanceBench
question; labels fixed by construction; built once, before any router runs on it.

  docs, statement (30)  a page sampled with seed 20261009 (disjoint from R1's sampled pages, the first page set and
                        FinanceBench's evidence pages, each +-1; at most 2 per filing); writer glm-5.3-flash asked
                        for a question whose answer is a statement on the page, not a number
  docs, figure (30)     the same, but the answer is a figure on the page that is NOT one of the database's 23
                        consolidated annual metrics (segment, quarterly, per-unit, count, guidance, note figure)
                        Both pass the checker (gpt-oss-120b): the page answers it, it is specific, and its kind holds.
  either (30)           one of the database's metrics for one company and fiscal year, held by the database, with
                        that year's 10-K in the library
  sql (30)              rankings, top-k, counts, averages or medians across the companies, a company's series over
                        years at least one of which has no 10-K in the library, the largest year-on-year growth
  hybrid (30)           15 dependent (pick a company by a database figure, then its 10-K), 15 independent
  neither (30)          10 companies in neither source, 10 live or future figures, 10 requests outside data analysis
Every sql, either and hybrid reference query is executed and must return rows. Template phrasings are new (none
copied from the first routing set) and chosen with seed 20261009.
Also writes the authors' blind check sheet (40 items, stratified by class) and its key, kept apart.

    /opt/anaconda3/bin/python scripts/build_routing_set_r2.py
"""

from __future__ import annotations

import json
import random
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import build_hybrid_set as bh  # noqa: E402
import build_page_set as bp  # noqa: E402
import build_routing_set as br  # noqa: E402
import paperb_docs as pd  # noqa: E402
from eval_writer_window import TENANT  # noqa: E402

OUT = REPO / "docs" / "eval" / "routing_set_r2.json"
SHEET = REPO / "docs" / "eval" / "routing_set_r2_author_check.md"
KEY = REPO / "docs" / "eval" / "routing_set_r2_author_check_key.json"
R1 = REPO / "docs" / "eval" / "page_set_r1.json"
SEED = 20261009
WRITER = ("glm-5.3-flash", "cheaperinference")
CHECKER = ("gpt-oss-120b", "cheaperinference")
PER_KIND = 30               # noqa: literals - pre-registered
CANDIDATES_PER_KIND = 80    # noqa: literals - sampled before the checker drops some (raised from 48, change log 4)
PER_FILING = 2              # noqa: literals - pages per filing
CHECK_SAMPLE = {"docs_statement": 6, "docs_figure": 7, "either": 7, "sql": 7, "hybrid": 7, "neither": 6}

NAMES = {**bh.NAMES, "AMZN": "Amazon", "FL": "Foot Locker", "XYZ": "Block"}
# the 23 metrics, as a reader would say them
PHRASES = {
    "revenue": "revenue", "net_income": "net income", "operating_income": "operating income",
    "gross_profit": "gross profit", "cost_of_revenue": "cost of revenue", "research_and_development": "research and development expense",
    "eps_diluted": "diluted earnings per share", "total_assets": "total assets", "total_liabilities": "total liabilities",
    "current_assets": "current assets", "current_liabilities": "current liabilities", "stockholders_equity": "stockholders' equity",
    "long_term_debt": "long-term debt", "cash_and_equivalents": "cash and cash equivalents", "inventory": "inventory",
    "accounts_receivable": "accounts receivable", "accounts_payable": "accounts payable",
    "property_plant_equipment_net": "net property, plant and equipment", "operating_cash_flow": "operating cash flow",
    "capital_expenditure": "capital expenditure", "dividends_paid": "dividends paid", "share_repurchases": "share repurchases",
    "depreciation_and_amortization": "depreciation and amortization",
}
METRIC_LIST = "; ".join(PHRASES.values())
OUTSIDE = ["Intel", "Oracle", "Ford", "General Motors", "Chevron", "Procter & Gamble", "Home Depot", "Starbucks",
           "McDonald's", "UnitedHealth", "Visa", "Mastercard", "IBM", "Cisco", "Salesforce", "Target", "Disney",
           "Comcast", "Caterpillar", "Honda"]
OFF_TOPIC = ["Write a short poem about autumn leaves.", "Translate 'thank you very much' into Spanish.",
             "What is the capital of Canada?", "Recommend a good science fiction novel.", "How do I make a cup of masala chai?",
             "Explain how a rainbow forms, in simple words.", "Who painted the Mona Lisa?", "Suggest a name for a pet goldfish.",
             "Write a limerick about a sleepy cat.", "What is the boiling point of water at sea level in Fahrenheit?",
             "Give me three tips for a job interview.", "Summarise the plot of Romeo and Juliet in two sentences."]

STATEMENT_PROMPT = (
    "You write evaluation questions for a search system over company filings. You are given the filing's name "
    "(company, year, type) and one page of it. Write ONE question a financial analyst would ask that this page "
    "alone answers, whose answer is a statement or explanation on the page (a reason, a risk, a plan, a policy, a "
    "description), NOT a number. The question must name the company and the fiscal year or period. Do not copy "
    "sentences or distinctive phrases from the page; ask in your own words. Reply with JSON only: "
    "{\"question\": \"...\", \"answer\": \"...\"}.")
FIGURE_PROMPT = (
    "You write evaluation questions for a search system over company filings. You are given the filing's name "
    "(company, year, type) and one page of it. Write ONE question a financial analyst would ask whose answer is a "
    "specific figure stated on this page that is NOT one of the company's consolidated annual totals listed here: "
    f"{METRIC_LIST}. Good figures: a segment's or region's result, a quarterly figure, a per-unit or per-store "
    "figure, a count (employees, stores, customers), guidance, or a figure from a note. The question must name the "
    "company and the fiscal year or period. Do not copy sentences or distinctive phrases from the page. Reply with "
    "JSON only: {\"question\": \"...\", \"answer\": \"...\"}.")
CHECK_PROMPT = (
    "You check an evaluation question against one page of a company filing. Answer three things: does the page "
    "contain the answer to the question (not merely the topic); is the question specific (it names the company and "
    "the period, and has one clear answer); and does the question's kind hold. KIND statement: the answer is a "
    "statement or explanation, not a number. KIND figure: the answer is a figure that is not one of the company's "
    f"consolidated annual totals ({METRIC_LIST}). Reply with JSON only: "
    "{\"page_answers\": true|false, \"specific\": true|false, \"kind_ok\": true|false}.")


def pages_excluded() -> set[tuple[str, int]]:
    ex = {(n, p + d) for q in pd.questions() for n, p in pd.gold_pages(q) for d in (-1, 0, 1)}
    for f in (bp.OUT, R1):
        d = json.loads(f.read_text())
        ex |= {(x["doc_name"], int(x["page"]) + k) for x in d["items"] + d["dropped"] for k in (-1, 0, 1)}
    return ex


def docs_items(rng: random.Random) -> list[dict]:
    ingest = json.loads(pd.INGEST.read_text())
    docs = {d["document_id"]: d["doc_name"] for d in ingest["documents"] if d.get("status") == "ready" and d.get("document_id")}
    res = subprocess.run(["docker", "exec", "-i", "statesync-mcp-paperb", "python", "-c", bp._PAGES],  # noqa: SLF001
                         input=json.dumps({"ids": sorted(docs), "tenant": TENANT}), capture_output=True, text=True, timeout=600)
    if res.returncode:
        raise SystemExit(res.stderr[-600:])
    pages = json.loads(res.stdout.strip().splitlines()[-1])
    excluded = pages_excluded()
    eligible = sorted((docs[i], int(p), t) for i, p, t in pages if len(t or "") >= bp.MIN_CHARS and (docs[i], int(p)) not in excluded)
    rng.shuffle(eligible)
    per: Counter = Counter()
    sampled = []
    for name, page, text in eligible:
        if per[name] >= PER_FILING:
            continue
        per[name] += 1
        kind = "statement" if len(sampled) % 2 == 0 else "figure"
        sampled.append({"id": f"r2d{len(sampled) + 1:03d}", "kind": kind, "doc_name": name, "page": page, "text": text[:bp.PAGE_CHARS]})
        if len(sampled) == 2 * CANDIDATES_PER_KIND:
            break
    out = []
    for kind, prompt in (("statement", STATEMENT_PROMPT), ("figure", FIGURE_PROMPT)):
        batch = [s for s in sampled if s["kind"] == kind]
        written = bp.llm(WRITER, prompt, [{"id": s["id"], "prompt": f"Filing: {s['doc_name']}\n\nPage {s['page']}:\n{s['text']}"} for s in batch])
        for s in batch:
            s["written"] = bp.parse(written.get(s["id"], ""))
        ok = [s for s in batch if s["written"] and s["written"].get("question")]
        checked = bp.llm(CHECKER, CHECK_PROMPT, [{"id": s["id"], "prompt": f"KIND {kind}\n\nPage {s['page']} of {s['doc_name']}:\n{s['text']}\n\n"
                                                                         f"Question: {s['written']['question']}"} for s in ok])
        kept = 0
        for s in batch:
            v = bp.parse(checked.get(s["id"], "")) if s["written"] else None
            why = ("writer reply did not parse" if not s["written"] or not s["written"].get("question")
                   else "checker reply did not parse" if v is None else "page does not answer it" if not v.get("page_answers")
                   else "not specific" if not v.get("specific") else f"not a {kind} question" if not v.get("kind_ok") else None)
            if why is None and kept >= PER_KIND:
                why = "beyond the first 30 kept"
            kept += why is None
            out.append({"id": s["id"], "class": "docs", "subclass": f"docs_{kind}", "question": (s["written"] or {}).get("question"),
                        "gold_answer": (s["written"] or {}).get("answer"), "filing": s["doc_name"], "page": s["page"],
                        "acceptable": ["docs"], "source": "page", **({"dropped": why} if why else {})})
        reasons = Counter(o["dropped"] for o in out if o["subclass"] == f"docs_{kind}" and "dropped" in o)
        print(f"{kind}: {kept} kept of {len(batch)}; dropped {dict(reasons)}; call errors so far {len(bp.CALL_ERRORS)} "
              f"{Counter(e['error'][:60] for e in bp.CALL_ERRORS).most_common(3)}", flush=True)
        if kept < PER_KIND:
            raise SystemExit(f"only {kept} {kind} questions kept; log the shortfall before going on")
    return out


def main() -> int:
    if OUT.exists():
        raise SystemExit(f"{OUT} exists; the pre-registered set is built once")
    rng = random.Random(SEED)
    ingest = json.loads(pd.INGEST.read_text())
    library = {d["doc_name"].upper() for d in ingest["documents"] if d.get("status") == "ready"}
    have = {(r[0], r[1], int(r[2])) for r in br.psql(f"SELECT ticker, metric, period_end_year FROM {br.AM} WHERE value IS NOT NULL")}
    old = {i["question"] for i in json.loads(br.OUT.read_text())["items"]} | {i["question"] for i in json.loads(bh.OUT.read_text())["items"]}

    def filing(t: str, y: int) -> str | None:
        name = f"{br.PREFIX.get(t, '')}_{y}_10K"
        return name if t in br.PREFIX and name.upper() in library else None

    items: list[dict] = []

    def add(cls: str, q: str, sql: str | None, acceptable: list[str], **extra) -> bool:
        if q in old or any(i["question"] == q for i in items):
            return False
        if sql is not None:
            rows = br.psql(sql)
            if not rows or not any(c for c in rows[0]):
                return False
            extra["reference_sql"], extra["sql_answer"] = sql, br.answer_text(rows)
        items.append({"id": f"r2{cls[0]}{sum(1 for i in items if i['class'] == cls) + 1:03d}", "class": cls, "subclass": cls,
                      "question": q, "acceptable": acceptable, "source": "template", **extra})
        return True

    # either: one metric, one company, one fiscal year with its 10-K in the library
    either_forms = ["What was {n}'s {m} in fiscal {y}?", "Tell me {n}'s {m} for fiscal year {y}.",
                    "How much did {n} report as {m} for fiscal {y}?"]
    cands = [(t, y, m) for t in br.PREFIX for y in range(2015, 2024) if filing(t, y) for m in PHRASES
             if (t, m, y) in have and not (t in bh.REVENUE_DEFECT and m in ("revenue", "gross_profit", "cost_of_revenue"))]
    rng.shuffle(cands)
    used: set[tuple[str, int]] = set()
    for t, y, m in cands:
        if sum(i["class"] == "either" for i in items) == PER_KIND:
            break
        if (t, y) in used:
            continue
        q = rng.choice(either_forms).format(n=NAMES[t], m=PHRASES[m], y=y)
        sql = f"SELECT value FROM {br.AM} WHERE ticker = '{t}' AND metric = '{m}' AND period_end_year = {y}"
        if add("either", q, sql, ["sql", "docs"], filing=filing(t, y)):
            used.add((t, y))

    # sql: across companies, or years beyond the library
    plain = [m for m in PHRASES if m != "eps_diluted"]
    def p_top(y, m):
        o = rng.choice(["highest", "lowest"])
        q = rng.choice(["Which company had the {o} {m} in fiscal {y}?", "Name the company with the {o} {m} in fiscal {y}."]).format(o=o, m=PHRASES[m], y=y)
        return q, br.one(m, y, "DESC" if o == "highest" else "ASC")
    def p_topk(y, m):
        k = rng.choice([3, 4, 5])
        q = rng.choice(["List the {k} companies with the most {m} in fiscal {y}.", "Rank the top {k} companies by {m} for fiscal {y}."]).format(k=k, m=PHRASES[m], y=y)
        return q, br.one(m, y, "DESC", limit=k)
    def p_count(y, m):
        med = br.psql(f"SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY value) FROM {br.AM} WHERE metric = '{m}' AND period_end_year = {y}")
        if not med or not med[0][0]:
            return None
        x = max(1, round(float(med[0][0]) / 1e9))
        q = rng.choice(["How many of the companies reported {m} above {x} billion dollars in fiscal {y}?",
                        "Count the companies whose {m} exceeded {x} billion dollars in fiscal {y}."]).format(m=PHRASES[m], x=x, y=y)
        return q, f"SELECT count(*) FROM {br.AM} WHERE metric = '{m}' AND period_end_year = {y} AND value > {x}e9"
    def p_avg(y, m):
        a = rng.choice(["average", "median"])
        agg = "avg(value)" if a == "average" else "percentile_cont(0.5) WITHIN GROUP (ORDER BY value)"
        q = rng.choice(["What was the {a} {m} across all the companies in fiscal {y}?", "Across the companies, what was the {a} {m} in fiscal {y}?"]).format(a=a, m=PHRASES[m], y=y)
        return q, f"SELECT round(({agg})::numeric, 0) FROM {br.AM} WHERE metric = '{m}' AND period_end_year = {y}"
    def p_series(y, m):
        t = rng.choice([t for t in NAMES if any((t, m, yy) in have for yy in range(2015, 2024))])
        y0, y1 = y - 4, y
        if any(filing(t, yy) is None for yy in range(y0, y1 + 1)) and sum((t, m, yy) in have for yy in range(y0, y1 + 1)) >= 4:
            q = rng.choice(["What was {n}'s {m} in each fiscal year from {y0} to {y1}?", "Show {n}'s {m} for every fiscal year between {y0} and {y1}."]).format(n=NAMES[t], m=PHRASES[m], y0=y0, y1=y1)
            return q, br.series(t, m, y0, y1)
        return None
    def p_growth(y, m):
        q = rng.choice(["Which company grew its {m} the most from fiscal {y0} to fiscal {y}?", "Which company had the largest percentage increase in {m} between fiscal {y0} and fiscal {y}?"]).format(m=PHRASES[m], y0=y - 1, y=y)
        return q, br.growth(m, y - 1, y)
    for pattern in (p_top, p_topk, p_count, p_avg, p_series, p_growth):
        made = tries = 0
        while made < 5 and tries < 200:
            tries += 1
            r = pattern(rng.randint(2017, 2023), rng.choice(plain))
            if r and add("sql", r[0], r[1], ["sql"], pattern=pattern.__name__):
                made += 1
        if made < 5:
            raise SystemExit(f"sql pattern {pattern.__name__}: only {made}")

    # hybrid: 15 dependent, 15 independent
    dep_forms = ["Of {g}, which reported the {o} {m} in fiscal {y}? Summarise what that company's {y} annual report says about {t}.",
                 "Which company among {g} had the {o} {m} in fiscal {y}, and what does its {y} 10-K say about {t}?"]
    cands = []
    for y in (2022, 2021, 2020):
        with_10k = [t for t in br.PREFIX if filing(t, y)]
        for key, phrase, num, den in bh.METRICS:
            ok = [t for t in with_10k if (t, num, y) in have and (den is None or (t, den, y) in have)
                  and not ({num, den} & {"revenue"} and t in bh.REVENUE_DEFECT)]
            for size in (2, 3):
                if len(ok) >= size:
                    cands.append((y, phrase, num, den, sorted(rng.sample(ok, size))))
    rng.shuffle(cands)
    for y, phrase, num, den, group in cands:
        if sum(i["class"] == "hybrid" for i in items) == 15:
            break
        o = rng.choice(["highest", "lowest"])
        rows = br.psql(bh.value_sql(num, den, y, group))
        if len(rows) != len(group) or len({r[1] for r in rows}) != len(rows):
            continue
        win = rows[0] if o == "highest" else rows[-1]
        names = [NAMES[t] for t in group]
        rng.shuffle(names)
        q = rng.choice(dep_forms).format(g=bh.phrase_list(names), o=o, m=phrase, y=y, t=rng.choice(bh.TOPICS))
        add("hybrid", q, bh.value_sql(num, den, y, group), ["hybrid"], dependent=True, filing=filing(win[0], y))
    ind_forms = ["{L} in fiscal {y}? Also, what does {n}'s {y} 10-K say about {t}?",
                 "For fiscal {y}: {l}, and how does {n}'s {y} annual report describe {t}?"]
    aggs = [("How many of the companies had negative net income", lambda y: f"SELECT count(*) FROM {br.AM} WHERE metric = 'net_income' AND period_end_year = {y} AND value < 0"),
            ("What was the combined capital expenditure of all the companies", lambda y: f"SELECT sum(value) FROM {br.AM} WHERE metric = 'capital_expenditure' AND period_end_year = {y}"),
            ("Which company held the most cash and cash equivalents", lambda y: br.one("cash_and_equivalents", y)),
            ("What was the average diluted earnings per share across the companies", lambda y: f"SELECT round(avg(value)::numeric, 2) FROM {br.AM} WHERE metric = 'eps_diluted' AND period_end_year = {y}"),
            ("Which two companies reported the most long-term debt", lambda y: br.one("long_term_debt", y, limit=2))]
    fil = [(t, y) for t in br.PREFIX for y in range(2017, 2024) if filing(t, y)]
    rng.shuffle(fil)
    for t, y in fil:
        if sum(i["class"] == "hybrid" for i in items) == 30:
            break
        lead, fn = rng.choice(aggs)
        form = rng.choice(ind_forms)
        q = form.format(L=lead, l=lead[0].lower() + lead[1:], y=y, n=NAMES[t], t=rng.choice(bh.TOPICS))
        add("hybrid", q, fn(y), ["hybrid"], dependent=False, filing=filing(t, y))

    # neither
    for c in rng.sample(OUTSIDE, 10):
        m = rng.choice(list(PHRASES))
        add("neither", rng.choice(["What was {c}'s {m} in fiscal {y}?", "How much {m} did {c} report for fiscal {y}?"]).format(c=c, m=PHRASES[m], y=rng.randint(2018, 2023)),
            None, ["neither"], why="company in neither source")
    live = ["What is {n}'s share price right now?", "What will {n}'s revenue be in fiscal 2028?",
            "What is {n}'s market capitalization today?", "What do analysts expect {n} to earn per share in fiscal 2027?",
            "How did {n}'s stock trade this morning?"]
    for t in rng.sample(sorted(NAMES), 10):
        add("neither", rng.choice(live).format(n=NAMES[t]), None, ["neither"], why="live or future data")
    for q in rng.sample(OFF_TOPIC, 10):
        add("neither", q, None, ["neither"], why="outside data analysis")

    counts = Counter(i["class"] for i in items)
    if any(counts[c] != PER_KIND for c in ("either", "sql", "hybrid", "neither")):
        raise SystemExit(f"template classes incomplete: {counts}")
    docs = docs_items(rng)
    kept_docs = [d for d in docs if "dropped" not in d]
    all_items = kept_docs + items
    for i in all_items:
        i["split"] = "r2"
    OUT.write_text(json.dumps({"preregistration": "docs/research/paperb_reruns_prereg.md", "seed": SEED, "writer": WRITER,
                               "checker": CHECKER, "data_defects": br.DATA_DEFECTS, "items": all_items,
                               "dropped_docs": [d for d in docs if "dropped" in d], "conversations": []}, indent=1) + "\n")
    # the authors' blind check: 40 items stratified by subclass, questions only
    crng = random.Random(SEED + 1)
    pick = []
    for sub, n in CHECK_SAMPLE.items():
        pick += crng.sample([i for i in all_items if i["subclass"] == sub], n)
    crng.shuffle(pick)
    KEY.write_text(json.dumps({f"C{k + 1:02d}": {"id": i["id"], "class": i["class"]} for k, i in enumerate(pick)}, indent=1) + "\n")
    lines = ["# Routing set R2: authors' blind label check (40 items)", "",
             "Fill this in yourself, without help from an AI tool and without looking at the other author's sheet. "
             "Do not open routing_set_r2.json or the key file first.", "",
             "**The two sources.** (1) A database of 23 standard annual figures (revenue, net income, total assets, "
             "cash flow, dividends, share repurchases, debt, EPS and so on) for 31 large US companies, fiscal years "
             "about 2015-2024. (2) A library of 84 annual and quarterly filings (10-K, 10-Q, 8-K) of the same "
             "companies, covering some years only.", "",
             "**Classes.** docs: only the filings can answer (narrative, segments, quarters, notes, figures the "
             "database does not hold). either: the database holds every figure needed AND the matching filing is in "
             "the library. sql: only the database can answer in practice (across many companies, or many years). "
             "hybrid: needs both (a database figure plus what a filing says). neither: no source holds it.", "",
             "For each question write one class after `Class:`.", ""]
    for k, i in enumerate(pick):
        lines += [f"## C{k + 1:02d}", "", i["question"], "", "Class: ", ""]
    SHEET.write_text("\n".join(lines))
    print(f"wrote {len(all_items)} items {dict(Counter(i['subclass'] for i in all_items))}; dropped docs: "
          f"{Counter(d['dropped'] for d in docs if 'dropped' in d)}; check sheet {SHEET.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
