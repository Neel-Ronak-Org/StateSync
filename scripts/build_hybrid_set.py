#!/usr/bin/env python3
"""
scripts/build_hybrid_set.py
A held-out set of hybrid questions (database + filings) for the hybrid route end to end, built after T5/T5b
were designed on the routing set's 24 hybrid questions and before any of these is asked. Generated from
templates with a fixed seed, so it is reproducible and nobody chose the items by looking at answers.

  - dependent (60): "Among A, B and C, which had the highest/lowest <metric> in fiscal <Y>, and what does that
    company's <Y> 10-K say about <topic>?" A, B, C are drawn from the companies whose fiscal-<Y> 10-K is in the
    library and that all have the metric (and its denominator) for <Y>; the reference SQL picks the winner,
    whose <Y> 10-K is the filing the documents half must cite. A tie in the metric drops the item.
  - independent (40): an aggregate over all companies (count, average, median, total, the top three) or one
    company's series, plus "what does <company>'s <Y> 10-K say about <topic>?" for a filing in the library.

Reference SQL reads the fiscal-year view of build_routing_set (AM_CTE; its DATA_DEFECTS apply: General Mills'
revenue is excluded from every revenue-based item). Writes docs/eval/hybrid_set.json and a 10-item spot-check
sheet, docs/eval/hybrid_set_owner_check.md.

Analysis, fixed before any run (scripts/eval_hybrid.py --set held_out):
  primary    v3 (the evaluated system) vs shipped, "both halves right", exact McNemar, two-sided, alpha 0.05;
  secondary  v3_entities vs v3 on the 60 dependent items, the same test; everything else descriptive.

    python3 scripts/build_hybrid_set.py
"""

from __future__ import annotations

import json
import random
import sys
from itertools import combinations
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import build_routing_set as br  # noqa: E402

OUT = REPO / "docs" / "eval" / "hybrid_set.json"
SHEET = REPO / "docs" / "eval" / "hybrid_set_owner_check.md"
SEED = 20261006
N_DEPENDENT = 60            # noqa: literals - sized for power (docs/specs/routing_docs_fixes.md §5, 2026-10-06)
N_INDEPENDENT = 40          # noqa: literals - as above
OWNER_SAMPLE = 10           # noqa: literals - owner's spot check
GROUP_SIZES = (2, 3, 4)     # noqa: literals - companies named in a dependent question

# (metric key, phrase, numerator, denominator or None); a ratio is numerator / denominator * 100
METRICS = [
    ("revenue", "revenue", "revenue", None),
    ("net_income", "net income", "net_income", None),
    ("operating_income", "operating income", "operating_income", None),
    ("total_assets", "total assets at year end", "total_assets", None),
    ("long_term_debt", "long-term debt at year end", "long_term_debt", None),
    ("capital_expenditure", "capital expenditure", "capital_expenditure", None),
    ("share_repurchases", "spending on share repurchases", "share_repurchases", None),
    ("dividends_paid", "dividends paid", "dividends_paid", None),
    ("operating_cash_flow", "operating cash flow", "operating_cash_flow", None),
    ("cash_and_equivalents", "cash and cash equivalents at year end", "cash_and_equivalents", None),
    ("net_margin", "net profit margin", "net_income", "revenue"),
    ("operating_margin", "operating margin", "operating_income", "revenue"),
    ("capex_intensity", "capital expenditure as a share of revenue", "capital_expenditure", "revenue"),
    ("current_ratio", "current ratio", "current_assets", "current_liabilities"),
]
TOPICS = ["the main risks it faces", "how its business segments are organised", "its competition",
          "its employees and workforce", "its liquidity and capital resources", "any material legal proceedings",
          "what drove its results that year", "its properties and facilities"]
REVENUE_DEFECT = {"GIS"}     # build_routing_set.DATA_DEFECTS: General Mills' revenue tag is wrong
NAMES = {"MMM": "3M", "BA": "Boeing", "AMD": "AMD", "ADBE": "Adobe", "KO": "Coca-Cola", "PEP": "PepsiCo",
         "GIS": "General Mills", "JPM": "JPMorgan", "AXP": "American Express", "PYPL": "PayPal",
         "LMT": "Lockheed Martin", "GLW": "Corning", "CVS": "CVS Health", "JNJ": "Johnson & Johnson",
         "MGM": "MGM Resorts", "AES": "AES", "AWK": "American Water Works", "KHC": "Kraft Heinz",
         "NFLX": "Netflix", "WMT": "Walmart", "MSFT": "Microsoft", "NKE": "Nike", "BBY": "Best Buy",
         "AMCR": "Amcor", "ULTA": "Ulta Beauty", "VZ": "Verizon", "PFE": "Pfizer", "COST": "Costco"}


