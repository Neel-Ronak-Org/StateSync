"""
tests/test_sql_agent.py
The SQL agent (orchestrator/nodes/sql_agent.py): one attempt, as the graph runs it.

Pinned here:
- the fast path: an exact approved example runs as is, with no model call, labelled verified;
- a first attempt drafts, samples the tables the draft reads, then writes the final query with the samples;
- a retry gets every earlier failure with its hint, and a corrected answer carries the retry caution;
- a first empty result is a retriable error; a second one in a row is the answer;
- a refused query is classified (never by the model) and is not reported as executed;
- a query that reads no table is no_data_source (the signal for route recovery).
The MCP and the model are fakes.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))

pytest.importorskip("pglast")
from nodes import sql_agent as sa  # noqa: E402

RUN = asyncio.run
DATASET = "cn_0123456789abcdef"
FACTS = {"table": "facts", "columns": [{"name": "company", "type": "text", "nullable": True},
                                       {"name": "value", "type": "numeric", "nullable": False}], "foreign_keys": []}


class _Session:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class World:
    def __init__(self, monkeypatch, replies, results, knowledge=None):
        self.prompts: list[tuple[str, str]] = []
        self.ran: list[str] = []
        self.sampled: list[str] = []
        replies, results = list(replies), list(results)

        class _LLM:
            async def complete(inner, system, messages, max_tokens):
                self.prompts.append((system, messages[0]["content"]))
                return SimpleNamespace(text=replies.pop(0), tokens_in=100, tokens_out=20)

        async def list_tables(session, schema):
            return {"tables": [{"name": "facts"}]}

        async def describe(session, names, schema):
            return "TABLE cn_0123456789abcdef.facts: ...", [FACTS]

        async def manage(session, principal_id, action, workspace_id, question):
            return {"ok": True, **(knowledge or {})}

        async def sample(session, table, columns=None, schema=None):
            self.sampled.append(table)
            return {"rows": [{"company": "3M"}]}

        async def run(session, sql, max_rows=None):
            self.ran.append(sql)
            return results.pop(0)

        monkeypatch.setattr(sa, "MCPSession", _Session)
        monkeypatch.setattr(sa, "get_agent_llm", lambda role: _LLM())
        monkeypatch.setattr(sa, "schema_list_tables", list_tables)
        monkeypatch.setattr(sa, "describe_schema", describe)
        monkeypatch.setattr(sa, "workspaces_manage", manage)
        monkeypatch.setattr(sa, "data_sample_rows", sample)
        monkeypatch.setattr(sa, "data_run_select", run)


def _state(**extra):
    return {"tenant_id": "t", "principal_id": "p", "workspace_id": "ws", "target_schema": DATASET,
            "query_id": "q1", "raw_query": "What was 3M's revenue?", "errors": [], **extra}


OK = {"ok": True, "rows": [{"value": 32.8}], "columns": ["value"]}
EMPTY = {"ok": True, "rows": [], "columns": ["value"]}


def test_an_exact_approved_example_runs_as_is_with_no_model_call(monkeypatch):
    w = World(monkeypatch, [], [OK], knowledge={"exact": {"sql": "SELECT value FROM cn_0123456789abcdef.facts"}})
    update = RUN(sa.sql_agent_node(_state()))
    assert w.prompts == [] and w.ran == ["SELECT value FROM cn_0123456789abcdef.facts"]
    assert update["verified_example"] is True and update["executed_sql"] == w.ran[0]


def test_a_first_attempt_drafts_samples_the_tables_it_reads_then_writes_the_final_query(monkeypatch):
    draft = "SELECT f.value FROM facts f WHERE f.company = '3M'"
    final = "SELECT f.value FROM cn_0123456789abcdef.facts f WHERE f.company = '3M'"
    w = World(monkeypatch, [draft, final], [OK], knowledge={"business_context": "Annual SEC figures."})
    update = RUN(sa.sql_agent_node(_state()))
    assert len(w.prompts) == 2 and w.sampled == ["facts"] and w.ran == [final]
    assert "Sampled data" not in w.prompts[0][1] and "Sampled data" in w.prompts[1][1]
    assert "Annual SEC figures." in w.prompts[0][0]          # the owner's words, in the system prompt
    assert update["executed_sql"] == final and update["sql_rows"] == OK["rows"]
    assert update.get("caveats") is None and "errors" not in update


def test_a_retry_gets_the_error_history_and_a_corrected_answer_carries_the_caution(monkeypatch):
    earlier = {"attempt": 0, "category": "unknown_column", "engine_code": "42703", "message": "column x does not exist",
               "offending_sql": "SELECT x FROM facts", "remediation_hint": "Check the column names."}
    w = World(monkeypatch, ["SELECT f.value FROM cn_0123456789abcdef.facts f"], [OK])
    update = RUN(sa.sql_agent_node(_state(errors=[earlier])))
    system, user = w.prompts[0]
    assert "correcting a previously failed query" in system
    assert "column x does not exist" in user and "Check the column names." in user
    assert update["attempt"] == 1 and update["caveats"] == [sa.RETRY_CAUTION]


def test_a_first_empty_result_retries_and_a_second_is_the_answer(monkeypatch):
    World(monkeypatch, ["SELECT 1 FROM facts", "SELECT 1 FROM facts"], [EMPTY])
    first = RUN(sa.sql_agent_node(_state()))
    assert first["errors"][0]["category"] == "empty_result" and first["executed_sql"]
    World(monkeypatch, ["SELECT 1 FROM facts"], [EMPTY])
    second = RUN(sa.sql_agent_node(_state(errors=first["errors"])))
    assert "errors" not in second and second["sql_rows"] == [] and second["executed_sql"]


def test_a_refused_query_is_classified_and_not_reported_as_executed(monkeypatch):
    refused = {"ok": False, "error": {"sqlstate": "42703", "message": 'column "x" does not exist'}}
    World(monkeypatch, ["SELECT x FROM facts", "SELECT x FROM facts"], [refused])
    update = RUN(sa.sql_agent_node(_state()))
    assert "executed_sql" not in update and update["candidate_sql"] == "SELECT x FROM facts"
    assert update["errors"][0]["category"] == "unknown_column" and update["errors"][0]["remediation_hint"]


def test_a_query_that_reads_no_table_is_no_data_source(monkeypatch):
    refused = {"ok": False, "error": {"sqlstate": None,
                                      "message": "[Layer 1 Gate] Rejected: statement reads no table (a constant answer)."}}
    World(monkeypatch, ["SELECT 'not available'", "SELECT 'not available'"], [refused])
    assert RUN(sa.sql_agent_node(_state()))["errors"][0]["category"] == "no_data_source"


def test_the_tables_a_draft_reads_are_parsed_and_unparseable_sql_is_none():
    assert set(sa._parse_referenced_tables("WITH t AS (SELECT * FROM facts) SELECT * FROM t JOIN companies c ON true")) \
        == {"facts", "t", "companies"}
    assert sa._parse_referenced_tables("not sql at all") == []


def test_sample_targets_prefer_the_drafts_tables_and_fall_back_to_nullable_ones():
    other = {"table": "companies", "columns": [{"name": "name", "type": "text", "nullable": True}]}
    assert [t["table"] for t in sa._pick_sample_targets([FACTS, other], 4, ["companies"])] == ["companies"]
    assert [t["table"] for t in sa._pick_sample_targets([FACTS, other], 1, ["unknown"])] == ["facts"]


def test_on_the_two_source_route_the_agent_answers_only_the_database_part():
    q = {"route": "hybrid", "normalized_query": "full question", "subtasks": ["sql part", "doc part"]}
    assert sa.question_for(q) == "sql part"
    assert sa.question_for({**q, "route": "sql"}) == "full question"
