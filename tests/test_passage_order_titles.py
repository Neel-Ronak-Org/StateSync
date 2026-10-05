"""
tests/test_passage_order_titles.py

WHY THIS EXISTS (docs/specs/partd_tasks/T3_passages_titles.md; docs/specs/routing_docs_fixes.md F-D, F-T).
The answer writer reads only the first retrieval.semantic_top_k of up to 25 passages, which were
ordered by document and position, not relevance; and neither the re-ranker nor the writer saw which
filing a passage came from. Pinned here, with every flag at its shipped default byte-identical:
- passage_order "relevance": the window is the most relevant passages, grouped by document;
- passage_titles: the writer's label names the document title and page;
- rerank_titles: the re-ranker scores "Document: {title}\\n{content}", and the orchestrator sends the
  parameter only when it should (flag on and re-ranking on).
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
import config_loader  # noqa: E402
from nodes import synthesizer as syn  # noqa: E402
from nodes import unstructured_knowledge as uk  # noqa: E402
from state import Provenance  # noqa: E402

WINDOW = 3  # noqa: literals - a small window for the ordering tests


def _m(chunk_id: str, doc: str, score: float, rerank_score: float | None = None) -> dict:
    m = {"chunk_id": chunk_id, "document_id": doc, "content_md": f"text {chunk_id}", "page": 1, "score": score,
         "title": None}
    if rerank_score is not None:
        m["rerank_score"] = rerank_score
    return m


def _ids(chunks: list[dict]) -> list[str]:
    return [c["chunk_id"] for c in chunks]


# ── C1: the ordering function ───────────────────────────────────────────────
def test_relevance_reads_the_rerank_score_when_present_else_the_score():
    """WHY: a cross-encoder score and a cosine score are different scales; a match has one or the other."""
    assert uk._relevance(_m("a", "d", 0.2, 5.0)) == 5.0
    assert uk._relevance(_m("a", "d", 0.2)) == 0.2
    assert uk._relevance(_m("a", "d", 0.2, 0.0)) == 0.0  # a re-ranker score of 0 is a score, not "absent"


def test_the_window_is_the_most_relevant_passages_and_the_rest_follow_in_relevance_order():
    chunks = [_m("d1_c1", "d1", 0.1), _m("d2_c1", "d2", 0.9), _m("d3_c1", "d3", 0.5),
              _m("d4_c1", "d4", 0.7), _m("d5_c1", "d5", 0.3)]
    out = uk.order_passages(chunks, "relevance", WINDOW, [])
    # window = d2 (0.9), d4 (0.7), d3 (0.5), grouped by document in relevance order; then d5, d1
    assert _ids(out) == ["d2_c1", "d4_c1", "d3_c1", "d5_c1", "d1_c1"]


def test_inside_the_window_a_document_is_grouped_and_in_chunk_order_and_documents_follow_their_best_passage():
    chunks = [_m("a_c3", "a", 0.9), _m("b_c1", "b", 0.95), _m("a_c1", "a", 0.8), _m("a_c2", "a", 0.1),
              _m("b_c2", "b", 0.7)]
    out = uk.order_passages(chunks, "relevance", 4, [])
    # window by relevance = b_c1, a_c3, a_c1, b_c2 ; b's best (0.95) beats a's (0.9)
    assert _ids(out) == ["b_c1", "b_c2", "a_c1", "a_c3", "a_c2"]


def test_the_window_edge_is_decided_by_relevance_not_by_document():
    """WHY: a document's weak passage stays outside the window when stronger passages fill it."""
    chunks = [_m("a_c1", "a", 0.9), _m("b_c1", "b", 0.8), _m("a_c2", "a", 0.1)]
    assert _ids(uk.order_passages(chunks, "relevance", 2, [])) == ["a_c1", "b_c1", "a_c2"]
    assert _ids(uk.order_passages(chunks, "relevance", 1, [])) == ["a_c1", "b_c1", "a_c2"]
    chunks = [_m("a_c1", "a", 0.9), _m("b_c1", "b", 0.8), _m("a_c0", "a", 0.85)]
    assert _ids(uk.order_passages(chunks, "relevance", 2, [])) == ["a_c0", "a_c1", "b_c1"]


def test_a_window_larger_than_the_list_groups_everything_and_a_window_of_zero_is_pure_relevance():
    chunks = [_m("a_c2", "a", 0.9), _m("b_c1", "b", 0.8), _m("a_c1", "a", 0.1)]
    assert _ids(uk.order_passages(chunks, "relevance", 25, [])) == ["a_c1", "a_c2", "b_c1"]
    assert _ids(uk.order_passages(chunks, "relevance", 0, [])) == ["a_c2", "b_c1", "a_c1"]
    assert uk.order_passages([], "relevance", WINDOW, []) == []


