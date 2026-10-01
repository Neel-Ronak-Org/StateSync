"""
tests/test_retrieval_mode_forwarding.py
Pins how the docs route hands the retrieval mode and the dataset to the MCP
(docs/specs/rag_v2.md Phase D, D3).

WHY THIS EXISTS. The keyword leg is only reachable through the flag
pipeline.retrieval_mode, and the flag only means something if BOTH rounds of the
two-round search carry it: the second round (restricted to the first round's
documents) fusing differently from the first would silently re-rank what the
first round found. A flag that is absent -- an old flags.toml, a test state --
must mean today's dense search, and the dataset must travel with every call so
Phase E's scoping has what it needs.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))

if "psycopg_pool" not in sys.modules:
    try:
        import psycopg_pool  # noqa: F401
    except ImportError:
        import unittest.mock
        sys.modules["psycopg_pool"] = unittest.mock.MagicMock()

from nodes import unstructured_knowledge as uk  # noqa: E402


class _Session:
    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _run(monkeypatch, flags: dict, target_schema: str | None = "olist") -> list[dict]:
    calls: list[dict] = []

    async def fake_search(session, query_text, top_k=None, document_ids=None, mode=None, dataset=None):
        calls.append({"top_k": top_k, "document_ids": document_ids, "mode": mode, "dataset": dataset})
        return {"matches": [{"chunk_id": "c1", "document_id": "doc_a", "content_md": "x", "score": 0.5}]}

    monkeypatch.setattr(uk, "MCPSession", _Session)
    monkeypatch.setattr(uk, "vector_semantic_search", fake_search)
    state = {"route": "docs", "normalized_query": "q", "tenant_id": "t", "flags_snapshot": flags}
    if target_schema is not None:
        state["target_schema"] = target_schema
    asyncio.run(uk.unstructured_knowledge_node(state))
    return calls


def test_both_rounds_carry_the_flags_mode_and_the_dataset(monkeypatch):
    calls = _run(monkeypatch, {"pipeline.retrieval_mode": "hybrid"}, target_schema="olist")
    assert len(calls) == 2
    assert calls[0]["document_ids"] is None and calls[1]["document_ids"] == ["doc_a"]
    assert [c["mode"] for c in calls] == ["hybrid", "hybrid"]
    assert [c["dataset"] for c in calls] == ["olist", "olist"]


def test_without_the_flag_the_mode_is_dense(monkeypatch):
    calls = _run(monkeypatch, {})
    assert [c["mode"] for c in calls] == ["dense", "dense"]


def test_the_shipped_flag_is_dense():
    assert uk.cfg.retrieval.mode == "dense"
    from config_loader import snapshot_flags
    assert snapshot_flags()["pipeline.retrieval_mode"] == "dense"


def test_the_client_sends_mode_and_dataset_only_when_given():
    sent: list[dict] = []

    class _S:
        async def call(self, method, params):
            sent.append(params)
            return {"matches": []}

    from mcp_client import vector_semantic_search
    asyncio.run(vector_semantic_search(_S(), "q"))
    asyncio.run(vector_semantic_search(_S(), "q", mode="hybrid", dataset="public"))
    assert sent[0] == {"query_text": "q"}
    assert sent[1] == {"query_text": "q", "mode": "hybrid", "dataset": "public"}
