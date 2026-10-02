#!/usr/bin/env python3
"""
scripts/eval_sec_crosscheck.py
The database-against-documents study (docs/specs/sec_crosscheck.md).

    python3 scripts/eval_sec_crosscheck.py coverage      Phase 0, no model calls (§3)

`coverage` records, for each of the 50 FinanceBench metrics-generated questions, the line items
it needs, the fiscal years, and whether `sec.annual_metrics` (values as first reported) holds
each one. The NEEDS table below was written from the question texts on 2026-10-02, before any
model was called; it names StateSync's metric names (sec.metric_tags), never the gold answers.
A question whose items are missing stays in every denominator, tagged not_in_database.

Fiscal years are resolved from the filing itself: the question's 10-K is the company's 10-K
whose DEI fiscal year (facts.filing_fy) is the document year; its latest one-year period ends
that fiscal year, and earlier fiscal years end one year apart before it.

The `ask` and `score` subcommands (§9) are written when the run is scheduled (needs model calls).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
QUESTIONS = REPO / "data" / "external" / "financebench" / "data" / "financebench_open_source.jsonl"
OUT = REPO / "docs" / "eval" / "sec_crosscheck_coverage.json"
CONTAINER = "statesync-sec-db"
YEAR_TOLERANCE_DAYS = 20  # noqa: literals - fiscal years ending on a weekday drift by a few days

# question index (file order among metrics-generated) -> [(metric, [fiscal years])]
NEEDS: dict[int, list[tuple[str, list[int]]]] = {
    0: [("capital_expenditure", [2018])],
    1: [("property_plant_equipment_net", [2018])],
    2: [("revenue", [2019]), ("property_plant_equipment_net", [2018, 2019])],
    3: [("capital_expenditure", [2017, 2018, 2019]), ("revenue", [2017, 2018, 2019])],
    4: [("operating_cash_flow", [2015]), ("current_liabilities", [2015])],
    5: [("operating_income", [2015, 2016])],
    6: [("operating_cash_flow", [2017]), ("current_liabilities", [2017])],
    7: [("net_income", [2022]), ("total_assets", [2021, 2022])],
    8: [("accounts_payable", [2016, 2017]), ("cost_of_revenue", [2017]), ("inventory", [2016, 2017])],
    9: [("revenue", [2016, 2017])],
    10: [("net_income", [2019])],
    11: [("accounts_receivable", [2020])],
    12: [("depreciation_and_amortization", [2015]), ("revenue", [2015])],
    13: [("dividends_paid", [2020])],
    14: [("operating_income", [2021]), ("depreciation_and_amortization", [2021])],
    15: [("net_income", [2015, 2016, 2017]), ("revenue", [2015, 2016, 2017])],
    16: [("inventory", [2019])],
    17: [("current_assets", [2016]), ("current_liabilities", [2016])],
    18: [("revenue", [2019, 2020])],
    19: [("operating_cash_flow", [2020])],
    20: [("property_plant_equipment_net", [2018])],
    21: [("net_income", [2017]), ("total_assets", [2016, 2017])],
    22: [("cost_of_revenue", [2021]), ("revenue", [2021])],
    23: [("dividends_paid", [2022]), ("net_income", [2022])],
    24: [("accounts_payable", [2019, 2020]), ("cost_of_revenue", [2020]), ("inventory", [2019, 2020])],
    25: [("operating_income", [2019, 2020, 2021]), ("revenue", [2019, 2020, 2021])],
    26: [("total_assets", [2021])],
    27: [("revenue", [2018]), ("property_plant_equipment_net", [2017, 2018])],
    28: [("inventory", [2018, 2019]), ("cost_of_revenue", [2019]), ("accounts_receivable", [2018, 2019]),
         ("revenue", [2019]), ("accounts_payable", [2018, 2019])],
    29: [("current_assets", [2020]), ("current_liabilities", [2020])],
    30: [("operating_cash_flow", [2020]), ("capital_expenditure", [2020])],
    31: [("dividends_paid", [2022]), ("net_income", [2022])],
    32: [("cost_of_revenue", [2019]), ("inventory", [2018, 2019])],
    33: [("revenue", [2020]), ("total_assets", [2019, 2020])],
    34: [("current_assets", [2021]), ("current_liabilities", [2021])],
    35: [("revenue", [2020, 2022])],
    36: [("accounts_payable", [2018])],
    37: [("capital_expenditure", [2018, 2019, 2020]), ("revenue", [2018, 2019, 2020])],
    38: [("cost_of_revenue", [2016])],
    39: [("operating_income", [2015]), ("depreciation_and_amortization", [2015]), ("revenue", [2015])],
    40: [("current_liabilities", [2017])],
    41: [("cost_of_revenue", [2016, 2017, 2018]), ("revenue", [2016, 2017, 2018])],
    42: [("current_assets", [2019])],
    43: [("cost_of_revenue", [2021]), ("inventory", [2020, 2021])],
    44: [("capital_expenditure", [2021])],
    45: [("operating_income", [2022]), ("depreciation_and_amortization", [2022]), ("capital_expenditure", [2022])],
    46: [("operating_income", [2022]), ("depreciation_and_amortization", [2022]), ("revenue", [2022])],
    47: [("accounts_payable", [2017, 2018]), ("cost_of_revenue", [2018]), ("inventory", [2017, 2018])],
    48: [("operating_income", [2018, 2019]), ("revenue", [2018, 2019])],
    49: [("operating_income", [2018, 2019, 2020]), ("depreciation_and_amortization", [2018, 2019, 2020]),
         ("revenue", [2018, 2019, 2020])],
}


def psql(sql: str) -> list[list[str]]:
    out = subprocess.run(["docker", "exec", CONTAINER, "psql", "-U", "postgres", "-d", "sec", "-At", "-F", "\t",
                          "-c", sql], check=True, capture_output=True, text=True).stdout
    return [line.split("\t") for line in out.splitlines() if line]


def questions() -> list[dict]:
    rows = [json.loads(line) for line in QUESTIONS.read_text().splitlines() if line.strip()]
    return [r for r in rows if r["question_type"] == "metrics-generated"]


def fiscal_year_end(company: str, year: int) -> date | None:
    """The end of fiscal `year` per the company's own 10-K for that year (DEI fiscal year)."""
    rows = psql(
        "SELECT max(f.period_end) FROM sec.facts f JOIN sec.companies c USING (cik) "
        f"WHERE c.financebench_name = '{company.replace(chr(39), chr(39) * 2)}' AND f.form = '10-K' "
        f"AND f.filing_fp = 'FY' AND f.filing_fy = {int(year)} AND f.period_start IS NOT NULL "
        "AND f.period_end - f.period_start BETWEEN 350 AND 380")
    return date.fromisoformat(rows[0][0]) if rows and rows[0][0] else None


