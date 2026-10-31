"""
tests/test_graph.py
The pipeline's wiring (orchestrator/graph.py): which step runs after which.

Pinned here, first as the edge functions and then by running the whole graph with every step faked:
- the router's route picks the next step, and "neither" goes straight to the answer writer;
- the SQL retry loop: retry a retriable error, stop on a final category, on too many timeouts or at
  sql.retry_max;
- a two-source question continues to its documents part after the SQL agent, also when the SQL failed;
- route recovery runs the documents route once for a database question whose data the database does not
  hold, only in a workspace with documents.
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

graph = pytest.importorskip("graph")
from state import StateSyncState  # noqa: E402


def _s(route="sql", errors=(), attempt=0, **extra):
    return SimpleNamespace(route=route, errors=[SimpleNamespace(category=c) for c in errors], attempt=attempt,
                           recovered_from=None, library_titles=[], out_of_scope=False, **extra)


def test_the_route_picks_the_next_step_and_neither_goes_to_the_writer():
    for route in ("sql", "docs", "hybrid"):
        assert graph._route_after_front_door(_s(route)) == route
    assert graph._route_after_front_door(SimpleNamespace(out_of_scope=True, route=None)) == "synthesizer"
    with pytest.raises(RuntimeError):
        graph._route_after_front_door(SimpleNamespace(out_of_scope=False, route=None))


def test_the_retry_loop():
    assert graph._route_after_sql(_s()) == "success"
    assert graph._route_after_sql(_s(errors=["unknown_column"], attempt=0)) == "retry"
    for final in ("permission_denied", "no_data_source", "unclassified"):
        assert graph._route_after_sql(_s(errors=[final], attempt=0)) == "terminal"
    retry_max = int(graph.cfg.sql.retry_max)
    assert graph._route_after_sql(_s(errors=["syntax"] * (retry_max + 1), attempt=retry_max)) == "terminal"
    timeouts = int(graph.cfg.sql.timeout_retry_max) + 1
    assert graph._route_after_sql(_s(errors=["timeout"] * timeouts, attempt=timeouts - 1)) == "terminal"


def test_a_two_source_question_reaches_its_documents_part_also_when_the_sql_failed():
    assert graph._after_sql(_s("hybrid")) == "hybrid_docs"
    assert graph._after_sql(_s("sql")) == "success"
    assert graph._after_error_exit(_s("hybrid", errors=["unclassified"])) == "hybrid_docs"


def test_route_recovery_needs_no_data_source_and_documents_and_runs_once():
    with_docs = _s("sql", errors=["no_data_source"])
    with_docs.library_titles = ["WALMART 2020 10K"]
    assert graph._after_error_exit(with_docs) == "recover_docs"
    assert graph._after_error_exit(_s("sql", errors=["no_data_source"])) == "synthesizer"      # no documents
    other = _s("sql", errors=["permission_denied"])
    other.library_titles = ["X"]
    assert graph._after_error_exit(other) == "synthesizer"
    with_docs.recovered_from = "sql"
    assert graph._after_error_exit(with_docs) == "synthesizer"


def _run(monkeypatch, route, sql_updates, library_titles=()):
    """The whole graph with every step faked; returns (the steps that ran, the final state)."""
    ran: list[str] = []
    sql_updates = list(sql_updates)

    def fake(name, update):
        async def step(state):
            ran.append(name)
            return update(state) if callable(update) else dict(update)
        return step

    def sql_step(state):
        u = sql_updates.pop(0)
        return {"attempt": len(state.get("errors", [])), **u}

    for attr, name, update in (
        ("entry_node", "entry", {}), ("front_door_node", "router", {"route": route} if route else {"out_of_scope": True}),
        ("sql_agent_node", "sql", sql_step), ("documents_node", "docs", {}), ("split_question_node", "split", {}),
        ("documents_after_sql_node", "docs_after_sql", {}), ("error_exit_node", "error_exit", {}),
        ("synthesizer_node", "writer", {"answer_text": "done"}),
    ):
        monkeypatch.setattr(graph, attr, fake(name, update))
    compiled = graph.build_graph()
    state = StateSyncState(query_id="q", tenant_id="t", principal_id="p", session_id="s", raw_query="q",
                           target_schema="cn_0123456789abcdef", library_titles=list(library_titles))
    final = asyncio.run(compiled.ainvoke(state))
    return ran, final


def _error(category):
    return {"errors": [{"attempt": 0, "category": category, "engine_code": None, "message": "m",
                        "offending_sql": "SELECT 1", "remediation_hint": "h"}]}


def test_a_database_question_retries_then_succeeds(monkeypatch):
    ran, final = _run(monkeypatch, "sql", [_error("syntax"), {"executed_sql": "SELECT 1"}])
    assert ran == ["entry", "router", "sql", "sql", "writer"] and final["answer_text"] == "done"


def test_a_two_source_question_runs_split_sql_documents_writer(monkeypatch):
    ran, _ = _run(monkeypatch, "hybrid", [{"executed_sql": "SELECT 1"}])
    assert ran == ["entry", "router", "split", "sql", "docs_after_sql", "writer"]


def test_neither_runs_only_the_writer(monkeypatch):
    ran, _ = _run(monkeypatch, None, [])
    assert ran == ["entry", "router", "writer"]


def test_route_recovery_end_to_end(monkeypatch):
    ran, final = _run(monkeypatch, "sql", [_error("no_data_source")], library_titles=["WALMART 2020 10K"])
    assert ran == ["entry", "router", "sql", "error_exit", "docs", "writer"]
    assert final["recovered_from"] == "sql"
