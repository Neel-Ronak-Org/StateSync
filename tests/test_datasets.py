"""
tests/test_datasets.py
Which code runs for which kind of dataset (orchestrator/datasets.py).

WHY THIS EXISTS. The ONDC sample (public) was measured with the ONDC framing and the DIGIPIN
resolver on; that must not change. Every other dataset gets a neutral framing (profiles.py), and
an owner's own database, with no uploaded documents, must not spend a router call whose prompt
describes ONDC tables.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))

import datasets  # noqa: E402
from nodes import sql_agent  # noqa: E402
from nodes import supervisor as supervisor_mod  # noqa: E402

OWN = "cn_0123456789abcdef"


def test_the_ondc_sample_keeps_its_sql_framing():
    for template in (sql_agent._SYSTEM_PROMPT, sql_agent._CORRECTION_SYSTEM_PROMPT):
        assert sql_agent.sql_system_prompt(template, 1000, "public", {}) == template.format(max_rows=1000)


def test_olist_and_live_get_the_neutral_sql_framing():
    for name in ("olist", "live"):
        for prompt in (sql_agent._SYSTEM_PROMPT, sql_agent._CORRECTION_SYSTEM_PROMPT):
            framed = sql_agent.sql_system_prompt(prompt, 1000, name, {})
            assert "ONDC" not in framed and "business analytics system over a PostgreSQL database" in framed


def test_an_own_database_gets_a_neutral_framing_in_every_sql_prompt():
    assert datasets.is_connection_dataset(OWN)
    for prompt in (sql_agent._SYSTEM_PROMPT, sql_agent._CORRECTION_SYSTEM_PROMPT):
        framed = sql_agent.sql_system_prompt(prompt, 1000, OWN, {})
        assert "ONDC" not in framed and "owner's own PostgreSQL database" in framed


def test_only_the_exact_shape_is_an_own_database():
    for name in ("public", "cn_123", "CN_0123456789abcdef", None, 3):
        assert not datasets.is_connection_dataset(name)


class _Forbidden:
    def __getattr__(self, name):
        raise AssertionError("the router must not be called")


def _state(schema, titles=None):
    return {"query_id": "q", "normalized_query": "How many orders?", "target_schema": schema,
            "flags_snapshot": {"pipeline.router_mode": "always"}, "library_titles": titles or []}


def test_an_own_database_without_uploads_skips_the_router(monkeypatch):
    monkeypatch.setattr(supervisor_mod, "get_agent_llm", lambda role: _Forbidden())
    out = asyncio.run(supervisor_mod.supervisor_node(_state(OWN)))
    assert out["route"] == "sql" and out["metrics"]["llm_calls"] == 0


def test_with_uploads_the_router_still_decides(monkeypatch):
    from types import SimpleNamespace
    calls = []

    class _LLM:
        async def complete(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(text='{"route":"docs"}', tokens_in=1, tokens_out=1)

    monkeypatch.setattr(supervisor_mod, "get_agent_llm", lambda role: _LLM())
    assert asyncio.run(supervisor_mod.supervisor_node(_state(OWN, ["Refund policy"])))["route"] == "docs"
    assert asyncio.run(supervisor_mod.supervisor_node(_state("olist")))["route"] == "docs"
    assert len(calls) == 2