def coverage() -> int:
    qs = questions()
    assert len(qs) == len(NEEDS) == 50, (len(qs), len(NEEDS))
    records = []
    for i, q in enumerate(qs):
        doc_year = int(re.search(r"_(\d{4})_", q["doc_name"]).group(1))
        anchor = fiscal_year_end(q["company"], doc_year)
        items = []
        for metric, years in NEEDS[i]:
            for fy in years:
                found = None
                if anchor is not None:
                    target = date(anchor.year - (doc_year - fy), anchor.month, min(anchor.day, 28))
                    rows = psql(
                        "SELECT period_end, value, tag FROM sec.annual_metrics "
                        f"WHERE company = '{q['company'].replace(chr(39), chr(39) * 2)}' AND metric = '{metric}' "
                        f"AND abs(period_end - DATE '{target.isoformat()}') <= {YEAR_TOLERANCE_DAYS} "
                        "ORDER BY abs(period_end - DATE '" + target.isoformat() + "') LIMIT 1")
                    if rows:
                        found = {"period_end": rows[0][0], "tag": rows[0][2]}   # the value itself is not recorded here
                items.append({"metric": metric, "fiscal_year": fy, "in_database": found is not None, "match": found})
        complete = all(it["in_database"] for it in items)
        records.append({"index": i, "financebench_id": q["financebench_id"], "company": q["company"],
                        "doc_name": q["doc_name"], "fiscal_year_end": anchor.isoformat() if anchor else None,
                        "items": items, "tag": None if complete else "not_in_database"})
    summary = {
        "questions": len(records),
        "complete_in_database": sum(r["tag"] is None for r in records),
        "items": sum(len(r["items"]) for r in records),
        "items_found": sum(it["in_database"] for r in records for it in r["items"]),
        "questions_sha256": hashlib.sha256(QUESTIONS.read_bytes()).hexdigest(),
    }
    OUT.write_text(json.dumps({"summary": summary, "questions": records}, indent=1) + "\n")
    print(json.dumps(summary, indent=1))
    for r in records:
        if r["tag"]:
            missing = [f"{it['metric']}@FY{it['fiscal_year']}" for it in r["items"] if not it["in_database"]]
            print(f"  {r['index']:2d} {r['doc_name']:<28} missing: {', '.join(missing)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("coverage", help="Phase 0: line-item coverage, no model calls")
    args = ap.parse_args()
    return coverage() if args.cmd == "coverage" else 1


if __name__ == "__main__":
    sys.exit(main())
