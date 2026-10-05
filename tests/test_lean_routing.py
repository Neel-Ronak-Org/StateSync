"""
tests/test_lean_routing.py
Pins the Phase 3b lean-pipeline variants: the language gate in normalize_node,
the router skip in supervisor_node and the timeout retry cap in
_route_after_sql.

WHY THIS EXISTS. Each variant removes a model call or a retry, so each must
(a) do nothing unless its flag is switched, and (b) when switched, skip the
call ONLY where the answer is already known. The model seams are replaced by
fakes that raise if touched, so "no call" is asserted, not inferred.
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

from nodes import normalize as normalize_mod  # noqa: E402
from nodes import supervisor as supervisor_mod  # noqa: E402
from state import ErrorRecord, StateSyncState  # noqa: E402


class _Forbidden:
    def __getattr__(self, name):
        raise AssertionError("the model must not be called on this path")


def _normalize_state(text: str, gate: str) -> dict:
    return {"raw_query": text, "query_id": "q", "flags_snapshot": {"pipeline.language_gate": gate}}


def test_gate_script_skips_model_for_english(monkeypatch):
    monkeypatch.setattr(normalize_mod, "get_language_normalizer", lambda: _Forbidden())
    out = asyncio.run(normalize_mod.normalize_node(_normalize_state("How many orders were delivered?", "script")))
    assert out["detected_language"] == "en"
    assert out["normalized_query"] == "How many orders were delivered?"
    assert out["preserved_entities"] == {}
    assert out["metrics"]["llm_calls"] == 0


def test_gate_script_calls_model_for_devanagari(monkeypatch):
    calls = []

    class _Normalizer:
        async def normalize(self, text, target_schema=None):
            calls.append(text)
            return {"detected_language": "hi", "normalized_query": "translated",
                    "preserved_entities": {}, "tokens_in": 1, "tokens_out": 1}

    monkeypatch.setattr(normalize_mod, "get_language_normalizer", lambda: _Normalizer())
    out = asyncio.run(normalize_mod.normalize_node(_normalize_state("कितने ऑर्डर हैं?", "script")))
    assert calls == ["कितने ऑर्डर हैं?"]
    assert out["normalized_query"] == "translated"


def test_gate_llm_calls_model_for_english(monkeypatch):
    calls = []

    class _Normalizer:
        async def normalize(self, text, target_schema=None):
            calls.append(text)
            return {"detected_language": "en", "normalized_query": text,
                    "preserved_entities": {}, "tokens_in": 1, "tokens_out": 1}

    monkeypatch.setattr(normalize_mod, "get_language_normalizer", lambda: _Normalizer())
    asyncio.run(normalize_mod.normalize_node(_normalize_state("How many orders?", "llm")))
    assert calls == ["How many orders?"]


def _supervisor_state(mode: str, schema: str) -> dict:
    return {"query_id": "q", "normalized_query": "How many orders?", "target_schema": schema,
            "flags_snapshot": {"pipeline.router_mode": mode}}


class _FakeLLM:
    def __init__(self):
        self.calls = 0

    async def complete(self, **kwargs):
        self.calls += 1
        return SimpleNamespace(text='{"route":"docs"}', tokens_in=1, tokens_out=1)


def test_router_skips_model_when_source_has_no_documents(monkeypatch):
    monkeypatch.setattr(supervisor_mod, "get_agent_llm", lambda role: _Forbidden())
    out = asyncio.run(supervisor_mod.supervisor_node(_supervisor_state("when_documents", "live")))
    assert out["route"] == "sql"
    assert out["metrics"]["llm_calls"] == 0


def test_router_still_calls_model_for_document_sources(monkeypatch):
    llm = _FakeLLM()
    monkeypatch.setattr(supervisor_mod, "get_agent_llm", lambda role: llm)
    out = asyncio.run(supervisor_mod.supervisor_node(_supervisor_state("when_documents", "public")))
    assert out["route"] == "docs"
    assert llm.calls == 1


def test_router_always_mode_calls_model_even_without_documents(monkeypatch):
    llm = _FakeLLM()
    monkeypatch.setattr(supervisor_mod, "get_agent_llm", lambda role: llm)
    out = asyncio.run(supervisor_mod.supervisor_node(_supervisor_state("always", "live")))
    assert out["route"] == "docs"
    assert llm.calls == 1


@pytest.fixture
def graph():
    """orchestrator/graph.py needs langgraph, which the host interpreter may lack."""
    pytest.importorskip("langgraph")
    import graph as graph_mod
    return graph_mod


def _timeout_state(n_timeouts: int) -> StateSyncState:
    errors = [
        ErrorRecord(attempt=i, category="timeout", engine_code=None, message="t",
                    offending_sql=None, remediation_hint="h")
        for i in range(n_timeouts)
    ]
    return StateSyncState(
        query_id="q", tenant_id="t", principal_id="p", session_id="s",
        target_schema="live", raw_query="q", errors=errors, attempt=n_timeouts - 1,
    )


def test_timeout_cap_lowered_ends_the_loop(graph, monkeypatch):
    monkeypatch.setattr(graph, "_timeout_retry_max", lambda: 1)  # noqa: literals - one allowed retry
    assert graph._route_after_sql(_timeout_state(2)) == "terminal"


def test_timeout_shipped_cap_still_retries(graph):
    assert graph._route_after_sql(_timeout_state(2)) == "retry"