def test_ties_go_to_the_lower_chunk_id():
    chunks = [_m("b_c2", "b", 0.5), _m("a_c9", "a", 0.5), _m("a_c1", "a", 0.5), _m("c_c1", "c", 0.5)]
    assert _ids(uk.order_passages(chunks, "relevance", 2, [])) == ["a_c1", "a_c9", "b_c2", "c_c1"]
    # window 2 = a_c1, a_c9 (the two lowest ids among the ties); the rest in the same tie order
    assert _ids(uk.order_passages(chunks, "relevance", 1, [])) == ["a_c1", "a_c9", "b_c2", "c_c1"]


def test_the_rerank_score_decides_when_every_match_has_one():
    chunks = [_m("a_c1", "a", 0.99, rerank_score=1.0), _m("b_c1", "b", 0.10, rerank_score=7.0)]
    assert _ids(uk.order_passages(chunks, "relevance", 1, [])) == ["b_c1", "a_c1"]


def test_a_partly_reranked_set_is_ordered_by_the_search_score():
    """Lead review: a cross-encoder score and a cosine score are not on one scale."""
    chunks = [_m("a_c1", "a", 0.99), _m("b_c1", "b", 0.10, rerank_score=7.0)]
    assert _ids(uk.order_passages(chunks, "relevance", 1, [])) == ["a_c1", "b_c1"]


def test_document_order_is_exactly_todays_order_on_the_same_input():
    """WHY: the default must be byte-identical: documents in first-appearance order, chunk_id inside."""
    chunks = [_m("b_c2", "b", 0.9), _m("a_c3", "a", 0.1), _m("b_c1", "b", 0.2), _m("a_c1", "a", 0.8),
              _m("z_c1", "z", 0.99)]
    doc_order = ["b", "a"]  # z is not in doc_order: today's code drops it too
    expected = [c for d in doc_order for c in sorted((c for c in chunks if c["document_id"] == d),
                                                     key=lambda c: c["chunk_id"])]
    assert uk.order_passages(chunks, "document", WINDOW, doc_order) == expected
    assert _ids(expected) == ["b_c1", "b_c2", "a_c1", "a_c3"]


# ── the two-round search uses it ────────────────────────────────────────────
class _Session:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Search:
    def __init__(self):
        self.calls: list[dict] = []

    async def __call__(self, session, query_text, top_k=None, document_ids=None, mode=None, dataset=None,
                       scope=None, rerank=None, **kwargs):
        self.calls.append({"document_ids": document_ids, "rerank": rerank, **kwargs})
        if document_ids is not None:
            return {"matches": [_m("a_c2", "a", 0.2, 3.0), _m("a_c1", "a", 0.1, 1.0), _m("b_c1", "b", 0.3, 9.0),
                                _m("a_c9", "a", 0.1, 0.5)]}
        return {"matches": [_m("a_c1", "a", 0.4, 6.0), _m("b_c1", "b", 0.3, 9.0)],
                "best_dense_score": 0.9, "best_keyword_score": None}


def _expanded(monkeypatch, **kwargs):
    search = _Search()
    monkeypatch.setattr(uk, "vector_semantic_search", search)
    cfg = SimpleNamespace(retrieval=SimpleNamespace(**{**config_loader.cfg.retrieval.as_dict(), "semantic_top_k": 2}))
    monkeypatch.setattr(uk, "cfg", cfg)
    matches, _ = asyncio.run(uk._semantic_search_expanded(None, "q", 2, **kwargs))
    return search, matches


def test_the_two_round_search_merges_as_before_and_only_the_final_order_changes(monkeypatch):
    _, doc = _expanded(monkeypatch)
    _, rel = _expanded(monkeypatch, passage_order="relevance")
    # the first call's record wins for its chunks (a_c1 keeps score 0.4 / rerank 6.0)
    assert _ids(doc) == ["a_c1", "a_c2", "a_c9", "b_c1"]
    # window 2 = b_c1 (9.0), a_c1 (6.0); then a_c2 (3.0), a_c9 (0.5)
    assert _ids(rel) == ["b_c1", "a_c1", "a_c2", "a_c9"]
    assert {c["chunk_id"]: c["score"] for c in rel}["a_c1"] == 0.4
    assert sorted(_ids(doc)) == sorted(_ids(rel))


