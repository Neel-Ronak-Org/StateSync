"""
tests/test_workspace_logic.py
Pins the pure logic behind workspaces (docs/V3_PLAN.md Phase 3,
mcp/tools/workspace_tools.py) and the refusals that happen before any
database access.

WHY THIS EXISTS.
- normalize_question decides the fast path: an exact match runs approved SQL
  without asking a model, so "What's revenue?" and "whats revenue" must match
  and "revenue by state" must not match "revenue".
- similarity picks the (at most three) examples a question is shown; stopwords
  must not make unrelated questions look alike.
- mentioned_terms decides which business definitions reach the prompt; "on
  time" must not fire on "on-timeline" style partial words.
- sample_values is what the drafter sees of a column: distinct, bounded.
- The early refusals (unknown action, missing workspace, unknown dataset,
  blank name, bad setup questions) must never reach the database.
The database paths are verified live by scripts/verify_workspaces.py.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MCP_DIR = REPO_ROOT / "mcp"
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))
sys.path.insert(0, str(MCP_DIR))

if "psycopg_pool" not in sys.modules:
    try:
        import psycopg_pool  # noqa: F401
    except ImportError:
        import unittest.mock
        sys.modules["psycopg_pool"] = unittest.mock.MagicMock()

pytest.importorskip("pglast")
pytest.importorskip("psycopg")
import tools.workspace_tools as wt  # noqa: E402
from session import set_session  # noqa: E402


def _manage(action: str, **params):
    async def run():
        set_session(tenant_id="t-test")
        return await wt.manage(action, **params)
    return asyncio.run(run())


# ── Pure helpers ─────────────────────────────────────────────────────────────

def test_normalize_question_ignores_case_and_punctuation():
    assert wt.normalize_question("What's the  Revenue?") == wt.normalize_question("whats the revenue")
    assert wt.normalize_question("Revenue by state") != wt.normalize_question("Revenue")


def test_similarity_ignores_stopwords_and_is_bounded():
    assert wt.similarity("What is the total revenue?", "Show me all of the orders") == 0.0
    assert wt.similarity("revenue by state", "revenue by state") == 1.0
    mid = wt.similarity("monthly revenue by state", "revenue by category")
    assert 0.0 < mid < 1.0
    assert wt.similarity("", "revenue") == 0.0


def test_mentioned_terms_match_whole_words_only():
    terms = ["On time", "revenue", "GMV"]
    assert wt.mentioned_terms("What share of orders arrived on time?", terms) == ["On time"]
    assert wt.mentioned_terms("Monthly gmv trend", terms) == ["GMV"]
    assert wt.mentioned_terms("revenues by ontime flag", terms) == []


def test_sample_values_distinct_bounded_text_only():
    rows = [{"c": "a"}, {"c": "a"}, {"c": None}, {"c": "  "}, {"c": 3}, {"c": "b"}, {"c": "x" * 500}, {"c": "d"}]
    got = wt.sample_values(rows, "c", 3)
    assert got[:2] == ["a", "b"] and len(got) == 3
    assert all(len(v) <= wt._SAMPLE_VALUE_MAX_CHARS for v in got)


def test_clean_text():
    assert wt.clean_text("  hi  ", 10) == "hi"
    assert wt.clean_text("   ", 10) is None
    assert wt.clean_text("   ", 10, allow_empty=True) == ""
    assert wt.clean_text(5, 10) is None
    assert wt.clean_text("abcdef", 3) == "abc"


# ── Refusals before the database ─────────────────────────────────────────────

def test_unknown_action_and_missing_ids_are_refused():
    assert _manage("drop", principal_id="p")["error"] == "invalid"
    assert _manage("get", principal_id="p")["error"] == "invalid"
    assert _manage("list", principal_id="")["error"] == "invalid"


def test_remove_example_needs_a_workspace_and_a_question():
    assert _manage("remove_example", principal_id="p", question="q")["error"] == "invalid"
    assert _manage("remove_example", principal_id="p", workspace_id="w", question=" ")["error"] == "invalid"


def test_create_refuses_unknown_dataset_and_blank_name():
    assert _manage("create", principal_id="p", name="Shop", target_schema="pg_catalog")["error"] == "invalid"
    assert _manage("create", principal_id="p", name="  ", target_schema="olist")["error"] == "invalid"


def test_update_needs_something_to_change():
    assert _manage("update", principal_id="p", workspace_id="w")["error"] == "invalid"
    assert _manage("update", principal_id="p", workspace_id="w", name=" ")["error"] == "invalid"


def test_examples_must_pass_the_sql_gate():
    bad = _manage("add_example", principal_id="p", workspace_id="w", question="q", sql="DELETE FROM olist.orders")
    assert bad["error"] == "invalid" and "SQL gate" in bad["message"]
    assert _manage("add_example", principal_id="p", workspace_id="w", question=" ", sql="SELECT 1")["error"] == "invalid"
    assert _manage("add_example", principal_id="p", workspace_id="w", question="q", sql="SELECT 1",
                   source="model")["error"] == "invalid"


def test_setup_runs_are_validated_before_anything_is_written():
    ok_q = {"question": "How many orders?", "reference_sql": "SELECT count(*) FROM olist.orders"}
    assert _manage("start_run", principal_id="p", workspace_id="w", graded_by="model", questions=[ok_q])["error"] == "invalid"
    assert _manage("start_run", principal_id="p", workspace_id="w", graded_by="reference", questions=[])["error"] == "invalid"
    too_many = [ok_q] * (int(wt.cfg.workspaces.setup_questions) + 1)
    assert _manage("start_run", principal_id="p", workspace_id="w", graded_by="reference", questions=too_many)["error"] == "invalid"
    unsafe = {"question": "q", "reference_sql": "DROP TABLE olist.orders"}
    assert _manage("start_run", principal_id="p", workspace_id="w", graded_by="reference", questions=[unsafe])["error"] == "invalid"
    # Reference-graded needs reference SQL; owner-graded must not carry any.
    assert _manage("start_run", principal_id="p", workspace_id="w", graded_by="reference",
                   questions=[{"question": "q"}])["error"] == "invalid"
    assert _manage("start_run", principal_id="p", workspace_id="w", graded_by="owner", questions=[ok_q])["error"] == "invalid"


def test_verdicts_are_restricted():
    assert _manage("record_result", principal_id="p", run_id="r", position=1, verdict="maybe")["error"] == "invalid"
    assert _manage("set_verdict", principal_id="p", run_id="r", position=1, verdict="pending")["error"] == "invalid"


def test_result_signature_is_the_shape_not_the_values():
    assert wt.result_signature(["a", "b", "c"], 5) == "cols=a,b,c;rows=yes"
    assert wt.result_signature(["a", "b", "c"], 1) == wt.result_signature(["a", "b", "c"], 900)
    assert wt.result_signature(["a"], 0) == "cols=a;rows=no"
    # Column order and names are part of the shape.
    assert wt.result_signature(["b", "a"], 1) != wt.result_signature(["a", "b"], 1)
    assert wt.result_signature([], 0) == "cols=;rows=no"


def test_example_checks_need_a_workspace():
    assert _manage("check_examples", principal_id="p")["error"] == "invalid"
    assert _manage("examples", principal_id="p")["error"] == "invalid"
