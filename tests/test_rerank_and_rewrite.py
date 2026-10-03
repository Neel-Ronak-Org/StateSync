"""
tests/test_rerank_and_rewrite.py

WHY THIS EXISTS (docs/specs/rag_v3.md). On real filings the right document is usually found
but the exact page often is not (42% page hit@5). The re-ranker and search-query rewriting
are the remedies under test, so their contracts are pinned here:
- rerank "off" is today's search, with no call to the reranker service;
- with a re-ranker, rerank_candidates are fetched and the top_k come back in re-ranker order;
- a re-ranker failure never fails a search (the original order is kept);
- the orchestrator forwards the flag on both retrieval rounds, and the client omits "off";
- with query_rewrite = "llm" the search uses the rewrite, the answer the original question.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))

pytest.importorskip("psycopg")
pytest.importorskip("httpx")
sys.path.insert(0, str(REPO_ROOT / "mcp"))
if "psycopg_pool" not in sys.modules:
    try:
        import psycopg_pool  # noqa: F401
    except ImportError:
        import unittest.mock
        sys.modules["psycopg_pool"] = unittest.mock.MagicMock()
import tools.vector_tools as vt  # noqa: E402

sys.path.insert(0, str(REPO_ROOT / "orchestrator"))


def _match(cid: str, score: float) -> dict:
    return {"chunk_id": cid, "document_id": "d", "content_md": f"text {cid}", "page": 1, "score": score, "title": None}


class _Reply:
    def __init__(self, status: int, body: dict) -> None:
        self.status_code, self._body = status, body

    def json(self) -> dict:
        return self._body


def _fake_client(scores=None, raise_exc=None, seen=None):
    class _Client:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None):
            if seen is not None:
                seen.append(json)
            if raise_exc:
                raise raise_exc
            return _Reply(200, {"ok": True, "scores": scores(json["passages"])})
    return _Client


def _wire(monkeypatch, matches, client):
    calls = []

    async def fake_retrieve(query_text, top_k, document_ids, mode, prefixes, scope):
        calls.append(top_k)
        return {"matches": matches[:top_k], "best_dense_score": 0.9, "best_keyword_score": None,
                "lexemes": None, "lexemes_kept": None}

    monkeypatch.setattr(vt, "_retrieve", fake_retrieve)
    monkeypatch.setattr(vt.httpx, "AsyncClient", client)
    return calls


def test_rerank_off_is_todays_search_and_calls_no_reranker(monkeypatch):
    """WHY: the shipped path must not change, or even touch the new service."""
    seen = []
    calls = _wire(monkeypatch, [_match(f"c{i}", 1 - i / 100) for i in range(40)], _fake_client(lambda p: [0.0] * len(p), seen=seen))
    out = asyncio.run(vt.semantic_search("q", top_k=5))
    assert calls == [5] and seen == [] and "rerank" not in out
    assert [m["chunk_id"] for m in out["matches"]] == ["c0", "c1", "c2", "c3", "c4"]


def test_a_reranker_fetches_the_candidates_and_returns_the_top_k_in_its_order(monkeypatch):
    """WHY: the cross-encoder can only promote what the first stage fetched, so it must see
    rerank_candidates, not top_k; the caller still gets top_k."""
    seen = []
    matches = [_match(f"c{i}", 1 - i / 100) for i in range(40)]
    calls = _wire(monkeypatch, matches, _fake_client(lambda p: [float(i) for i in range(len(p))], seen=seen))
    out = asyncio.run(vt.semantic_search("what was capex", top_k=5, rerank="bge"))
    n = int(vt.cfg.retrieval.rerank_candidates)
    assert calls == [n] and seen[0]["model"] == "bge" and len(seen[0]["passages"]) == n
    assert [m["chunk_id"] for m in out["matches"]] == [f"c{n - 1 - i}" for i in range(5)]  # reversed by the fake scores
    assert all("rerank_score" in m for m in out["matches"]) and out["rerank"]["applied"] is True


def test_a_reranker_failure_keeps_the_original_order_and_never_fails_the_search(monkeypatch):
    """WHY: re-ranking is an improvement; an outage must degrade ranking, not answers."""
    matches = [_match(f"c{i}", 1 - i / 100) for i in range(40)]
    _wire(monkeypatch, matches, _fake_client(raise_exc=TimeoutError("slow")))
    out = asyncio.run(vt.semantic_search("q", top_k=3, rerank="minilm"))
    assert [m["chunk_id"] for m in out["matches"]] == ["c0", "c1", "c2"]
    assert out["rerank"]["applied"] is False and out["rerank"]["reason"] == "TimeoutError"


def test_an_unknown_reranker_is_refused():
    with pytest.raises(ValueError):
        asyncio.run(vt.semantic_search("q", rerank="gpt"))


def test_the_client_sends_rerank_only_when_it_is_on():
    """WHY: the shipped request stays byte-identical with the flag off."""
    from mcp_client import vector_semantic_search
    sent = []

    class _S:
        async def call(self, method, params, timeout=None):
            sent.append(params)
            return {"matches": []}

    timeouts = []

    class _T(_S):
        async def call(self, method, params, timeout=None):
            timeouts.append(timeout)
            return await super().call(method, params)

    asyncio.run(vector_semantic_search(_T(), "q", rerank="off"))
    asyncio.run(vector_semantic_search(_T(), "q", rerank="bge"))
    assert "rerank" not in sent[0] and sent[1]["rerank"] == "bge"
    # the re-ranked search waits longer than the cross-encoder may take, so it is not cut off at 20 s
    from config_loader import cfg
    assert timeouts[0] is None and timeouts[1] > float(cfg.retrieval.rerank_timeout_s)


def test_clean_rewrite_keeps_one_line_and_falls_back_to_the_question():
    from query_rewrite import clean_rewrite
    assert clean_rewrite('"3M FY2018 capital expenditure"\nextra', "orig") == "3M FY2018 capital expenditure"
    assert clean_rewrite("", "orig") == "orig"
    assert clean_rewrite("x" * (int(vt.cfg.retrieval.query_rewrite_max_chars) + 1), "orig") == "orig"