def test_provenance_carries_the_rerank_score_only_when_the_match_has_one():
    got = uk._to_retrievals([_m("a_c1", "a", 0.4, 6.0), _m("b_c1", "b", 0.3)])
    assert got[0]["provenance"]["rerank_score"] == 6.0
    assert "rerank_score" not in got[1]["provenance"]
    assert Provenance(**got[0]["provenance"]).rerank_score == 6.0  # survives the typed state
    assert Provenance(**got[1]["provenance"]).rerank_score is None


# ── C2: the writer's label ──────────────────────────────────────────────────
def _ret(chunk_id="d_c1", doc="doc_x", page=4, title="Acme 10-K 2022", score=0.5):
    return {"content": "body", "provenance": {"document_id": doc, "chunk_id": chunk_id, "page": page,
                                              "score": score, "title": title}}


def test_the_writers_label_off_is_unchanged():
    expected = "Retrieved 1 document passage(s):\n\n  [chunk_id=d_c1 document_id=doc_x page=4 score=0.500]\nbody"
    assert syn._format_doc_context([_ret()]) == expected
    assert syn._format_doc_context([_ret()], with_titles=False) == expected


def test_the_writers_label_on_names_the_title_and_page():
    out = syn._format_doc_context([_ret()], with_titles=True)
    assert "  [Document: Acme 10-K 2022 · page 4 · chunk_id=d_c1 · score=0.500]\nbody" in out
    assert "document_id=" not in out


def test_the_writers_label_on_falls_back_to_the_document_id_and_a_question_mark():
    out = syn._format_doc_context([_ret(title=None, page=None)], with_titles=True)
    assert "[Document: doc_x · page ? · chunk_id=d_c1 · score=0.500]" in out
    out = syn._format_doc_context([_ret(title="", page=0)], with_titles=True)
    assert "[Document: doc_x · page 0 ·" in out  # page 0 is a page; only a missing page is "?"


def test_the_synthesizer_reads_the_flag(monkeypatch):
    seen = []
    monkeypatch.setattr(syn, "_format_doc_context", lambda r, with_titles=False: seen.append(with_titles) or "ctx")
    monkeypatch.setattr(syn, "get_agent_llm", lambda role: (_ for _ in ()).throw(RuntimeError("stop")))
    for flags, want in (({}, False), ({"pipeline.passage_titles": "off"}, False), ({"pipeline.passage_titles": "on"}, True)):
        with pytest.raises(RuntimeError):
            asyncio.run(syn.synthesizer_node({"query_id": "q", "raw_query": "x", "retrievals": [_ret()],
                                              "flags_snapshot": flags}))
        assert seen[-1] is want


# ── C2: the re-ranker's passage texts (the HTTP call is a stub) ─────────────
class _Reply:
    status_code = 200

    def __init__(self, n):
        self._n = n

    def json(self):
        return {"ok": True, "scores": [float(i) for i in range(self._n)]}


def _stub_http(monkeypatch, seen):
    class _Client:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, json=None):
            seen.append(json)
            return _Reply(len(json["passages"]))

    monkeypatch.setattr(vt.httpx, "AsyncClient", _Client)


def _titled(cid, title, content="text"):
    return {"chunk_id": cid, "document_id": "d", "content_md": content, "page": 1, "score": 0.5, "title": title}


def test_rerank_passages_are_byte_identical_without_the_flag(monkeypatch):
    seen = []
    _stub_http(monkeypatch, seen)
    matches = [_titled("c1", "Acme 10-K", "alpha"), _titled("c2", None, None)]
    asyncio.run(vt._rerank("bge", "q", matches))
    asyncio.run(vt._rerank("bge", "q", matches, False))
    assert seen[0]["passages"] == ["alpha", ""] == seen[1]["passages"]


def test_rerank_passages_lead_with_the_title_when_on_and_a_missing_title_stays_plain(monkeypatch):
    seen = []
    _stub_http(monkeypatch, seen)
    matches = [_titled("c1", "Acme 10-K", "alpha"), _titled("c2", None, "beta"), _titled("c3", "", "gamma"),
               _titled("c4", "Acme 10-K", None)]
    asyncio.run(vt._rerank("bge", "q", matches, True))
    assert seen[0]["passages"] == ["Document: Acme 10-K\nalpha", "beta", "gamma", "Document: Acme 10-K\n"]
    assert seen[0]["model"] == "bge" and seen[0]["query"] == "q"


