"""
tests/test_hybrid_route.py

WHY THIS EXISTS. The hybrid route (SQL + documents) used to run its own single-shot SQL: no
workspace knowledge, no approved examples, no templates, no correction loop. A compound question
got a worse SQL answer than the same data question asked alone. Since 2026-10-02 the route is
plan -> sql_agent_node (the same agent, with its correction loop) -> hybrid_docs_node ->
synthesizer. Pinned here:
- a successful SQL half on the hybrid route continues to the documents half;
- a failed SQL half still gets its documents half;
- the sql route is unchanged;
- the SQL agent answers only the SQL sub-question on the hybrid route;
- the plan node splits the question, or passes it whole when decomposition is off.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))
if "psycopg_pool" not in sys.modules:
    try:
        import psycopg_pool  # noqa: F401
    except ImportError:
        import unittest.mock
        sys.modules["psycopg_pool"] = unittest.mock.MagicMock()


def _state(route, errors=0, attempt=0, category="syntax_error"):
    errs = [SimpleNamespace(category=category) for _ in range(errors)]
    return SimpleNamespace(route=route, errors=errs, attempt=attempt)


def test_after_sql_sends_a_successful_hybrid_run_to_the_documents_half():
    graph = pytest.importorskip("graph")
    assert graph._after_sql(_state("hybrid")) == "hybrid_docs"
    assert graph._after_sql(_state("sql")) == "success"


def test_after_sql_keeps_the_correction_loop_for_both_routes():
    graph = pytest.importorskip("graph")
    assert graph._after_sql(_state("hybrid", errors=1, attempt=0)) == "retry"
    assert graph._after_sql(_state("sql", errors=1, attempt=0)) == "retry"


def test_a_failed_hybrid_sql_half_still_gets_its_documents_half():
    graph = pytest.importorskip("graph")
    assert graph._after_error_exit(_state("hybrid")) == "hybrid_docs"
    assert graph._after_error_exit(_state("sql")) == "synthesizer"


def test_the_sql_agent_answers_only_the_sql_part_on_the_hybrid_route():
    sql_agent = pytest.importorskip("nodes.sql_agent")
    q = {"route": "hybrid", "normalized_query": "full question", "subtasks": ["sql part", "doc part"]}
    assert sql_agent.question_for(q) == "sql part"
    assert sql_agent.question_for({**q, "route": "sql"}) == "full question"
    assert sql_agent.question_for({"route": "hybrid", "normalized_query": "full question"}) == "full question"


def test_the_plan_node_splits_or_passes_the_question_whole(monkeypatch):
    uk = pytest.importorskip("nodes.unstructured_knowledge")

    async def decompose(text):
        return "sql part", "doc part", 3, 4

    monkeypatch.setattr(uk, "_decompose", decompose)
    out = asyncio.run(uk.hybrid_plan_node({"normalized_query": "q", "flags_snapshot": {}}))
    assert out["subtasks"] == ["sql part", "doc part"] and out["metrics"]["llm_calls"] == 1
    off = asyncio.run(uk.hybrid_plan_node({"normalized_query": "q",
                                           "flags_snapshot": {"retrieval.hybrid_decomposition.enabled": False}}))
    assert off["subtasks"] == ["q", "q"] and off["metrics"]["llm_calls"] == 0
