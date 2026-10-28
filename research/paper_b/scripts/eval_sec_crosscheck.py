#!/usr/bin/env python3
"""
scripts/eval_sec_crosscheck.py
The database-against-documents study (docs/specs/sec_crosscheck.md).

    python3 scripts/eval_sec_crosscheck.py coverage            Phase 0, no model calls (§3)
    python3 scripts/eval_sec_crosscheck.py setup               the two workspaces (§2; drafting calls the model)
    python3 scripts/eval_sec_crosscheck.py ask --arm sql|docs  the 50 questions, verbatim (§2, §6)
    python3 scripts/eval_sec_crosscheck.py score               §4 and §5, no model calls

`coverage` records, for each of the 50 FinanceBench metrics-generated questions, the line items
it needs, the fiscal years, and whether `sec.annual_metrics` (values as first reported) holds
each one. The NEEDS table below was written from the question texts on 2026-10-02, before any
model was called; it names StateSync's metric names (sec.metric_tags), never the gold answers.
A question whose items are missing stays in every denominator, tagged not_in_database.

Fiscal years are resolved from the filing itself: the question's 10-K is the company's 10-K
whose DEI fiscal year (facts.filing_fy) is the document year; its latest one-year period ends
that fiscal year, and earlier fiscal years end one year apart before it.

`setup` and `ask` call the model (Groq); run them only when quota is planned (§6). `ask` is
resumable and stops after MAX_CONSECUTIVE_ERRORS failures in a row (§7). The dev token comes from
api_auth.api_token() and is never printed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
QUESTIONS = REPO / "data" / "external" / "financebench" / "data" / "financebench_open_source.jsonl"
OUT = REPO / "docs" / "eval" / "sec_crosscheck_coverage.json"
CONTAINER = "statesync-sec-db"
YEAR_TOLERANCE_DAYS = 20  # noqa: literals - fiscal years ending on a weekday drift by a few days
API = "http://localhost:8090"
SETUP = REPO / "docs" / "eval" / "sec_crosscheck_setup.json"
INGEST_RECORD = REPO / "docs" / "eval" / "financebench_ingest_paragraph.json"
SQL_WORKSPACE, DOCS_WORKSPACE = "eval-sec-sql", "eval-sec-docs"
SEC_CONNECTION = {"name": "eval-sec", "host": "host.docker.internal", "port": 5435, "dbname": "sec",
                  "username": "statesync_reader", "password": "sec-reader-pass"}  # demo-only, demo/sec_db.sh
# Verbatim from docs/specs/sec_crosscheck.md §2.
SQL_BUSINESS_CONTEXT = (
    "Annual figures reported by US public companies to the SEC (XBRL company facts). Use the view "
    "sec.annual_metrics, which holds each figure as first reported in the company's 10-K; sec.facts holds "
    "every reported value including later restatements. Amounts are in the unit given in the unit column "
    "(usually USD). A fiscal year is identified by period_end; some companies' fiscal years end early in "
    "the next calendar year.")
MAX_CONSECUTIVE_ERRORS = 5     # noqa: literals - §7 stop rule
REL_TOLERANCE = 0.01           # noqa: literals - §4: relative error at most 1%
QUERY_TIMEOUT_S = 600          # noqa: literals - one question may wait out Groq rate limits
DRAFT_POLL_S = 5               # noqa: literals - poll interval for the drafting job

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


# ── the API (setup, ask) ──────────────────────────────────────────────────────

def api(method: str, path: str, body: dict | None = None, timeout: float = 120) -> tuple[int, dict]:
    sys.path.insert(0, str(REPO / "scripts"))
    from api_auth import api_token
    req = urllib.request.Request(API + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {api_token()}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except json.JSONDecodeError:
            return exc.code, {}


def _stop(message: str) -> int:
    print(message)
    return 1


def _workspace(name: str) -> dict | None:
    _, listed = api("GET", "/workspaces")
    return next((w for w in listed.get("workspaces", []) if w["name"] == name), None)


def setup() -> int:
    """§2: the SQL workspace on the connected SEC database (drafted by the product, approved
    unchanged) and the documents workspace on Part 1's folder. Idempotent by name."""
    record = json.loads(SETUP.read_text()) if SETUP.exists() else {}
    sql_ws = _workspace(SQL_WORKSPACE)
    if sql_ws is None:
        st, conn = api("POST", "/library/connections", SEC_CONNECTION)
        if st != 201:
            return _stop(f"connection refused: HTTP {st} {conn.get('detail')}")
        cid = conn["connection"]["connection_id"]
        st, body = api("PUT", f"/library/connections/{cid}/schema", {"schema_name": "sec"})
        if st != 200:
            return _stop(f"schema refused: HTTP {st} {body.get('detail')}")
        st, body = api("POST", "/workspaces", {"name": SQL_WORKSPACE, "target_schema": cid,
                                               "business_context": SQL_BUSINESS_CONTEXT})
        if st != 201:
            return _stop(f"workspace refused: HTTP {st} {body.get('detail')}")
        sql_ws = body["workspace"]
        record["connection_id"] = cid
    record["sql_workspace_id"] = sql_ws["workspace_id"]
    if not record.get("draft_approved"):
        st, body = api("POST", f"/workspaces/{sql_ws['workspace_id']}/draft")
        if st != 202:
            return _stop(f"draft refused: HTTP {st} {body.get('detail')}")
        while True:
            time.sleep(DRAFT_POLL_S)
            _, status = api("GET", f"/workspaces/{sql_ws['workspace_id']}/draft")
            job = status.get("job") or {}
            if job.get("state") not in ("queued", "running"):
                break
        if job.get("state") != "done":
            return _stop(f"draft ended {job.get('state')}: {job.get('error')}")
        st, body = api("POST", f"/workspaces/{sql_ws['workspace_id']}/approve")   # unchanged, as the spec says
        if st != 200:
            return _stop(f"approve refused: HTTP {st} {body.get('detail')}")
        record["draft_approved"] = {"columns": body.get("approved_columns"), "definitions": body.get("approved_definitions")}
    docs_ws = _workspace(DOCS_WORKSPACE)
    if docs_ws is None:
        st, body = api("POST", "/workspaces", {"name": DOCS_WORKSPACE, "target_schema": "public"})
        if st != 201:
            return _stop(f"docs workspace refused: HTTP {st} {body.get('detail')}")
        docs_ws = body["workspace"]
    folder = json.loads(INGEST_RECORD.read_text())["folder_id"]
    st, body = api("PUT", f"/workspaces/{docs_ws['workspace_id']}/folders", {"folder_ids": [folder]})
    if st != 200:
        return _stop(f"folder link refused: HTTP {st} {body.get('detail')}")
    record.update({"docs_workspace_id": docs_ws["workspace_id"], "folder_id": folder,
                   "set_up_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    SETUP.write_text(json.dumps(record, indent=1) + "\n")
    print(json.dumps(record, indent=1))
    return 0


def answers_path(arm: str) -> Path:
    return REPO / "docs" / "eval" / f"sec_crosscheck_answers_{arm}.json"


def ask(arm: str, spacing_s: float) -> int:
    """§2: every question verbatim, one new session each, in the arm's workspace. Resumable."""
    setup_record = json.loads(SETUP.read_text())
    workspace_id = setup_record["sql_workspace_id" if arm == "sql" else "docs_workspace_id"]
    out = answers_path(arm)
    done = json.loads(out.read_text()) if out.exists() else {"arm": arm, "workspace_id": workspace_id, "answers": {}}
    errors = 0
    for i, q in enumerate(questions()):
        if q["financebench_id"] in done["answers"]:
            continue
        t0 = time.monotonic()
        st, body = api("POST", "/query", {"query": q["question"], "session_id": str(uuid.uuid4()),
                                          "workspace_id": workspace_id}, timeout=QUERY_TIMEOUT_S)
        trace = body.get("state_trace") or {}
        record = {"index": i, "http_status": st, "answer_text": body.get("answer_text"), "detail": body.get("detail"),
                  "route": trace.get("route"), "executed_sql": trace.get("executed_sql"), "evidence": trace.get("evidence"),
                  "number_grounding": trace.get("number_grounding"), "citations": body.get("citations"),
                  "latency_ms": body.get("latency_ms") or round((time.monotonic() - t0) * 1000),
                  "metrics": trace.get("metrics"), "calls": trace.get("calls"),
                  "asked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        if st == 200:
            done["answers"][q["financebench_id"]] = record
            errors = 0
        else:
            errors += 1
            print(f"[{i}] HTTP {st}: {body.get('detail')}")
        out.write_text(json.dumps(done, indent=1) + "\n")
        print(f"[{i + 1}/50] {q['financebench_id']} {arm}: HTTP {st} route={record['route']}")
        if errors >= MAX_CONSECUTIVE_ERRORS:
            return _stop(f"stopping: {errors} errors in a row (§7)")
        time.sleep(spacing_s)
    print(f"{len(done['answers'])} of 50 answered for the {arm} arm")
    return 0


# ── scoring (§4, §5; pure, unit-tested in tests/test_eval_sec_crosscheck.py) ──────

_NUMBER = re.compile(r"(?<![\w.])(?P<neg>-|\()?(?P<dollar>\$)?\s?(?P<whole>\d{1,3}(?:,\d{3})+|\d+)(?P<frac>\.\d+)?\)?"
                     r"(?P<glued>[A-Za-z]+\b)?\s*(?P<unit>%|percent\b|trillion\b|billion\b|million\b|thousand\b|bn\b|mn\b)?",
                     re.I)
_SCALE = {"thousand": 1e3, "k": 1e3, "million": 1e6, "mn": 1e6, "m": 1e6, "billion": 1e9, "bn": 1e9, "b": 1e9,
          "trillion": 1e12}
_YEAR = re.compile(r"(19[9]\d|20[0-3]\d)")


def question_unit(question: str) -> str:
    """'millions', 'billions', 'percent' or 'plain', from the question's own instruction."""
    q = question.lower()
    if re.search(r"usd\s+billions|in\s+billions", q):
        return "billions"
    if re.search(r"usd\s+millions|in\s+millions", q):
        return "millions"
    if "percent" in q or "%" in q:
        return "percent"
    return "plain"


def numbers(text: str) -> list[tuple[float, str | None]]:
    """(value, unit word) for every figure in the text, in order; a leading '-' or '(...)' is
    negative. Not figures: digits glued to letters ("3M", "2U", "10X" are names), except a
    single-letter scale right after a dollar amount ("$1.6B"); and a bare year (1990-2039 with
    no $, decimals or unit), which is a date."""
    out = []
    for m in _NUMBER.finditer(text or ""):
        whole, frac, glued = m.group("whole"), m.group("frac") or "", m.group("glued")
        unit = m.group("unit")
        if glued:
            if m.group("dollar") and glued.lower() in ("b", "m", "k", "bn", "mn"):
                unit = glued
            else:
                continue
        if not m.group("dollar") and not frac and not unit and "," not in whole and _YEAR.fullmatch(whole):
            continue
        value = float(whole.replace(",", "") + frac)
        out.append((-value if m.group("neg") else value, unit.lower() if unit else None))
    return out


def in_unit(value: float, unit: str | None, target: str) -> float:
    """A number with its unit word expressed in the question's unit (§4)."""
    if unit in (None, "%", "percent") or target in ("percent", "plain"):
        return value
    absolute = value * _SCALE.get(unit, 1.0)
    return absolute / (1e9 if target == "billions" else 1e6)


def extract(answer: str, target: str) -> tuple[float | None, bool]:
    """(the first number in the answer in the question's unit, ambiguous?). Ambiguous when the
    first sentence holds more than one number: listed for blind adjudication (§4)."""
    found = numbers(answer)
    if not found:
        return None, False
    first_sentence = re.split(r"(?<=[.!?])\s", (answer or "").strip(), maxsplit=1)[0]
    value, unit = found[0]
    return in_unit(value, unit, target), len(numbers(first_sentence)) > 1


def decimals(text: str) -> int:
    m = re.search(r"\d+(?:\.(\d+))?", str(text).replace(",", ""))
    return len(m.group(1)) if m and m.group(1) else 0


def matches(value: float | None, gold: float, gold_decimals: int) -> bool:
    """§4: relative error at most 1%, or equal after rounding to the gold's decimals."""
    if value is None:
        return False
    if round(value, gold_decimals) == round(gold, gold_decimals):
        return True
    return gold != 0 and abs(value - gold) / abs(gold) <= REL_TOLERANCE


def wilson(k: int, n: int, z: float = 1.959963984540054) -> tuple[float, float] | None:  # noqa: literals - 95% normal quantile
    if n == 0:
        return None
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return round(centre - half, 4), round(centre + half, 4)


def fisher_two_sided(a: int, b: int, c: int, d: int) -> float:
    """Fisher's exact test for [[a, b], [c, d]]: the summed probability of tables no more likely."""
    n, row1, col1 = a + b + c + d, a + b, a + c
    def prob(x: int) -> float:
        return math.comb(col1, x) * math.comb(n - col1, row1 - x) / math.comb(n, row1)
    observed = prob(a)
    lo, hi = max(0, row1 + col1 - n), min(row1, col1)
    return min(1.0, sum(prob(x) for x in range(lo, hi + 1) if prob(x) <= observed * (1 + 1e-9)))  # noqa: literals - float tie tolerance


def score() -> int:
    qs = questions()
    arms = {arm: json.loads(answers_path(arm).read_text())["answers"] for arm in ("sql", "docs")}
    rows = []
    for q in qs:
        unit = question_unit(q["question"])
        gold_value = numbers(str(q["answer"]))
        gold = in_unit(gold_value[0][0], gold_value[0][1], unit) if gold_value else None
        row = {"financebench_id": q["financebench_id"], "unit": unit, "gold_raw": q["answer"], "gold": gold}
        for arm, answers in arms.items():
            text = (answers.get(q["financebench_id"]) or {}).get("answer_text") or ""
            value, ambiguous = extract(text, unit)
            row[arm] = {"value": value, "ambiguous": ambiguous,
                        "correct": gold is not None and matches(value, gold, decimals(q["answer"]))}
        both = row["sql"]["value"] is not None and row["docs"]["value"] is not None
        row["agree"] = both and matches(row["sql"]["value"], row["docs"]["value"], decimals(q["answer"]))
        row["both_answered"] = both
        rows.append(row)
    n = len(rows)
    both = [r for r in rows if r["both_answered"]]
    agree = [r for r in both if r["agree"]]
    disagree = [r for r in both if not r["agree"]]
    def acc(sub, arm):
        k = sum(r[arm]["correct"] for r in sub)
        return {"k": k, "n": len(sub), "rate": round(k / len(sub), 4) if sub else None, "wilson95": wilson(k, len(sub))}
    a, b = sum(r["sql"]["correct"] for r in agree), sum(not r["sql"]["correct"] for r in agree)
    c, d = sum(r["sql"]["correct"] for r in disagree), sum(not r["sql"]["correct"] for r in disagree)
    def warning(arm):
        wrong = [r for r in both if not r[arm]["correct"]]
        flagged = disagree
        tp = sum(1 for r in flagged if not r[arm]["correct"])
        return {"precision": round(tp / len(flagged), 4) if flagged else None,
                "recall": round(tp / len(wrong), 4) if wrong else None, "flagged": len(flagged), "wrong": len(wrong)}
    summary = {
        "questions": n,
        "H3_accuracy": {"sql": acc(rows, "sql"), "docs": acc(rows, "docs")},
        "both_answered": len(both), "agree": len(agree),
        "H1_sql_correct_given_agree": acc(agree, "sql"), "H1_sql_correct_given_disagree": acc(disagree, "sql"),
        "H1_fisher_two_sided_p": round(fisher_two_sided(a, b, c, d), 4) if both else None,
        "H2_disagreement_as_warning": {"for_wrong_sql": warning("sql"), "for_wrong_docs": warning("docs")},
        "H3_answer_only_on_agreement": {"coverage": round(len(agree) / n, 4), "accuracy": acc(agree, "sql")},
        "ambiguous_for_adjudication": [r["financebench_id"] for r in rows if r["sql"]["ambiguous"] or r["docs"]["ambiguous"]],
    }
    out = REPO / "docs" / "eval" / "sec_crosscheck_scores.json"
    out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=1) + "\n")
    print(json.dumps(summary, indent=1))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("coverage", help="Phase 0: line-item coverage, no model calls")
    sub.add_parser("setup", help="the two workspaces (drafting calls the model)")
    p_ask = sub.add_parser("ask", help="ask the 50 questions in one arm (model calls)")
    p_ask.add_argument("--arm", choices=("sql", "docs"), required=True)
    p_ask.add_argument("--spacing", type=float, default=45.0, help="seconds between questions (Groq per-minute limits)")
    sub.add_parser("score", help="score both arms, no model calls")
    args = ap.parse_args()
    if args.cmd == "coverage":
        return coverage()
    if args.cmd == "setup":
        return setup()
    if args.cmd == "ask":
        return ask(args.arm, args.spacing)
    return score()


if __name__ == "__main__":
    sys.exit(main())
