"""
tests/test_workspace_setup.py
The workspace setup flow (docs/V3_PLAN.md Phase 3, slice 2,
orchestrator/workspace_setup.py): AI drafts and the setup test.

WHY THIS EXISTS.
- parse_drafts is the only door from a model reply into the knowledge store.
  A column the catalog does not have must never get in, whatever the model
  says, and every draft must be marked "ai" (unapproved, replaceable).
- The setup test's score is the Phase 3 exit measure, so how it is computed is
  pinned here: the questions are the first ten English SQL questions of the
  curated set (no cherry-picking), grading is execution accuracy against the
  reference SQL, an answer with no SQL is wrong, a broken reference is an
  error (not a free point), and a quota failure stops the run instead of
  scoring the rest as wrong.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))

import workspace_setup as ws  # noqa: E402

CATALOG = [
    {"table_name": "orders", "column_name": "", "data_type": "table", "row_estimate": 1000},
    {"table_name": "orders", "column_name": "order_id", "data_type": "text", "sample_values": ["a1"]},
    {"table_name": "orders", "column_name": "status", "data_type": "text", "sample_values": ["delivered", "canceled"]},
    {"table_name": "sellers", "column_name": "", "data_type": "table", "row_estimate": 50},
    {"table_name": "sellers", "column_name": "city", "data_type": "text", "description": "Where the seller is."},
]


# ── Drafts ───────────────────────────────────────────────────────────────────

def test_parse_drafts_keeps_only_catalog_names_and_marks_them_ai():
    reply = json.dumps({"tables": [
        {"table": "orders", "description": "One row per order.",
         "columns": {"status": "Where the order is.", "invented_col": "Made up.", "order_id": ""}},
        {"table": "ghost_table", "description": "Not real.", "columns": {"x": "y"}},
        {"table": "sellers", "description": "Sellers."},
    ], "definitions": [{"term": "Delivered", "definition": "status = 'delivered'"}, {"term": "", "definition": "x"}]})
    parsed = ws.parse_drafts(reply, CATALOG, ["orders"], 400, 6)
    assert not isinstance(parsed, str)
    descriptions, definitions = parsed
    assert {(d["table_name"], d["column_name"]) for d in descriptions} == {("orders", ""), ("orders", "status")}
    assert all(d["source"] == "ai" for d in descriptions + definitions)
    assert definitions == [{"term": "Delivered", "definition": "status = 'delivered'", "source": "ai"}]


def test_parse_drafts_accepts_fences_and_refuses_junk():
    good = "```json\n" + json.dumps({"tables": [{"table": "orders", "description": "Orders."}]}) + "\n```"
    assert not isinstance(ws.parse_drafts(good, CATALOG, ["orders"], 400, 6), str)
    assert ws.parse_drafts("Sure! Here you go", CATALOG, ["orders"], 400, 6) == "The reply was not JSON."
    assert isinstance(ws.parse_drafts(json.dumps({"tables": [{"table": "nope", "description": "x"}]}),
                                      CATALOG, ["orders"], 400, 6), str)


def test_parse_drafts_caps_lengths_and_definitions():
    reply = json.dumps({"tables": [{"table": "orders", "description": "x" * 900}],
                        "definitions": [{"term": f"t{i}", "definition": "d"} for i in range(20)]})
    descriptions, definitions = ws.parse_drafts(reply, CATALOG, ["orders"], 400, 6)
    assert len(descriptions[0]["description"]) == 400 and len(definitions) == 6


def test_catalog_batches_and_summary():
    batches = ws.catalog_batches(CATALOG, 10_000)
    assert batches == [["orders", "sellers"]]
    assert ws.catalog_batches(CATALOG, 10) == [["orders"], ["sellers"]]
    summary = ws.summarize_tables(CATALOG, ["orders", "sellers"], 10_000)
    assert "status (text) e.g. 'delivered', 'canceled'" in summary
    assert "city (text) -- Where the seller is." in summary


# ── Setup questions ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("schema", ["olist", "public"])
def test_every_sample_dataset_has_ten_reference_questions(schema):
    chosen = ws.load_reference_set(schema, REPO_ROOT / "fixtures", 10)
    assert chosen is not None and len(chosen) == 10
    assert all(q["reference_sql"].lstrip().upper().startswith(("SELECT", "WITH")) for q in chosen)
    assert len({q["question"] for q in chosen}) == 10


def test_reference_questions_take_the_first_eligible_in_order():
    queries = [{"text": "a", "ground_truth_sql": "SELECT 1"},
               {"text": "b", "ground_truth_sql": "SELECT 2", "lang": "hi"},
               {"text": "c", "expected_route": "docs", "ground_truth_sql": "SELECT 3"},
               {"text": "d"},
               {"text": "e", "ground_truth_sql": "SELECT 5"}]
    assert [q["question"] for q in ws.reference_questions(queries, 5)] == ["a", "e"]


def test_a_dataset_without_a_reference_set_is_owner_graded():
    assert ws.load_reference_set("live", REPO_ROOT / "fixtures", 10) is None


def test_parse_questions():
    reply = json.dumps({"questions": ["How many orders?", "how many orders?", "Top sellers?", " "]})
    assert ws.parse_questions(reply, 2, 300) == [{"question": "How many orders?"}, {"question": "Top sellers?"}]
    assert isinstance(ws.parse_questions(reply, 3, 300), str)
    assert isinstance(ws.parse_questions("nope", 1, 300), str)


def test_grade_is_execution_accuracy():
    assert ws.grade([{"n": 5}], [{"count": 5}])[0] == "right"            # names do not matter
    assert ws.grade([{"a": 1}, {"a": 2}], [{"b": 2}, {"b": 1}])[0] == "right"  # order does not
    verdict, detail = ws.grade([{"a": 1}], [{"b": 1}, {"b": 2}])
    assert verdict == "wrong" and "50%" in detail


# ── Runners ──────────────────────────────────────────────────────────────────

class FakeManage:
    def __init__(self, catalog=None):
        self.calls: list[tuple[str, dict]] = []
        self.catalog = catalog if catalog is not None else CATALOG

    async def __call__(self, action, **params):
        self.calls.append((action, params))
        if action == "catalog":
            return {"ok": True, "columns": self.catalog}
        if action == "start_run":
            return {"ok": True, "run": {"run_id": "run-123456789"}}
        return {"ok": True}

    def results(self):
        final = {}
        for action, p in self.calls:
            if action == "record_result" and p["verdict"] != "running":
                final[p["position"]] = (p["verdict"], p.get("detail"))
        return final


class HTTPError(Exception):
    def __init__(self, detail):
        super().__init__(detail)
        self.detail = detail


def test_run_test_grades_each_answer_and_stops_on_quota():
    questions = [{"question": f"q{i}", "reference_sql": f"SELECT {i}"} for i in range(1, 6)]
    answers = {
        "q1": {"state_trace": {"executed_sql": "SELECT 1", "sql_rows": [{"x": 1}]}},
        "q2": {"state_trace": {"executed_sql": "SELECT 9", "sql_rows": [{"x": 9}]}},
        "q3": {"state_trace": {"executed_sql": None, "sql_rows": []}},
    }

    async def ask(question, session_id):
        assert session_id.startswith("setup-run-1234")
        if question == "q4":
            raise HTTPError("The language model is rate-limited right now. Please try again in a minute.")
        return answers[question]

    async def run_select(sql):
        return {"ok": True, "rows": [{"v": int(sql.split()[-1])}]}

    manage = FakeManage()
    job = ws.new_job("test", "p", "w")
    asyncio.run(ws.run_test(job, manage, questions, "reference", ask, run_select))
    got = manage.results()
    assert got[1][0] == "right" and got[2][0] == "wrong" and got[3] == ("wrong", "No database query was run for it.")
    assert got[4][0] == "error" and got[5][0] == "error" and "quota" in got[5][1]
    assert ("finish_run", {"run_id": "run-123456789", "failed": True}) in manage.calls
    assert job.state == "failed" and job.done == 5


def test_a_broken_reference_is_an_error_not_a_point():
    async def ask(question, session_id):
        return {"state_trace": {"executed_sql": "SELECT 1", "sql_rows": []}}

    async def run_select(sql):
        return {"ok": False, "rows": []}

    manage = FakeManage()
    job = ws.new_job("test", "p", "w")
    asyncio.run(ws.run_test(job, manage, [{"question": "q", "reference_sql": "SELECT 1"}], "reference", ask, run_select))
    assert manage.results()[1][0] == "error" and job.state == "done"


def test_owner_graded_answers_wait_for_the_owner():
    async def ask(question, session_id):
        return {"answer_text": "5", "state_trace": {"executed_sql": "SELECT 5", "sql_rows": [{"n": 5}]}}

    async def run_select(sql):
        raise AssertionError("owner-graded runs have no reference to run")

    manage = FakeManage()
    asyncio.run(ws.run_test(ws.new_job("test", "p", "w"), manage, [{"question": "q"}], "owner", ask, run_select))
    assert manage.results()[1] == ("pending", None)


def test_run_draft_scans_first_and_saves_ai_drafts():
    catalogs = [[], CATALOG]

    class ScanningManage(FakeManage):
        async def __call__(self, action, **params):
            if action == "catalog":
                self.calls.append((action, params))
                return {"ok": True, "columns": catalogs.pop(0)}
            return await super().__call__(action, **params)

    async def complete(system, user, max_tokens):
        assert "status (text)" in user and "We sell things." in user
        return json.dumps({"tables": [{"table": "orders", "description": "Orders.", "columns": {"status": "State."}},
                                      {"table": "sellers", "description": "Sellers."}],
                           "definitions": [{"term": "Late", "definition": "after the estimate"}]})

    manage = ScanningManage()
    job = ws.new_job("draft", "p", "w")
    asyncio.run(ws.run_draft(job, manage, complete, "We sell things."))
    actions = [a for a, _ in manage.calls]
    assert actions[:3] == ["catalog", "scan", "catalog"]
    saved = next(p for a, p in manage.calls if a == "save_descriptions")["items"]
    assert len(saved) == 3 and all(i["source"] == "ai" for i in saved)
    assert job.state == "done" and job.done == job.total == 1


def test_run_draft_fails_cleanly_when_every_reply_is_junk():
    async def complete(system, user, max_tokens):
        return "I cannot help with that."

    manage = FakeManage()
    job = ws.new_job("draft", "p", "w")
    asyncio.run(ws.run_draft(job, manage, complete, ""))
    assert job.state == "failed" and "could not be read" in job.error
    assert not any(a == "save_descriptions" for a, _ in manage.calls)