def phrase_list(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def value_sql(num: str, den: str | None, year: int, tickers: list[str]) -> str:
    tl = ", ".join(repr(t) for t in tickers)
    if den:
        return (f"SELECT n.ticker, round((n.value / d.value * 100)::numeric, 2) FROM {br.AM} n JOIN {br.AM} d "
                f"ON d.company = n.company AND d.period_end_year = n.period_end_year AND d.metric = '{den}' "
                f"WHERE n.metric = '{num}' AND n.period_end_year = {year} AND d.value <> 0 AND n.ticker IN ({tl}) ORDER BY 2 DESC")
    return (f"SELECT ticker, value FROM {br.AM} WHERE metric = '{num}' AND period_end_year = {year} "
            f"AND ticker IN ({tl}) ORDER BY 2 DESC")


def main() -> int:
    rng = random.Random(SEED)
    ingest = json.loads((REPO / "docs" / "eval" / "financebench_ingest_paragraph.json").read_text())
    library = {d["doc_name"].upper() for d in ingest["documents"] if d.get("status") == "ready"}
    have = {(r[0], r[1], int(r[2])) for r in br.psql(f"SELECT ticker, metric, period_end_year FROM {br.AM} WHERE value IS NOT NULL")}
    ticker_company = {r[0]: r[1] for r in br.psql(f"SELECT DISTINCT ticker, company FROM {br.AM}")}
    old = json.loads(br.OUT.read_text())
    old_questions = {i["question"] for i in old["items"]}

    def filing(ticker: str, year: int) -> str | None:
        name = f"{br.PREFIX.get(ticker, '')}_{year}_10K"
        return name if ticker in br.PREFIX and name.upper() in library else None

    # dependent
    candidates = []
    for year in (2022, 2021):
        with_10k = [t for t in br.PREFIX if filing(t, year)]
        for key, phrase, num, den in METRICS:
            ok = [t for t in with_10k if (t, num, year) in have and (den is None or (t, den, year) in have)
                  and not ({num, den} & {"revenue"} and t in REVENUE_DEFECT)]
            for size in GROUP_SIZES:
                for group in combinations(sorted(ok), size):
                    for order in ("highest", "lowest"):
                        candidates.append((year, key, phrase, num, den, list(group), order))
    rng.shuffle(candidates)
    items, seen_groups = [], set()
    for year, key, phrase, num, den, group, order in candidates:
        if len(items) >= N_DEPENDENT:
            break
        sig = (frozenset(group), key)
        if sig in seen_groups:
            continue
        rows = br.psql(value_sql(num, den, year, group))
        if len(rows) != len(group):
            continue
        values = [float(r[1]) for r in rows]
        if len(set(values)) != len(values):
            continue                                 # a tie has no single winner
        win = rows[0] if order == "highest" else rows[-1]
        names = [NAMES[t] for t in group]
        rng.shuffle(names)
        topic = rng.choice(TOPICS)
        q = (f"Among {phrase_list(names)}, which had the {order} {phrase} in fiscal {year}, and what does that "
             f"company's {year} 10-K say about {topic}?")
        if q in old_questions:
            continue
        seen_groups.add(sig)
        items.append({"id": f"hd{len(items) + 1:02d}", "class": "hybrid", "dependent": True, "question": q,
                      "source": "generated", "reference_sql": value_sql(num, den, year, group),
                      "sql_answer": f"{win[0]}, {win[1]}", "all_values": "; ".join(f"{r[0]}, {r[1]}" for r in rows),
                      "winner": ticker_company.get(win[0], win[0]), "filing": filing(win[0], year),
                      "metric": key, "year": year, "order": order, "topic": topic, "acceptable": ["hybrid"]})

    # independent
    aggregates = [
        ("how many of the companies reported a net loss", lambda y: f"SELECT count(*) FROM {br.AM} WHERE metric = 'net_income' AND period_end_year = {y} AND value < 0"),
        ("what was the average net profit margin across the companies",
         lambda y: f"SELECT round((avg(n.value / d.value) * 100)::numeric, 1) FROM {br.AM} n JOIN {br.AM} d ON d.company = n.company AND d.period_end_year = n.period_end_year AND d.metric = 'revenue' WHERE n.metric = 'net_income' AND n.period_end_year = {y} AND d.value <> 0 AND n.ticker <> 'GIS'"),
        ("what was the median operating margin across the companies",
         lambda y: f"SELECT round((percentile_cont(0.5) WITHIN GROUP (ORDER BY o.value / r.value) * 100)::numeric, 1) FROM {br.AM} o JOIN {br.AM} r ON r.company = o.company AND r.period_end_year = o.period_end_year AND r.metric = 'revenue' WHERE o.metric = 'operating_income' AND o.period_end_year = {y} AND r.ticker <> 'GIS'"),
        ("what was the total spent on share repurchases by all the companies", lambda y: f"SELECT sum(value) FROM {br.AM} WHERE metric = 'share_repurchases' AND period_end_year = {y}"),
        ("which three companies had the largest total assets", lambda y: f"SELECT company, value FROM {br.AM} WHERE metric = 'total_assets' AND period_end_year = {y} ORDER BY value DESC LIMIT 3"),
        ("which three companies had the highest diluted earnings per share", lambda y: f"SELECT company, value FROM {br.AM} WHERE metric = 'eps_diluted' AND period_end_year = {y} ORDER BY value DESC LIMIT 3"),
        ("how many of the companies paid dividends", lambda y: f"SELECT count(DISTINCT ticker) FROM {br.AM} WHERE metric = 'dividends_paid' AND period_end_year = {y} AND value > 0"),
        ("what was the average current ratio across the companies",
         lambda y: f"SELECT round(avg(a.value / l.value)::numeric, 2) FROM {br.AM} a JOIN {br.AM} l ON l.company = a.company AND l.period_end_year = a.period_end_year AND l.metric = 'current_liabilities' WHERE a.metric = 'current_assets' AND a.period_end_year = {y} AND l.value <> 0"),
    ]
    filings = sorted((t, y) for t in br.PREFIX for y in range(2015, 2024) if filing(t, y))
    pairs = [(a, t, y) for a in range(len(aggregates)) for (t, y) in filings if 2017 <= y <= 2023]
    rng.shuffle(pairs)
    used_filings: set[str] = set()
    for a, t, y in pairs:
        if sum(1 for i in items if not i["dependent"]) >= N_INDEPENDENT:
            break
        f = filing(t, y)
        if f in used_filings:
            continue
        lead, sql_fn = aggregates[a]
        sql = sql_fn(y)
        rows = br.psql(sql)
        if not rows or not any(c for c in rows[0]):
            continue
        topic = rng.choice(TOPICS)
        q = f"In fiscal {y}, {lead}, and what does {NAMES[t]}'s {y} 10-K say about {topic}?"
        q = q[0].upper() + q[1:]
        used_filings.add(f)
        items.append({"id": f"hi{sum(1 for i in items if not i['dependent']) + 1:02d}", "class": "hybrid", "dependent": False,
                      "question": q, "source": "generated", "reference_sql": sql, "sql_answer": br.answer_text(rows),
                      "winner": None, "filing": f, "year": y, "topic": topic, "acceptable": ["hybrid"]})

    dep = sum(i["dependent"] for i in items)
    if dep < N_DEPENDENT or len(items) - dep < N_INDEPENDENT:
        raise SystemExit(f"only {dep} dependent and {len(items) - dep} independent items could be built")
    OUT.write_text(json.dumps({"seed": SEED, "built_with": "scripts/build_hybrid_set.py", "data_defects": br.DATA_DEFECTS,
                               "analysis": {"primary": "v3 vs shipped, both halves right, exact McNemar two-sided, alpha 0.05",
                                            "secondary": "v3_entities vs v3 on dependent items, same test"},
                               "items": items}, indent=1) + "\n")
    pick = random.Random(SEED + 1).sample(items, OWNER_SAMPLE)
    lines = ["# Held-out hybrid set: spot check (10 of 100)", "",
             "For each item: does the question read naturally, is the reference database result the right answer to "
             "its numeric part, and is the filing the one its documents part needs? Write ok, or what is wrong.", ""]
    for it in pick:
        lines += [f"## {it['id']}", "", it["question"], "",
                  f"- Reference database result: `{it['sql_answer']}`" + (f" (all: `{it['all_values']}`)" if it.get("all_values") else ""),
                  f"- Filing: `{it['filing']}`", "", "**Check:** ", ""]
    SHEET.write_text("\n".join(lines))
    print(f"wrote {len(items)} items ({dep} dependent) to {OUT.relative_to(REPO)}; spot check {SHEET.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
