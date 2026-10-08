#!/usr/bin/env python3
"""
scripts/build_routing_set_r4.py
Paper B rerun R4 (pre-registered in docs/research/paperb_reruns_prereg.md, section R4): 90 single questions that
differ only in whether the database covers the entity and year asked about. Every class uses the SAME three
phrasings (seed 20261010), so a router can tell them apart only by knowing what the database holds.

  neither, unknown company (30)   a standard metric, fiscal 2016-2023, for a company in neither source (fixed list,
                                  disjoint from R2's and the first routing set's)
  neither, out-of-range year (15) a standard metric for a company in the database, fiscal 1998-2004
  either (30)                     a metric the database holds for a company and year whose 10-K is in the library
  sql (15)                        a metric the database holds for a company and year whose 10-K is not in the library
Either and sql reference queries are executed and must return a value. Built once.

    /opt/anaconda3/bin/python scripts/build_routing_set_r4.py
"""

from __future__ import annotations

import json
import random
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import build_hybrid_set as bh  # noqa: E402
import build_routing_set as br  # noqa: E402
import build_routing_set_r2 as r2  # noqa: E402
import paperb_docs as pd  # noqa: E402

OUT = REPO / "docs" / "eval" / "routing_set_r4.json"
SEED = 20261010
FORMS = ["What {m} did {c} report for fiscal {y}?", "Give me {c}'s {m} for fiscal year {y}.",
         "How much was {c}'s {m} in fiscal {y}?"]
OUTSIDE = ["Meta Platforms", "Broadcom", "Eli Lilly", "Merck", "AbbVie", "Abbott Laboratories", "Thermo Fisher Scientific",
           "Danaher", "Texas Instruments", "Qualcomm", "Honeywell", "Union Pacific", "UPS", "FedEx", "Lowe's", "TJX Companies",
           "Booking Holdings", "Morgan Stanley", "Goldman Sachs", "Wells Fargo", "Bank of America", "Citigroup",
           "Delta Air Lines", "General Electric", "Deere & Company", "Dow", "Kimberly-Clark", "Colgate-Palmolive",
           "Mondelez", "AT&T"]
PLAIN = [m for m in r2.PHRASES if m != "eps_diluted"]


def main() -> int:
    if OUT.exists():
        raise SystemExit(f"{OUT} exists; the pre-registered set is built once")
    rng = random.Random(SEED)
    assert not set(OUTSIDE) & set(r2.OUTSIDE), "must be disjoint from R2's list"
    ingest = json.loads(pd.INGEST.read_text())
    library = {d["doc_name"].upper() for d in ingest["documents"] if d.get("status") == "ready"}
    have = {(r[0], r[1], int(r[2])) for r in br.psql(f"SELECT ticker, metric, period_end_year FROM {br.AM} WHERE value IS NOT NULL")}
    old = {i["question"] for f in (br.OUT, r2.OUT) for i in json.loads(f.read_text())["items"]}

    def filing(t: str, y: int) -> str | None:
        name = f"{br.PREFIX.get(t, '')}_{y}_10K"
        return name if t in br.PREFIX and name.upper() in library else None

    items: list[dict] = []

    def add(cls: str, sub: str, c: str, m: str, y: int, acceptable: list[str], sql: str | None = None, **extra) -> bool:
        q = rng.choice(FORMS).format(c=c, m=r2.PHRASES[m], y=y)
        if q in old or any(i["question"] == q for i in items):
            return False
        if sql is not None:
            rows = br.psql(sql)
            if not rows or not rows[0][0]:
                return False
            extra.update(reference_sql=sql, sql_answer=br.answer_text(rows))
        items.append({"id": f"r4{sub[0]}{sum(i['subclass'] == sub for i in items) + 1:03d}", "class": cls, "subclass": sub,
                      "question": q, "acceptable": acceptable, "split": "r4", "company": c, "year": y, "metric": m, **extra})
        return True

    for c in OUTSIDE:                                                      # 30 unknown companies
        while not add("neither", "unknown_company", c, rng.choice(PLAIN), rng.randint(2016, 2023), ["neither"]):
            pass
    for t in rng.sample(sorted(r2.NAMES), 15):                             # 15 out-of-range years
        while not add("neither", "out_of_range_year", r2.NAMES[t], rng.choice(PLAIN), rng.randint(1998, 2004), ["neither"]):
            pass
    cands = [(t, y, m) for t in br.PREFIX for y in range(2015, 2024) for m in PLAIN if (t, m, y) in have
             and not (t in bh.REVENUE_DEFECT and m in ("revenue", "gross_profit", "cost_of_revenue"))]
    rng.shuffle(cands)
    used: set[tuple[str, int]] = set()
    for t, y, m in cands:
        n_either = sum(i["subclass"] == "either" for i in items)
        n_sql = sum(i["subclass"] == "sql" for i in items)
        if n_either == 30 and n_sql == 15:
            break
        if (t, y) in used:
            continue
        in_lib = filing(t, y) is not None
        if (in_lib and n_either == 30) or (not in_lib and n_sql == 15):
            continue
        sql = f"SELECT value FROM {br.AM} WHERE ticker = '{t}' AND metric = '{m}' AND period_end_year = {y}"
        if add("either" if in_lib else "sql", "either" if in_lib else "sql", r2.NAMES[t], m, y,
               ["sql", "docs"] if in_lib else ["sql"], sql, filing=filing(t, y)):
            used.add((t, y))
    counts = Counter(i["subclass"] for i in items)
    if counts != Counter({"unknown_company": 30, "out_of_range_year": 15, "either": 30, "sql": 15}):
        raise SystemExit(f"incomplete: {counts}")
    OUT.write_text(json.dumps({"preregistration": "docs/research/paperb_reruns_prereg.md (R4)", "seed": SEED,
                               "forms": FORMS, "items": items, "conversations": []}, indent=1) + "\n")
    print(f"wrote {len(items)} items {dict(counts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