def test_semantic_search_accepts_an_optional_boolean_and_forwards_it(monkeypatch):
    seen = []
    _stub_http(monkeypatch, seen)

    async def fake_retrieve(query_text, top_k, document_ids, mode, prefixes, scope):
        return {"matches": [_titled("c1", "Acme 10-K", "alpha")]}

    monkeypatch.setattr(vt, "_retrieve", fake_retrieve)
    asyncio.run(vt.semantic_search("q", rerank="bge"))
    asyncio.run(vt.semantic_search("q", rerank="bge", rerank_titles=True))
    asyncio.run(vt.semantic_search("q", rerank="bge", rerank_titles=False))
    asyncio.run(vt.semantic_search("q", rerank="off", rerank_titles=True))  # no re-ranker: nothing to title
    assert [s["passages"] for s in seen] == [["alpha"], ["Document: Acme 10-K\nalpha"], ["alpha"]]
    for bad in ("yes", 1):
        with pytest.raises(ValueError):
            asyncio.run(vt.semantic_search("q", rerank="bge", rerank_titles=bad))


# ── C3: the orchestrator sends rerank_titles only when it should ────────────
def test_the_client_sends_rerank_titles_only_with_a_reranker_and_only_as_true():
    from mcp_client import vector_semantic_search
    sent = []

    class _S:
        async def call(self, method, params, timeout=None):
            sent.append(params)
            return {"matches": []}

    async def go():
        await vector_semantic_search(_S(), "q")
        await vector_semantic_search(_S(), "q", rerank="bge")
        await vector_semantic_search(_S(), "q", rerank="bge", rerank_titles=False)
        await vector_semantic_search(_S(), "q", rerank="off", rerank_titles=True)
        await vector_semantic_search(_S(), "q", rerank="bge", rerank_titles=True)
    asyncio.run(go())
    assert ["rerank_titles" in p for p in sent] == [False, False, False, False, True]
    assert sent[4] == {"query_text": "q", "rerank": "bge", "rerank_titles": True}
    assert sent[1] == {"query_text": "q", "rerank": "bge"}


@pytest.mark.parametrize("rerank,flag,sent", [
    ("bge", "on", True), ("bge", "off", False), ("off", "on", False), ("off", "off", False), (None, "on", False),
])
def test_the_search_node_passes_rerank_titles_on_both_rounds_only_when_the_flag_and_the_reranker_are_on(
        monkeypatch, rerank, flag, sent):
    search, _ = _expanded(monkeypatch, rerank=rerank, rerank_titles=flag == "on")
    assert len(search.calls) == 2
    assert all(("rerank_titles" in c) is sent for c in search.calls)


def test_the_docs_node_reads_the_flags(monkeypatch):
    search = _Search()
    monkeypatch.setattr(uk, "MCPSession", _Session)
    monkeypatch.setattr(uk, "vector_semantic_search", search)

    def run(flags):
        search.calls.clear()
        state = {"route": "docs", "normalized_query": "q", "tenant_id": "t", "flags_snapshot": flags}
        return asyncio.run(uk.unstructured_knowledge_node(state))

    shipped = run({"pipeline.rerank": "bge"})
    assert all("rerank_titles" not in c for c in search.calls)
    on = run({"pipeline.rerank": "bge", "pipeline.rerank_titles": "on", "pipeline.passage_order": "relevance"})
    assert all(c.get("rerank_titles") is True for c in search.calls)
    assert [r["provenance"]["chunk_id"] for r in shipped["retrievals"]] == ["a_c1", "a_c2", "a_c9", "b_c1"]
    assert [r["provenance"]["chunk_id"] for r in on["retrievals"]][0] == "b_c1"
    assert all("rerank_score" in r["provenance"] for r in on["retrievals"])


def test_the_new_flags_ship_at_todays_behaviour():
    snap = config_loader.snapshot_flags()
    assert snap["pipeline.passage_order"] == "document"
    assert snap["pipeline.passage_titles"] == "off" and snap["pipeline.rerank_titles"] == "off"


def test_relevance_uses_one_scale_when_some_chunks_lack_a_rerank_score():
    """Lead review: a round whose re-ranking failed keeps cosine scores; they must not be ranked
    against cross-encoder scores, so the order falls back to the retrieval score for every chunk."""
    from nodes.unstructured_knowledge import order_passages
    chunks = [
        {"document_id": "a", "chunk_id": "a_c001", "score": 0.40, "rerank_score": 9.0},
        {"document_id": "b", "chunk_id": "b_c001", "score": 0.80},
        {"document_id": "a", "chunk_id": "a_c002", "score": 0.60, "rerank_score": 1.0},
    ]
    order = [c["chunk_id"] for c in order_passages(chunks, "relevance", 3, ["a", "b"])]
    assert order[0] == "b_c001"            # best cosine first, not the 9.0 cross-encoder score
