#!/usr/bin/env python3
"""
scripts/build_routing_set.py
Paper B Part D v3's labelled routing set (docs/specs/routing_docs_fixes.md §4). It is written before any
router runs. Labels follow the rules in RULES; every database fact is checked by running its reference
SQL on the SEC demo database (demo/sec_db.sh), and every filing a label relies on is checked against the
84-filing library (financebench_ingest_paragraph.json). Deterministic: the same inputs give the same file.

Test bed: one workspace = the SEC database (sec.annual_metrics: 23 metrics, as first reported, for the
32 FinanceBench companies) + the 84 FinanceBench filings.

    /opt/anaconda3/bin/python3 scripts/build_routing_set.py          writes docs/eval/routing_set.json
                                                                    and docs/eval/routing_set_owner_check.md
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
import paperb_docs as pd  # noqa: E402

OUT = REPO / "docs" / "eval" / "routing_set.json"
SHEET = REPO / "docs" / "eval" / "routing_set_owner_check.md"
COVERAGE = REPO / "docs" / "eval" / "sec_crosscheck_coverage.json"
SEED = 20261005
OWNER_SAMPLE = 30           # noqa: literals - spec §4, at least 30 labels checked by the owner
CONTAINER = "statesync-sec-db"

RULES = {
    "classes": {
        "docs": "Only the filings can answer: narrative, segments, quarters, 8-Ks, earnings releases, or a "
                "figure that sec.annual_metrics does not hold for that company and year.",
        "either": "Both can answer: every figure the answer needs is a standard annual metric that "
                  "sec.annual_metrics holds for that company and fiscal year (checked by query), and the "
                  "question's filing is in the library. Arithmetic on those figures is allowed.",
        "sql": "Only the database can answer in practice: across companies, or over more years than any "
               "filing in the library covers.",
        "hybrid": "Needs both: a database fact (across companies or years) plus what a filing in the "
                  "library says. 'dependent' when the documents half needs the database half's answer.",
        "neither": "Neither source holds it: companies outside the 32, the future, live market data, or "
                   "requests outside data analysis.",
    },
    "acceptable": {"docs": ["docs"], "either": ["sql", "docs"], "sql": ["sql"], "hybrid": ["hybrid"],
                   "neither": ["neither"]},
    "financebench": "metrics-generated: 'either' when sec_crosscheck_coverage.json (pre-registered "
                    "2026-10-02) has every item in the database, else 'docs'. domain-relevant and "
                    "novel-generated: 'docs' unless listed in EITHER_FB (each checked by query below).",
    "multi_turn": "Each turn has a gold standalone question; depends_on_history is true when the turn "
                  "cannot be understood alone. History given to a router uses the gold answers of earlier "
                  "turns, so routing is measured apart from answer errors.",
}

# domain-relevant / novel-generated FinanceBench questions answerable from standard annual figures,
# with the figures checked in the database (2026-10-05, before any router ran). Six candidates failed
# the check (00215, 00723, 00799, 00216, 00566: a figure missing) and stay 'docs'.
EITHER_FB = {
    "00499": ("MMM", ["capital_expenditure", "revenue", "property_plant_equipment_net", "total_assets", "net_income"], [2022]),
    "00790": ("CVS", ["capital_expenditure", "revenue", "property_plant_equipment_net", "total_assets", "net_income"], [2022]),
    "00438": ("ADBE", ["operating_income", "revenue"], [2020, 2021, 2022]),
    "00591": ("ADBE", ["operating_cash_flow", "capital_expenditure", "net_income"], [2020, 2021, 2022]),
    "00540": ("AES", ["cost_of_revenue", "inventory"], [2021, 2022]),
    "00711": ("JNJ", ["cost_of_revenue", "inventory"], [2021, 2022]),
    "00222": ("AMD", ["current_assets", "inventory", "current_liabilities", "cash_and_equivalents", "accounts_receivable"], [2022]),
    "00684": ("AMCR", ["gross_profit", "revenue"], [2021, 2022, 2023]),
    "00678": ("BA", ["revenue", "cost_of_revenue"], [2020, 2021, 2022]),
    "00685": ("BBY", ["gross_profit", "revenue"], [2021, 2022, 2023]),
    "00070": ("AWK", ["current_assets", "current_liabilities"], [2022]),
    "00005": ("GLW", ["current_assets", "current_liabilities"], [2022]),
    "00080": ("PYPL", ["current_assets", "current_liabilities"], [2022]),
    "00956": ("JNJ", ["revenue", "net_income"], [2021, 2022]),
    "01254": ("MGM", ["dividends_paid"], [2022]),
    "00552": ("MSFT", ["long_term_debt"], [2022, 2023]),
    "00302": ("PFE", ["property_plant_equipment_net"], [2020, 2021]),
}

AM = "am"
# Every reference query reads `am`: sec.annual_metrics with the fiscal year taken as the calendar year of
# (period_end - 20 days), so a 52/53-week year ending on 1-2 January (Johnson & Johnson) counts in the year
# it covers (the cross-check study's tolerance, YEAR_TOLERANCE_DAYS), and one row per company, metric and
# fiscal year (the latest period end). Known database defects, recorded rather than patched: General
# Mills' `Revenues` tag holds about 2 billion dollars a year (its net sales are about 18 billion), so no
# question below depends on General Mills' revenue; Kraft Heinz has no fiscal-2022 revenue.
AM_CTE = ("am AS (SELECT DISTINCT ON (company, metric, fy) company, ticker, metric, fy AS period_end_year, period_end, "
          "value, unit, tag FROM (SELECT *, extract(year FROM period_end - interval '20 days')::int AS fy "
          "FROM sec.annual_metrics) x ORDER BY company, metric, fy, period_end DESC)")
DATA_DEFECTS = ["General Mills: the Revenues tag in sec.annual_metrics is about 2 billion dollars a year, not net sales "
                "(about 18 billion); revenue ratios for General Mills are wrong in the database.",
                "Kraft Heinz: no fiscal-2022 revenue row.",
                "Fiscal years: period_end_year mislabels 52/53-week years ending 1-2 January (Johnson & Johnson); "
                "reference queries use the year of period_end - 20 days."]


def ratio(num: str, den: str, year: int, extra: str = "", order: str = "DESC", limit: int | None = 1) -> str:
    lim = f" LIMIT {limit}" if limit else ""
    return (f"SELECT n.company, round((n.value / d.value * 100)::numeric, 1) AS pct FROM {AM} n "
            f"JOIN {AM} d ON d.company = n.company AND d.period_end_year = n.period_end_year AND d.metric = '{den}' "
            f"WHERE n.metric = '{num}' AND n.period_end_year = {year} AND d.value <> 0 {extra} "
            f"ORDER BY n.value / d.value {order}{lim}")


def growth(metric: str, y0: int, y1: int, extra: str = "", order: str = "DESC", limit: int | None = 1) -> str:
    lim = f" LIMIT {limit}" if limit else ""
    return (f"SELECT b.company, round(((b.value / a.value - 1) * 100)::numeric, 1) AS pct FROM {AM} a "
            f"JOIN {AM} b ON b.company = a.company AND b.metric = a.metric AND b.period_end_year = {y1} "
            f"WHERE a.metric = '{metric}' AND a.period_end_year = {y0} AND a.value > 0 {extra} "
            f"ORDER BY b.value / a.value {order}{lim}")


def one(metric: str, year: int, order: str = "DESC", limit: int = 1, extra: str = "") -> str:
    return (f"SELECT company, value FROM {AM} WHERE metric = '{metric}' AND period_end_year = {year} {extra} "
            f"ORDER BY value {order} LIMIT {limit}")


def series(ticker: str, metric: str, y0: int, y1: int) -> str:
    return (f"SELECT period_end_year, value FROM {AM} WHERE ticker = '{ticker}' AND metric = '{metric}' "
            f"AND period_end_year BETWEEN {y0} AND {y1} ORDER BY 1")


SQL_ONLY = [
    ("s01", "Which of the companies had the highest revenue in fiscal year 2022?", one("revenue", 2022)),
    ("s02", "Which company reported the largest net income in fiscal 2021?", one("net_income", 2021)),
    ("s03", "How many of the companies reported a net loss in fiscal 2020?",
     f"SELECT count(*) FROM {AM} WHERE metric = 'net_income' AND period_end_year = 2020 AND value < 0"),
    ("s04", "List the five companies with the highest research and development spending in fiscal 2022.",
     one("research_and_development", 2022, limit=5)),
    ("s05", "What was the median capital expenditure across all the companies in fiscal 2019?",
     f"SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY value) FROM {AM} WHERE metric = 'capital_expenditure' AND period_end_year = 2019"),
    ("s06", "Which companies grew revenue every year from fiscal 2018 through fiscal 2022?",
     f"WITH r AS (SELECT company, period_end_year y, value v FROM {AM} WHERE metric = 'revenue' AND period_end_year BETWEEN 2018 AND 2022) "
     "SELECT a.company FROM r a JOIN r b ON b.company = a.company AND b.y = a.y + 1 GROUP BY a.company "
     "HAVING count(*) = 4 AND bool_and(b.v > a.v) ORDER BY 1"),
    ("s07", "Which company had the highest gross margin in fiscal 2021?", ratio("gross_profit", "revenue", 2021)),
    ("s08", "Which companies had long-term debt larger than their stockholders' equity at the end of fiscal 2022?",
     f"SELECT d.company FROM {AM} d JOIN {AM} e ON e.company = d.company AND e.period_end_year = d.period_end_year "
     "AND e.metric = 'stockholders_equity' WHERE d.metric = 'long_term_debt' AND d.period_end_year = 2022 AND d.value > e.value ORDER BY 1"),
    ("s09", "Which three companies paid the most in dividends in fiscal 2022?", one("dividends_paid", 2022, limit=3)),
    ("s10", "How much did the companies spend on share repurchases in total in fiscal 2021, and how did that compare with fiscal 2020?",
     f"SELECT period_end_year, sum(value), count(*) FROM {AM} WHERE metric = 'share_repurchases' AND period_end_year IN (2020, 2021) GROUP BY 1 ORDER BY 1"),
    ("s11", "Which company had the lowest current ratio at the end of fiscal 2022?",
     ratio("current_assets", "current_liabilities", 2022, order="ASC")),
    ("s12", "What was the average operating margin of Coca-Cola and PepsiCo in fiscal 2022?",
     f"SELECT round((avg(o.value / r.value) * 100)::numeric, 1), count(*) FROM {AM} o JOIN {AM} r ON r.company = o.company "
     "AND r.period_end_year = o.period_end_year AND r.metric = 'revenue' WHERE o.metric = 'operating_income' "
     "AND o.period_end_year = 2022 AND o.ticker IN ('KO', 'PEP')"),
    ("s13", "Which company generated the most operating cash flow in fiscal 2020?",
     one("operating_cash_flow", 2020)),
    ("s14", "In which fiscal year did Netflix's revenue first exceed 20 billion dollars?",
     f"SELECT min(period_end_year) FROM {AM} WHERE ticker = 'NFLX' AND metric = 'revenue' AND value > 20e9"),
    ("s15", "How many of the companies had total assets above 100 billion dollars at the end of fiscal 2022?",
     f"SELECT count(*) FROM {AM} WHERE metric = 'total_assets' AND period_end_year = 2022 AND value > 1e11"),
    ("s16", "Which company had the largest percentage drop in revenue in fiscal 2020 compared with fiscal 2019?",
     growth("revenue", 2019, 2020, order="ASC")),
    ("s17", "What was Walmart's revenue in each fiscal year from 2015 to 2024?", series("WMT", "revenue", 2015, 2024)),
    ("s18", "Compare the diluted earnings per share of Microsoft, Nike and Costco in fiscal 2022.",
     f"SELECT company, value FROM {AM} WHERE ticker IN ('MSFT', 'NKE', 'COST') AND metric = 'eps_diluted' AND period_end_year = 2022 ORDER BY 1"),
    ("s19", "Which companies spent more than 15% of their revenue on research and development in fiscal 2022?",
     ratio("research_and_development", "revenue", 2022, extra="AND n.value / d.value > 0.15", limit=None)),
    ("s20", "How many of the companies reported revenue above 50 billion dollars in fiscal 2021?",
     f"SELECT count(*) FROM {AM} WHERE metric = 'revenue' AND period_end_year = 2021 AND value > 5e10"),
    ("s21", "Which company reported the largest depreciation and amortization expense in fiscal 2022?",
     one("depreciation_and_amortization", 2022)),
    ("s22", "What was Boeing's net income in each year from fiscal 2017 to fiscal 2023?", series("BA", "net_income", 2017, 2023)),
    ("s23", "Which companies reported negative stockholders' equity at the end of fiscal 2022?",
     f"SELECT company, value FROM {AM} WHERE metric = 'stockholders_equity' AND period_end_year = 2022 AND value < 0 ORDER BY 1"),
    ("s24", "How many of the companies increased operating income in fiscal 2022 compared with fiscal 2021?",
     f"SELECT count(*) FROM {AM} a JOIN {AM} b ON b.company = a.company AND b.metric = a.metric AND b.period_end_year = 2022 "
     "WHERE a.metric = 'operating_income' AND a.period_end_year = 2021 AND b.value > a.value"),
    ("s25", "Which company had the smallest revenue in fiscal 2018?", one("revenue", 2018, order="ASC")),
    ("s26", "Which five companies held the most cash and cash equivalents at the end of fiscal 2023?",
     one("cash_and_equivalents", 2023, limit=5)),
    ("s27", "What was the median net margin across all the companies in fiscal 2019?",
     f"SELECT round((percentile_cont(0.5) WITHIN GROUP (ORDER BY n.value / r.value) * 100)::numeric, 1), count(*) FROM {AM} n JOIN {AM} r ON r.company = n.company "
     "AND r.period_end_year = n.period_end_year AND r.metric = 'revenue' WHERE n.metric = 'net_income' AND n.period_end_year = 2019"),
    ("s28", "Which company had the highest inventory as a share of its current assets at the end of fiscal 2022?",
     ratio("inventory", "current_assets", 2022)),
    ("s29", "How did Amazon's operating cash flow change each year from fiscal 2018 to fiscal 2023?",
     series("AMZN", "operating_cash_flow", 2018, 2023)),
    ("s30", "Which companies grew both revenue and net income in fiscal 2023 compared with fiscal 2022?",
     f"SELECT r0.company FROM {AM} r0 JOIN {AM} r1 ON r1.company = r0.company AND r1.metric = 'revenue' AND r1.period_end_year = 2023 "
     f"JOIN {AM} n0 ON n0.company = r0.company AND n0.metric = 'net_income' AND n0.period_end_year = 2022 "
     f"JOIN {AM} n1 ON n1.company = r0.company AND n1.metric = 'net_income' AND n1.period_end_year = 2023 "
     "WHERE r0.metric = 'revenue' AND r0.period_end_year = 2022 AND r1.value > r0.value AND n1.value > n0.value ORDER BY 1"),
]

# Library prefixes (financebench doc_name) by ticker, for the filings a hybrid label relies on.
PREFIX = {"MMM": "3M", "BA": "BOEING", "AMD": "AMD", "ADBE": "ADOBE", "KO": "COCACOLA", "PEP": "PEPSICO",
          "GIS": "GENERALMILLS", "JPM": "JPMORGAN", "AXP": "AMERICANEXPRESS", "PYPL": "PAYPAL",
          "LMT": "LOCKHEEDMARTIN", "GLW": "CORNING", "CVS": "CVSHEALTH", "JNJ": "JOHNSON_JOHNSON",
          "MGM": "MGMRESORTS", "AES": "AES", "AWK": "AMERICANWATERWORKS", "KHC": "KRAFTHEINZ",
          "NFLX": "NETFLIX", "WMT": "WALMART", "MSFT": "MICROSOFT", "NKE": "NIKE", "BBY": "BESTBUY",
          "AMCR": "AMCOR", "ULTA": "ULTABEAUTY", "VZ": "VERIZON", "PFE": "PFIZER", "COST": "COSTCO"}


def among(sql_select: str, tickers: list[str]) -> str:
    return sql_select.replace("ORDER BY", f"AND n.ticker IN ({', '.join(repr(t) for t in tickers)}) ORDER BY", 1)


# Dependent hybrids: the SQL half picks a company among ones whose fiscal-2022 10-K is in the library; the
# documents half is about that company's filing. (id, question, SQL returning (ticker-bearing) winner, filing suffix)
HYBRID_DEP = [
    ("h01", "Among 3M, Boeing, AMD and Adobe, which had the lowest operating margin in fiscal 2022, and what does that company's 2022 annual report say drove its results?",
     ["MMM", "BA", "AMD", "ADBE"], ("operating_income", "revenue", "ASC")),
    ("h02", "Which of Coca-Cola, PepsiCo and General Mills grew revenue fastest in fiscal 2022, and what reasons does its 2022 10-K give for that growth?",
     ["KO", "PEP", "GIS"], ("revenue_growth", None, "DESC")),
    ("h03", "Which of JPMorgan, American Express and PayPal reported the largest net income in fiscal 2022, and what risks does its 2022 10-K highlight?",
     ["JPM", "AXP", "PYPL"], ("net_income", None, "DESC")),
    ("h04", "Among Lockheed Martin, Boeing and 3M, which spent the most on capital expenditure in fiscal 2022, and what does its 10-K say that spending is for?",
     ["LMT", "BA", "MMM"], ("capital_expenditure", None, "DESC")),
    ("h05", "Which of Corning, AMD and 3M spent the most on research and development in fiscal 2022, and what are its main products according to its 2022 10-K?",
     ["GLW", "AMD", "MMM"], ("research_and_development", None, "DESC")),
    ("h06", "Between CVS Health and Johnson & Johnson, which had the larger revenue in fiscal 2022, and how does its 2022 10-K describe its business segments?",
     ["CVS", "JNJ"], ("revenue", None, "DESC")),
    ("h07", "Which of MGM Resorts, Boeing and AES had the most long-term debt at the end of fiscal 2022, and what does its 2022 10-K say about its debt maturities?",
     ["MGM", "BA", "AES"], ("long_term_debt", None, "DESC")),
    ("h08", "Among American Water Works, AES and 3M, which spent the most on capital expenditure relative to revenue in fiscal 2022, and how does its 2022 10-K describe its capital investment plans?",
     ["AWK", "AES", "MMM"], ("capital_expenditure", "revenue", "DESC")),
    ("h09", "Which of Adobe, PayPal and AMD had the highest gross margin in fiscal 2022, and what does its 2022 10-K list as its main competitive risks?",
     ["ADBE", "PYPL", "AMD"], ("gross_profit", "revenue", "DESC")),
    ("h10", "Among Coca-Cola, PepsiCo and Kraft Heinz, which paid the most in dividends in fiscal 2022, and what does its 2022 10-K say about its dividend policy?",
     ["KO", "PEP", "KHC"], ("dividends_paid", None, "DESC")),
    ("h11", "Which of Lockheed Martin, Corning and General Mills spent the most on share repurchases in fiscal 2022, and what does its 10-K say about its repurchase program?",
     ["LMT", "GLW", "GIS"], ("share_repurchases", None, "DESC")),
    ("h12", "Among Verizon, JPMorgan and PepsiCo, which had the largest total assets at the end of fiscal 2022, and what does its 2022 10-K say about its liquidity?",
     ["VZ", "JPM", "PEP"], ("total_assets", None, "DESC")),
]

# Independent hybrids: (id, question, SQL half, filing the documents half needs)
HYBRID_IND = [
    ("h13", "What was the average revenue growth across all the companies in fiscal 2022, and what does AMD's 2022 10-K say drove its own revenue change?",
     f"SELECT round((avg(b.value / a.value - 1) * 100)::numeric, 1), count(*) FROM {AM} a JOIN {AM} b ON b.company = a.company AND b.metric = a.metric "
     "AND b.period_end_year = 2022 WHERE a.metric = 'revenue' AND a.period_end_year = 2021 AND a.value > 0", "AMD_2022_10K"),
    ("h14", "How many of the companies reported a net loss in fiscal 2022, and does Boeing's 2022 10-K describe any material legal proceedings?",
     f"SELECT count(*) FROM {AM} WHERE metric = 'net_income' AND period_end_year = 2022 AND value < 0", "BOEING_2022_10K"),
    ("h15", "What was the median operating margin across the companies in fiscal 2022, and how does 3M's 2022 10-K explain its own operating margin?",
     f"SELECT round((percentile_cont(0.5) WITHIN GROUP (ORDER BY o.value / r.value) * 100)::numeric, 1) FROM {AM} o JOIN {AM} r ON r.company = o.company "
     "AND r.period_end_year = o.period_end_year AND r.metric = 'revenue' WHERE o.metric = 'operating_income' AND o.period_end_year = 2022", "3M_2022_10K"),
    ("h16", "What were the total dividends paid by all the companies in fiscal 2021, and what does Coca-Cola's 2021 10-K say about its dividend?",
     f"SELECT sum(value), count(*) FROM {AM} WHERE metric = 'dividends_paid' AND period_end_year = 2021", "COCACOLA_2021_10K"),
    ("h17", "How did Netflix's revenue change each year from fiscal 2015 to fiscal 2022, and what does its 2017 10-K say about its streaming strategy?",
     series("NFLX", "revenue", 2015, 2022), "NETFLIX_2017_10K"),
    ("h18", "What was Walmart's net income in each fiscal year from 2018 to 2023, and what does its 2020 10-K say about its e-commerce investments?",
     series("WMT", "net_income", 2018, 2023), "WALMART_2020_10K"),
    ("h19", "How did Microsoft's revenue grow from fiscal 2016 to fiscal 2023, and what does its 2023 10-K say about its cloud business?",
     series("MSFT", "revenue", 2016, 2023), "MICROSOFT_2023_10K"),
    ("h20", "What was Nike's diluted earnings per share in each year from fiscal 2018 to fiscal 2023, and what does its 2023 10-K say about inventory levels?",
     series("NKE", "eps_diluted", 2018, 2023), "NIKE_2023_10K"),
    ("h21", "How did Best Buy's revenue change from fiscal 2017 to fiscal 2024, and what does its 2023 10-K say about its store count?",
     series("BBY", "revenue", 2017, 2024), "BESTBUY_2023_10K"),
    ("h22", "What was Amcor's capital expenditure in each year from fiscal 2018 to fiscal 2023, and what acquisitions does its 2023 10-K describe?",
     series("AMCR", "capital_expenditure", 2018, 2023), "AMCOR_2023_10K"),
    ("h23", "How did Ulta Beauty's net income change from fiscal 2019 to fiscal 2023, and what does its 2023 10-K say about its loyalty program?",
     series("ULTA", "net_income", 2019, 2023), "ULTABEAUTY_2023_10K"),
    ("h24", "Which five companies spent the most on research and development in fiscal 2022, and what does Corning's 2022 10-K say about its research focus?",
     one("research_and_development", 2022, limit=5), "CORNING_2022_10K"),
]

# Out of scope. Companies here are checked absent from both sources.
NEITHER = [
    ("n01", "What was Tesla's revenue in fiscal 2022?", "Tesla"),
    ("n02", "How much did Apple spend on research and development in fiscal 2021?", "Apple"),
    ("n03", "What was Alphabet's operating margin in 2022?", "Alphabet"),
    ("n04", "What is Nvidia's current market capitalization?", "Nvidia"),
    ("n05", "How many employees does Intel have?", "Intel"),
    ("n06", "What will 3M's revenue be in fiscal 2027?", None),
    ("n07", "What is Boeing's share price today?", None),
    ("n08", "Who will be PepsiCo's chief executive in 2030?", None),
    ("n09", "What is the weather forecast for Seattle tomorrow?", None),
    ("n10", "Write a short poem about Coca-Cola.", None),
    ("n11", "What is the home address of Walmart's chief executive?", None),
    ("n12", "Translate 'good morning' into French.", None),
    ("n13", "What was Exxon Mobil's capital expenditure in 2022?", "Exxon"),
    ("n14", "How did Meta's advertising revenue change in 2023?", "Meta"),
    ("n15", "What were Nike's sales at its Paris flagship store last week?", None),
    ("n16", "Which restaurant has the best pizza in New York?", None),
    ("n17", "What is the GDP of India?", None),
    ("n18", "How much did Ford spend on share buybacks in 2021?", "Ford"),
    ("n19", "What is Microsoft's share of the games console market this month?", None),
    ("n20", "What dividend will Verizon declare next quarter?", None),
]

FB = "financebench"


def fbq(qs: dict, suffix: str) -> dict:
    return qs[f"financebench_id_{suffix}"]


# Multi-turn conversations. Turn fields: question, depends (on history), standalone (gold), cls, and either
# fb (a FinanceBench id suffix whose class and gold answer the turn takes) or sql (reference SQL for the
# gold answer). A {X} in a later turn is replaced by the earlier turn's SQL winner (the ticker's company).
CONVERSATIONS = [
    ("c01", [dict(q="What was 3M's capital expenditure in fiscal 2018?", fb="03029"),
             dict(q="And in fiscal 2022?", depends=True, standalone="What was 3M's capital expenditure in fiscal 2022?", cls="either",
                  sql=series("MMM", "capital_expenditure", 2022, 2022)),
             dict(q="What drove its operating margin change that year?", depends=True,
                  standalone="What drove 3M's operating margin change in fiscal 2022?", fb="01226")]),
    ("c02", [dict(q="What are the major products and services that AMD sells as of FY22?", fb="00995"),
             dict(q="How much did it spend on research and development that year?", depends=True,
                  standalone="How much did AMD spend on research and development in fiscal 2022?", cls="either",
                  sql=series("AMD", "research_and_development", 2022, 2022))]),
    ("c03", [dict(q="Which of Coca-Cola, PepsiCo and General Mills had the highest revenue in fiscal 2022?", cls="sql",
                  sql=f"SELECT ticker, company, value FROM {AM} WHERE metric = 'revenue' AND period_end_year = 2022 AND ticker IN ('KO', 'PEP', 'GIS') ORDER BY value DESC LIMIT 1"),
             dict(q="Has it reported any materially important legal battles in fiscal 2022?", depends=True,
                  standalone="Has {X} reported any materially important ongoing legal battles in fiscal 2022?", cls="docs", needs_filing="2022_10K")]),
    ("c04", [dict(q="Does Boeing have an improving gross margin profile as of FY2022?", fb="00678"),
             dict(q="Who are its primary customers?", depends=True, standalone="Who are the primary customers of Boeing as of FY2022?", fb="01290")]),
    ("c05", [dict(q="How many of the companies reported a net loss in fiscal 2020?", cls="sql", sql=SQL_ONLY[2][2]),
             dict(q="Which ones?", depends=True, standalone="Which of the companies reported a net loss in fiscal 2020?", cls="sql",
                  sql=f"SELECT company FROM {AM} WHERE metric = 'net_income' AND period_end_year = 2020 AND value < 0 ORDER BY 1")]),
    ("c06", [dict(q="What was Nike's revenue in fiscal 2023?", cls="either", sql=series("NKE", "revenue", 2023, 2023), needs_filing_of="NIKE_2023_10K"),
             dict(q="What does its 10-K say about inventory levels?", depends=True,
                  standalone="What does Nike's fiscal 2023 10-K say about its inventory levels?", cls="docs", filing="NIKE_2023_10K")]),
    ("c07", [dict(q="Did Pfizer grow its PPNE between FY20 and FY21?", fb="00302"),
             dict(q="What are three main companies it acquired, according to that 10-K?", depends=True,
                  standalone="What are three main companies acquired by Pfizer mentioned in its fiscal 2021 10-K?", fb="02416")]),
    ("c08", [dict(q="Which company spent the most on share repurchases in fiscal 2022?", cls="sql", sql=one("share_repurchases", 2022)),
             dict(q="And the least?", depends=True, standalone="Which company spent the least on share repurchases in fiscal 2022?", cls="sql",
                  sql=one("share_repurchases", 2022, order="ASC"))]),
    ("c09", [dict(q="What drove revenue change as of the FY22 for AMD?", fb="01198"),
             dict(q="What was its revenue in each of the five years to then?", depends=True,
                  standalone="What was AMD's revenue in each fiscal year from 2018 to 2022?", cls="sql", sql=series("AMD", "revenue", 2018, 2022))]),
    ("c10", [dict(q="Does Paypal have positive working capital based on FY2022 data?", fb="00080"),
             dict(q="How about American Water Works?", depends=True,
                  standalone="Does American Water Works have positive working capital based on FY2022 data?", fb="00070")]),
    ("c11", [dict(q="Which debt securities are registered to trade on a national securities exchange under Ulta Beauty's name as of FY2023?", fb="00746"),
             dict(q="What was its net income that year?", depends=True, standalone="What was Ulta Beauty's net income in fiscal 2023?", cls="either",
                  sql=series("ULTA", "net_income", 2023, 2023), needs_filing_of="ULTABEAUTY_2023_10K")]),
    ("c12", [dict(q="What was the combined revenue of all the companies in fiscal 2021?", cls="sql",
                  sql=f"SELECT sum(value), count(*) FROM {AM} WHERE metric = 'revenue' AND period_end_year = 2021"),
             dict(q="Write me a haiku about that.", depends=True, standalone="Write a haiku about the combined revenue of the companies in fiscal 2021.", cls="neither")]),
    ("c13", [dict(q="Is Verizon a capital intensive business based on FY 2022 data?", fb="00215"),
             dict(q="Has it increased its debt between 2021 and 2022?", depends=True,
                  standalone="Has Verizon increased its debt on balance sheet between 2022 and the 2021 fiscal period?", fb="00566")]),
    ("c14", [dict(q="What was Microsoft's diluted earnings per share in fiscal 2023?", cls="either", sql=series("MSFT", "eps_diluted", 2023, 2023),
                  needs_filing_of="MICROSOFT_2023_10K"),
             dict(q="And Netflix's in fiscal 2017?", depends=True, standalone="What was Netflix's diluted earnings per share in fiscal 2017?", cls="either",
                  sql=series("NFLX", "eps_diluted", 2017, 2017), needs_filing_of="NETFLIX_2017_10K")]),
    ("c15", [dict(q="Has Microsoft increased its debt on balance sheet between FY2023 and the FY2022 period?", fb="00552"),
             dict(q="What about Tesla?", depends=True, standalone="Has Tesla increased its debt on balance sheet between FY2023 and FY2022?", cls="neither")]),
    ("c16", [dict(q="What was Lockheed Martin's revenue in fiscal 2020, 2021 and 2022?", cls="either", sql=series("LMT", "revenue", 2020, 2022),
                  needs_filing_of="LOCKHEEDMARTIN_2022_10K"),
             dict(q="Which of those years had the highest operating margin?", depends=True,
                  standalone="In which of fiscal 2020, 2021 and 2022 did Lockheed Martin have the highest operating margin?", cls="either",
                  sql=f"SELECT o.period_end_year, round((o.value / r.value * 100)::numeric, 1) FROM {AM} o JOIN {AM} r ON r.company = o.company "
                      "AND r.period_end_year = o.period_end_year AND r.metric = 'revenue' WHERE o.ticker = 'LMT' AND o.metric = 'operating_income' "
                      "AND o.period_end_year BETWEEN 2020 AND 2022 ORDER BY o.value / r.value DESC LIMIT 1", needs_filing_of="LOCKHEEDMARTIN_2022_10K")]),
    ("c17", [dict(q="What industry does AMCOR primarily operate in?", fb="01148"),
             dict(q="What major acquisitions has it done in FY2023, FY2022 and FY2021?", depends=True,
                  standalone="What are major acquisitions that AMCOR has done in FY2023, FY2022 and FY2021?", fb="01079")]),
    ("c18", [dict(q="Are JnJ's FY2022 financials that of a high growth company?", fb="00956"),
             dict(q="Which of its business segments will be treated as a discontinued operation from August 30, 2023?", depends=True,
                  standalone="Which business segment of JnJ will be treated as a discontinued operation from August 30, 2023 onward?", fb="01488")]),
    ("c19", [dict(q="Which companies spent more than 15% of their revenue on research and development in fiscal 2022?", cls="sql", sql=SQL_ONLY[18][2]),
             dict(q="For AMD, what drove its operating margin change that year?", depends=True,
                  standalone="What drove operating margin change as of the FY22 for AMD?", fb="00917")]),
    ("c20", [dict(q="What is the weather in Chicago today?", cls="neither"),
             dict(q="OK. What was Boeing's net income in fiscal 2022?", depends=False, standalone="What was Boeing's net income in fiscal 2022?", cls="either",
                  sql=series("BA", "net_income", 2022, 2022), needs_filing_of="BOEING_2022_10K")]),
    ("c21", [dict(q="Is CVS Health a capital-intensive business based on FY2022 data?", fb="00790"),
             dict(q="Has it reported any materially important legal battles from 2022, 2021 and 2020?", depends=True,
                  standalone="Has CVS Health reported any materially important ongoing legal battles from 2022, 2021 and 2020?", fb="01107"),
             dict(q="How did its revenue compare with Walmart's in fiscal 2022?", depends=True,
                  standalone="How did CVS Health's revenue compare with Walmart's in fiscal 2022?", cls="sql",
                  sql=f"SELECT company, value FROM {AM} WHERE metric = 'revenue' AND period_end_year = 2022 AND ticker IN ('CVS', 'WMT') ORDER BY 1")]),
    ("c22", [dict(q="Which of the companies had the highest revenue in fiscal year 2022?", cls="sql", sql=SQL_ONLY[0][2]),
             dict(q="Does Adobe have an improving operating margin profile as of FY2022?", depends=False,
                  standalone="Does Adobe have an improving operating margin profile as of FY2022?", fb="00438")]),
    ("c23", [dict(q="Has MGM Resorts paid dividends to common shareholders in FY2022?", fb="01254"),
             dict(q="Which region had the highest EBITDAR contribution for it during FY2022?", depends=True,
                  standalone="Which region had the Highest EBITDAR Contribution for MGM during FY2022?", fb="00382")]),
    ("c24", [dict(q="Does Corning have positive working capital based on FY2022 data?", fb="00005"),
             dict(q="And how much has its effective tax rate changed between FY2021 and FY2022?", depends=True,
                  standalone="How much has the effective tax rate of Corning changed between FY2021 and FY2022?", fb="01346")]),
]


def psql(sql: str) -> list[list[str]]:
    if "am" in sql.split():          # a reference query: read the fiscal-year view (AM_CTE)
        sql = (f"WITH {AM_CTE}, " + sql[len("WITH "):]) if sql.startswith("WITH ") else f"WITH {AM_CTE} " + sql
    res = subprocess.run(["docker", "exec", CONTAINER, "psql", "-U", "statesync_reader", "-d", "sec", "-At", "-F", "|", "-c", sql],
                         capture_output=True, text=True, timeout=60)  # noqa: literals - query timeout
    if res.returncode:
        raise RuntimeError(f"query failed: {res.stderr.strip()}\n{sql}")
    return [line.split("|") for line in res.stdout.strip().split("\n") if line]


def answer_text(rows: list[list[str]]) -> str:
    return "; ".join(", ".join(r) for r in rows)


def main() -> int:
    qs = {q["financebench_id"]: q for q in pd.questions()}
    ingest = json.loads((REPO / "docs" / "eval" / "financebench_ingest_paragraph.json").read_text())
    library = {d["doc_name"] for d in ingest["documents"] if d.get("status") == "ready"}
    library_upper = {d.upper() for d in library}
    coverage = {q["financebench_id"]: q for q in json.loads(COVERAGE.read_text())["questions"]}
    have = {(r[0], r[1], int(r[2])) for r in psql(f"SELECT ticker, metric, period_end_year FROM {AM} WHERE value IS NOT NULL")}
    companies = [r[0].lower() for r in psql("SELECT name FROM sec.companies")]
    ticker_company = {r[0]: r[1] for r in psql(f"SELECT DISTINCT ticker, company FROM {AM}")}
    items: list[dict] = []

    # 1. FinanceBench's 150.
    for fid, q in qs.items():
        if q["question_type"] == "metrics-generated":
            cov = coverage[fid]
            cls = "either" if cov["items"] and all(i["in_database"] for i in cov["items"]) else "docs"
            why = "sec_crosscheck_coverage: all items in database" if cls == "either" else "coverage: an item is not in the database"
        else:
            need = EITHER_FB.get(fid[-5:])
            if need:
                ticker, metrics, years = need
                missing = [f"{m}@{y}" for m in metrics for y in years if (ticker, m, y) not in have]
                if missing:
                    raise RuntimeError(f"{fid}: listed as either but missing {missing}")
                cls, why = "either", f"standard annual figures, all in the database: {', '.join(metrics)} for {years}"
            else:
                cls, why = "docs", f"{q['question_type']}: needs the filing (narrative, segment, quarter, 8-K/earnings, or a figure not in the database)"
        if q["doc_name"] not in library:
            raise RuntimeError(f"{fid}: filing {q['doc_name']} not in the library")
        items.append({"id": f"fb-{fid[-5:]}", "class": cls, "question": q["question"], "source": FB,
                      "financebench_id": fid, "financebench_type": q["question_type"], "filing": q["doc_name"],
                      "why": why, "gold_answer": q["answer"]})

    # 2. Database-only.
    for sid, question, sql in SQL_ONLY:
        rows = psql(sql)
        if not rows or all(not any(c for c in r) for r in rows):
            raise RuntimeError(f"{sid}: empty result")
        items.append({"id": sid, "class": "sql", "question": question, "source": "authored", "reference_sql": sql,
                      "gold_answer": answer_text(rows), "why": "across companies or more years than any library filing"})

    # 3. Hybrid, dependent: the winner must have its fiscal-2022 10-K in the library.
    for hid, question, tickers, (num, den, order) in HYBRID_DEP:
        tlist = ", ".join(repr(t) for t in tickers)
        if num == "revenue_growth":
            sql = (f"SELECT b.ticker, round(((b.value / a.value - 1) * 100)::numeric, 1) FROM {AM} a JOIN {AM} b ON b.company = a.company "
                   f"AND b.metric = a.metric AND b.period_end_year = 2022 WHERE a.metric = 'revenue' AND a.period_end_year = 2021 "
                   f"AND a.ticker IN ({tlist}) ORDER BY b.value / a.value {order} LIMIT 1")
        elif den:
            sql = (f"SELECT n.ticker, round((n.value / d.value * 100)::numeric, 1) FROM {AM} n JOIN {AM} d ON d.company = n.company "
                   f"AND d.period_end_year = n.period_end_year AND d.metric = '{den}' WHERE n.metric = '{num}' AND n.period_end_year = 2022 "
                   f"AND n.ticker IN ({tlist}) ORDER BY n.value / d.value {order} LIMIT 1")
        else:
            sql = (f"SELECT ticker, value FROM {AM} WHERE metric = '{num}' AND period_end_year = 2022 AND ticker IN ({tlist}) "
                   f"ORDER BY value {order} LIMIT 1")
        rows = psql(sql)
        if not rows:
            raise RuntimeError(f"{hid}: empty SQL half")
        winner = rows[0][0]
        filing = f"{PREFIX[winner]}_2022_10K"
        if filing.upper() not in library_upper:
            raise RuntimeError(f"{hid}: winner {winner} has no {filing} in the library")
        items.append({"id": hid, "class": "hybrid", "dependent": True, "question": question, "source": "authored",
                      "reference_sql": sql, "sql_answer": answer_text(rows), "winner": ticker_company.get(winner, winner),
                      "filing": filing, "why": "the documents half is about the company the database half finds"})

    # 4. Hybrid, independent.
    for hid, question, sql, filing in HYBRID_IND:
        rows = psql(sql)
        if not rows:
            raise RuntimeError(f"{hid}: empty SQL half")
        if filing.upper() not in library_upper:
            raise RuntimeError(f"{hid}: {filing} not in the library")
        items.append({"id": hid, "class": "hybrid", "dependent": False, "question": question, "source": "authored",
                      "reference_sql": sql, "sql_answer": answer_text(rows), "filing": filing,
                      "why": "a database fact plus what one library filing says"})

    # 5. Neither.
    for nid, question, company in NEITHER:
        if company:
            c = company.lower()
            if any(c in name for name in companies) or any(d.upper().startswith(company.upper()) for d in library):
                raise RuntimeError(f"{nid}: {company} is in a source")
        items.append({"id": nid, "class": "neither", "question": question, "source": "authored",
                      "why": f"{company} is in neither source" if company else "future, live, personal or not data analysis"})

    # 6. Conversations.
    conversations = []
    for cid, turns in CONVERSATIONS:
        out_turns = []
        winner = None
        for n, t in enumerate(turns, 1):
            rec = {"turn": n, "question": t["q"], "depends_on_history": bool(t.get("depends", False))}
            standalone = t.get("standalone", t["q"])
            if winner and "{X}" in standalone:
                standalone = standalone.replace("{X}", ticker_company.get(winner, winner))
            rec["standalone"] = standalone
            if "fb" in t:
                q = fbq(qs, t["fb"])
                base = next(i for i in items if i["id"] == f"fb-{t['fb']}")
                rec.update({"class": base["class"], "financebench_id": q["financebench_id"], "gold_answer": q["answer"]})
            else:
                rec["class"] = t["cls"]
                if "sql" in t:
                    rows = psql(t["sql"])
                    if not rows:
                        raise RuntimeError(f"{cid} turn {n}: empty result")
                    rec["reference_sql"] = t["sql"]
                    rec["gold_answer"] = answer_text(rows)
                    if rows[0] and rows[0][0] in PREFIX:
                        winner = rows[0][0]
                elif rec["class"] == "neither":
                    rec["gold_answer"] = "This workspace does not hold that."
                if t.get("needs_filing_of") and t["needs_filing_of"].upper() not in library_upper:
                    raise RuntimeError(f"{cid} turn {n}: {t['needs_filing_of']} not in the library")
                if t.get("needs_filing"):
                    filing = f"{PREFIX[winner]}_{t['needs_filing']}"
                    if filing.upper() not in library_upper:
                        raise RuntimeError(f"{cid} turn {n}: {filing} not in the library")
                    rec["filing"] = filing
                if t.get("filing"):
                    rec["filing"] = t["filing"]
            rec["acceptable"] = RULES["acceptable"][rec["class"]]
            out_turns.append(rec)
        conversations.append({"id": cid, "turns": out_turns})

    for it in items:
        it["acceptable"] = RULES["acceptable"][it["class"]]

    # 7. Split, stratified by class (single items) and by conversation.
    rng = random.Random(SEED)
    by_class: dict[str, list[dict]] = {}
    for it in items:
        by_class.setdefault(it["class"], []).append(it)
    for cls_items in by_class.values():
        order = sorted(cls_items, key=lambda i: i["id"])
        rng.shuffle(order)
        for k, it in enumerate(order):
            it["split"] = "dev" if k % 2 == 0 else "test"
    corder = sorted(conversations, key=lambda c: c["id"])
    rng.shuffle(corder)
    for k, c in enumerate(corder):
        c["split"] = "dev" if k % 2 == 0 else "test"

    record = {"version": 1, "created": "2026-10-05", "seed": SEED,
              "test_bed": {"database": "SEC demo database (demo/sec_db.sh), sec.annual_metrics as first reported",
                           "documents": "84 FinanceBench filings (financebench_ingest_paragraph.json)"},
              "rules": RULES, "data_defects": DATA_DEFECTS, "items": items, "conversations": conversations,
              "summary": {"items": len(items), "by_class": dict(Counter(i["class"] for i in items)),
                          "by_split": dict(Counter(f"{i['split']}:{i['class']}" for i in items)),
                          "conversations": len(conversations), "turns": sum(len(c["turns"]) for c in conversations),
                          "turns_by_class": dict(Counter(t["class"] for c in conversations for t in c["turns"])),
                          "dependent_turns": sum(t["depends_on_history"] for c in conversations for t in c["turns"])}}
    OUT.write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n")

    # 8. The owner's check: a random sample of single items and conversation turns, labels shown.
    pool = [("item", i) for i in items] + [("turn", (c, t)) for c in conversations for t in c["turns"]]
    pick = random.Random(SEED + 1).sample(pool, OWNER_SAMPLE)
    lines = ["# Routing set: owner's label check", "",
             f"{OWNER_SAMPLE} labels drawn at random (seed {SEED + 1}) from `docs/eval/routing_set.json`. For each,",
             "mark **agree** or **disagree** (and why). The classes and rules are at the end.", ""]
    for n, (kind, obj) in enumerate(pick, 1):
        if kind == "item":
            lines += [f"**{n}. `{obj['id']}`**: {obj['question']}",
                      f"- label: **{obj['class']}** (acceptable routes: {', '.join(obj['acceptable'])})",
                      f"- why: {obj['why']}", "- owner: agree / disagree:", ""]
        else:
            c, t = obj
            hist = " → ".join(f"T{p['turn']}: {p['question']}" for p in c["turns"] if p["turn"] < t["turn"])
            lines += [f"**{n}. `{c['id']}` turn {t['turn']}**: {t['question']}",
                      f"- earlier turns: {hist or '(none)'}",
                      f"- depends on history: **{t['depends_on_history']}**; standalone: {t['standalone']}",
                      f"- label: **{t['class']}** (acceptable routes: {', '.join(t['acceptable'])})", "- owner: agree / disagree:", ""]
    lines += ["## Classes", ""] + [f"- **{k}**: {v}" for k, v in RULES["classes"].items()]
    SHEET.write_text("\n".join(lines) + "\n")
    print(json.dumps(record["summary"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
