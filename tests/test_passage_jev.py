"""
tests/test_passage_jev.py
Pins Jev passage re-ranking (pipeline.passage_rerank = "jev"; Paper B R1): the state text is R1's, passages are
ordered by Jev's probability (ties and failures keep the search's order, failures last), and the flag off leaves
the order untouched. Jev is faked; no network.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))

import passage_jev as pj  # noqa: E402


def _m(cid, title="ACME 2022 10K", page=3, text="Revenue rose 5%."):
    return {"chunk_id": cid, "document_id": "d", "title": title, "page": page, "content_md": text}


def test_state_text_matches_r1():
    s = pj.passage_state("What was revenue?", _m("a", text="x" * 5000), 3000)
    assert s.startswith("Question: What was revenue?\n\nPassage from ACME 2022 10K, page 3:\n")
    assert s.endswith("x" * 3000) and len(s.split("\n", 3)[-1]) == 3000


def test_order_by_scores_ties_and_failures():
    ms = [_m("a"), _m("b"), _m("c"), _m("d")]
    out = pj.order_by_scores(ms, [0.2, None, 0.9, 0.2])
    assert [m["chunk_id"] for m in out] == ["c", "a", "d", "b"]
    assert out[0]["jev_score"] == 0.9 and out[-1]["jev_score"] is None


def test_jev_order_uses_scores_and_survives_errors():
    scores = {"a": 0.1, "b": 0.8, "c": None}

    async def fake_ask(state, questions):
        cid = "a" if "AAA" in state else "b" if "BBB" in state else "c"
        s = scores[cid]
        return SimpleNamespace(error=None if s is not None else "HTTP 429", probabilities={"contains": s} if s is not None else {}, ms=5)

    ms = [_m("a", text="AAA"), _m("b", text="BBB"), _m("c", text="CCC")]
    with patch.object(pj.jev_client, "ask", fake_ask):
        out, trace = asyncio.run(pj.jev_order("q", ms))
    assert [m["chunk_id"] for m in out] == ["b", "a", "c"]
    assert trace == {"scored": 2, "failed": 1, "ms": 5}


def test_flag_off_leaves_order(monkeypatch):
    import nodes.unstructured_knowledge as uk
    data = object.__getattribute__(uk.cfg.pipeline, "_data")
    monkeypatch.setitem(data, "passage_rerank", "off")
    ms = [_m("a"), _m("b")]
    out, trace = asyncio.run(uk._maybe_jev_order("q", ms))
    assert out == ms and trace is None
