"""
tests/test_datasets.py
Which code runs for which kind of dataset (orchestrator/datasets.py).

WHY THIS EXISTS. The built-in datasets were measured with the ONDC framing
and the DIGIPIN resolver on; that must not change (synthetic_schemas ships as
all three). An owner's own database must get neither, and, with no uploaded
documents, must not spend a router call whose prompt describes ONDC tables.
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


def test_the_built_in_datasets_keep_the_synthetic_handling():
    for name in ("public", "olist", "live"):
        assert datasets.synthetic_features(name)
        assert datasets.frame_prompt(sql_agent._SYSTEM_PROMPT, name) == sql_agent._SYSTEM_PROMPT


def test_an_own_database_gets_a_neutral_framing_in_every_sql_prompt():
    assert not datasets.synthetic_features(OWN) and datasets.is_connection_dataset(OWN)
    for prompt in (sql_agent._SYSTEM_PROMPT, sql_agent._CORRECTION_SYSTEM_PROMPT):
        framed = datasets.frame_prompt(prompt, OWN)
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
