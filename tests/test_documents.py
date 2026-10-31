"""
tests/test_documents.py
The documents engine (orchestrator/nodes/documents.py).

Pinned here:
- round 1 finds the files and round 2 searches only inside them, for up to 25 passages;
- Jev re-ranks the passages the writer reads, and a Jev failure leaves the search's order;
- the Inspector trace records each passage's place before and after Jev;
- a two-source question is narrowed to the documents that name a company in the SQL result, and a
  "which ... highest" question to the top row's company only;
- the split step keeps a split it was given and asks the database part for names when narrowing follows.
The MCP and Jev are fakes.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))

import passage_jev  # noqa: E402
from nodes import documents as dc  # noqa: E402

RUN = asyncio.run
STATE = {"tenant_id": "t", "principal_id": "p", "workspace_id": "ws", "target_schema": "cn_0123456789abcdef",
         "query_id": "q1"}


def _m(chunk, doc, score, title=None):
    return {"chunk_id": chunk, "document_id": doc, "score": score, "title": title or doc.upper(), "page": 1,
            "content_md": f"text of {chunk}"}


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _wire(monkeypatch, first, second, jev_scores=None, jev_error=False):
    calls = []

    async def search(session, text, top_k=None, document_ids=None):
        calls.append({"text": text, "top_k": top_k, "document_ids": document_ids})
        return {"matches": first if document_ids is None else second}

    async def ask(state, questions):
        if jev_error:
            return SimpleNamespace(error="down", probabilities={}, ms=5)
        chunk = state.split("text of ")[1].strip()
        return SimpleNamespace(error=None, probabilities={"contains": (jev_scores or {}).get(chunk, 0.0)}, ms=5)

    monkeypatch.setattr(dc, "vector_semantic_search", search)
    monkeypatch.setattr(dc, "_session", lambda state: _Session())
    monkeypatch.setattr(passage_jev.jev_client, "ask", ask)
    return calls


def test_round_one_finds_the_files_and_round_two_searches_inside_them(monkeypatch):
    calls = _wire(monkeypatch, [_m("a1", "a", 0.9), _m("b1", "b", 0.8)], [_m("a2", "a", 0.85), _m("b2", "b", 0.7)])
    RUN(dc.documents_node({**STATE, "raw_query": "What is the strategy?"}))
    assert calls[0] == {"text": "What is the strategy?", "top_k": 8, "document_ids": None}
    assert calls[1] == {"text": "What is the strategy?", "top_k": 25, "document_ids": ["a", "b"]}


def test_jev_orders_what_the_writer_reads_and_the_trace_shows_both_orders(monkeypatch):
    _wire(monkeypatch, [_m("a1", "a", 0.9)], [_m("a1", "a", 0.9), _m("a2", "a", 0.6), _m("a3", "a", 0.5)],
          jev_scores={"a3": 0.95, "a1": 0.4, "a2": 0.1})
    update = RUN(dc.documents_node({**STATE, "raw_query": "q"}))
    assert [r["provenance"]["chunk_id"] for r in update["retrievals"]] == ["a3", "a1", "a2"]
    assert update["retrievals"][0]["provenance"]["jev_score"] == 0.95
    rows = {p["chunk_id"]: (p["rank_searched"], p["rank_final"]) for p in update["retrieval_trace"]["passages"]}
    assert rows == {"a1": (1, 2), "a2": (2, 3), "a3": (3, 1)}
    assert update["subtasks"] == ["q"]


def test_a_jev_failure_keeps_the_search_order(monkeypatch):
    _wire(monkeypatch, [_m("a1", "a", 0.9)], [_m("a1", "a", 0.9), _m("a2", "a", 0.6)], jev_error=True)
    update = RUN(dc.documents_node({**STATE, "raw_query": "q"}))
    assert [r["provenance"]["chunk_id"] for r in update["retrievals"]] == ["a1", "a2"]
    assert update["retrieval_trace"]["jev"]["failed"] == 2


def test_no_match_in_round_one_is_no_passages(monkeypatch):
    calls = _wire(monkeypatch, [], [])
    update = RUN(dc.documents_node({**STATE, "raw_query": "q"}))
    assert update["retrievals"] == [] and len(calls) == 1


def test_the_search_order_groups_the_window_by_document():
    chunks = [_m("b_c1", "b", 0.9), _m("a_c2", "a", 0.8), _m("b_c0", "b", 0.7), _m("a_c9", "a", 0.1)]
    assert [c["chunk_id"] for c in dc.dense_order(chunks, 3)] == ["b_c0", "b_c1", "a_c2", "a_c9"]


def test_title_names():
    assert dc.title_words("JOHNSON JOHNSON 2023 8K dated 2023-04-25") == "JOHNSON JOHNSON"
    assert dc.title_name("COCACOLA 2022 10K") == "cocacola"
    assert dc.title_name("Employee handbook") == "employeehandbook"


def test_entities_match_titles_both_ways():
    docs = [{"document_id": "j", "title": "JPMORGAN 2022 10K"}, {"document_id": "w", "title": "WALMART 2020 10K"},
            {"document_id": "m", "title": "3M 2018 10K"}]
    assert dc.match_titles(["JPMORGAN CHASE & CO"], docs, 4) == [docs[0]]
    assert dc.match_titles(["Walmart"], docs, 4) == [docs[1]]
    assert dc.match_titles(["3M Company"], docs, 4) == []           # "3m" is too short to match inside a longer name
    assert dc.match_titles(["3M"], docs, 4) == [docs[2]]


def test_a_superlative_question_narrows_to_the_top_row_only():
    rows = [{"company": "Walmart Inc.", "revenue": 611.3}, {"company": "Amazon", "revenue": 514.0}]
    assert dc.answer_entities(rows, ["company", "revenue"], "Which company had the highest revenue?", 5, 5) == ["Walmart Inc."]
    assert dc.answer_entities(rows, ["company", "revenue"], "Revenue of these companies?", 5, 5) == ["Walmart Inc.", "Amazon"]


def test_a_dependent_documents_part_searches_only_the_named_filings(monkeypatch):
    calls = _wire(monkeypatch, [], [_m("w1", "w", 0.7)])

    async def documents(state):
        return [{"document_id": "w", "title": "WALMART 2020 10K"}, {"document_id": "a", "title": "AMAZON 2022 10K"}]

    monkeypatch.setattr(dc, "workspace_documents", documents)
    state = {**STATE, "raw_query": "full", "docs_depend_on_sql": True,
             "subtasks": ["Which company had the highest revenue in 2022?", "How does it describe its e-commerce strategy?"],
             "sql_rows": [{"company": "Walmart"}, {"company": "Amazon"}], "sql_columns": ["company"]}
    update = RUN(dc.documents_after_sql_node(state))
    assert calls == [{"text": "How does it describe its e-commerce strategy?", "top_k": 25, "document_ids": ["w"]}]
    assert update["hybrid_entities"] == {"entities": ["Walmart"], "matched_documents": ["WALMART 2020 10K"], "narrowed": True}
    assert update["retrieval_trace"]["narrowed_to"] == ["WALMART 2020 10K"] and update["retrieval_trace"]["round1"] == []


def test_without_dependence_the_documents_part_uses_the_two_round_search(monkeypatch):
    calls = _wire(monkeypatch, [_m("x1", "x", 0.9)], [_m("x1", "x", 0.9)])
    state = {**STATE, "raw_query": "full", "subtasks": ["db part", "docs part"], "sql_rows": [{"company": "Walmart"}]}
    update = RUN(dc.documents_after_sql_node(state))
    assert [c["document_ids"] for c in calls] == [None, ["x"]] and "hybrid_entities" not in update


def test_the_split_step_keeps_a_given_split_and_asks_for_names_when_narrowing_follows(monkeypatch):
    async def never(question):
        raise AssertionError("no model call when the split is given")

    monkeypatch.setattr(dc, "_split", never)
    out = RUN(dc.split_question_node({"normalized_query": "q", "subtasks": ["db", "docs"], "docs_depend_on_sql": True}))
    assert out["subtasks"] == [f"db {dc._ASK_FOR_NAMES}", "docs"] and out["metrics"]["llm_calls"] == 0


def test_the_split_step_asks_the_model_when_no_split_was_given(monkeypatch):
    async def split(question):
        return "db part", "docs part", 3, 4

    monkeypatch.setattr(dc, "_split", split)
    out = RUN(dc.split_question_node({"normalized_query": "q"}))
    assert out["subtasks"] == ["db part", "docs part"] and out["metrics"]["llm_calls"] == 1
