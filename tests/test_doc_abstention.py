"""
tests/test_doc_abstention.py
Pins abstention on the docs and hybrid routes (docs/specs/rag_v2.md Phase E, flag
pipeline.doc_abstain): the rule, what each route does when it fires, and that the flag
off is exactly today's behaviour.

WHY THIS EXISTS.
- A model asked to answer from passages that do not contain the answer is where an
  unsupported answer comes from. When nothing relevant was retrieved, the docs route must
  say so in a fixed sentence, with no model call, no citation and no chart.
- The rule reads the FIRST round's best_dense_score and best_keyword_score, never the fused
  RRF score (about the same for answerable and unanswerable questions), and a missing score
  counts as below its threshold. Strict "<": a score equal to its threshold is relevant.
  The boundary cases are pinned because the thresholds were calibrated on a frozen set
  and a flipped comparison would silently change which side every question falls on.
- Abstaining skips the second (expanded) round: that round re-searches the first round's
  documents, and there are none worth expanding.
- On the hybrid route (SQL plus documents) the SQL half may be the whole answer, so only the
  document retrievals are dropped: no fixed message, doc_abstained stays False.
- The fixed answer must pass what reads the synthesizer's output: the A7 validator, number
  grounding (it holds no figure) and the evidence tier (no SQL ran, so none).
- Flag off: no extra work and no state change.
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

import config_loader  # noqa: E402
from evidence import evidence_tier  # noqa: E402
from nodes import synthesizer as syn  # noqa: E402
from nodes import unstructured_knowledge as uk  # noqa: E402
from number_grounding import ground  # noqa: E402
from schemas.synthesizer_output import safe_synthesizer_output  # noqa: E402
from state import _LAST_WRITE_WINS_FIELDS, StateSyncState  # noqa: E402

MIN_DENSE, MIN_KEYWORD = 0.5, 4.0
MESSAGE = config_loader.cfg.retrieval.abstain_message


# ── the rule ────────────────────────────────────────────────────────────────
def test_should_abstain_boundaries():
    """Abstain only when BOTH legs are below their thresholds (a missing score is below)."""
    rule = uk.should_abstain
    assert rule(0.49, 3.9, MIN_DENSE, MIN_KEYWORD) is True
    assert rule(0.50, 3.9, MIN_DENSE, MIN_KEYWORD) is False      # equal to the threshold: relevant
    assert rule(0.49, 4.0, MIN_DENSE, MIN_KEYWORD) is False
    assert rule(0.90, 9.0, MIN_DENSE, MIN_KEYWORD) is False
    # None means that leg found nothing
    assert rule(None, None, MIN_DENSE, MIN_KEYWORD) is True
    assert rule(None, 3.9, MIN_DENSE, MIN_KEYWORD) is True
    assert rule(0.49, None, MIN_DENSE, MIN_KEYWORD) is True
    assert rule(None, 4.0, MIN_DENSE, MIN_KEYWORD) is False
    assert rule(0.50, None, MIN_DENSE, MIN_KEYWORD) is False
    # dense mode has no keyword score at all: only the dense condition decides
    assert rule(0.49, None, MIN_DENSE, MIN_KEYWORD) is True and rule(0.51, None, MIN_DENSE, MIN_KEYWORD) is False
    # thresholds of 0 never abstain on a real score
    assert rule(0.0, 0.0, 0.0, 0.0) is False and rule(0.2, 0.1, 0.0, 0.0) is False


def test_the_shipped_flag_is_off_and_the_message_is_the_specified_one():
    assert config_loader.snapshot_flags()["pipeline.doc_abstain"] == "off"
    assert MESSAGE == "I couldn't find this in the documents available to this question."
    assert config_loader.cfg.retrieval.abstain_min_dense >= 0 and config_loader.cfg.retrieval.abstain_min_keyword >= 0


def test_the_state_field_exists_and_is_registered():
    state = StateSyncState(query_id="q", tenant_id="t", principal_id="p", session_id="s",
                           target_schema="public", raw_query="x")
    assert state.doc_abstained is False
    assert "doc_abstained" in _LAST_WRITE_WINS_FIELDS


# ── the nodes ───────────────────────────────────────────────────────────────
class _Session:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Cfg:
    """The real config with the abstention thresholds fixed, so the tests do not depend on the calibrated values."""

    def __init__(self):
        retrieval = {**config_loader.cfg.retrieval.as_dict(),
                     "abstain_min_dense": MIN_DENSE, "abstain_min_keyword": MIN_KEYWORD}
        self.retrieval = SimpleNamespace(**retrieval)

    def __getattr__(self, name):
        return getattr(config_loader.cfg, name)


class _Search:
    """A fake vector.semantic_search: the first round answers with the given scores, the second with siblings."""

    def __init__(self, best_dense, best_keyword):
        self.best_dense, self.best_keyword = best_dense, best_keyword
        self.calls: list[dict] = []

    async def __call__(self, session, query_text, top_k=None, document_ids=None, mode=None, dataset=None,
                       scope=None, rerank=None):
        self.calls.append({"document_ids": document_ids, "mode": mode, "dataset": dataset, "scope": scope})
        if document_ids is not None:
            return {"matches": [{"chunk_id": "c2", "document_id": "doc_a", "content_md": "second", "score": 0.4}]}
        return {"matches": [{"chunk_id": "c1", "document_id": "doc_a", "content_md": "first", "score": 0.03}],
                "best_dense_score": self.best_dense, "best_keyword_score": self.best_keyword}


def _docs(monkeypatch, flags: dict, best_dense, best_keyword):
    search = _Search(best_dense, best_keyword)
    monkeypatch.setattr(uk, "MCPSession", _Session)
    monkeypatch.setattr(uk, "vector_semantic_search", search)
    monkeypatch.setattr(uk, "cfg", _Cfg())
    state = {"route": "docs", "normalized_query": "q", "tenant_id": "t", "target_schema": "public",
             "flags_snapshot": flags}
    return search, asyncio.run(uk.unstructured_knowledge_node(state))


ON = {"pipeline.doc_abstain": "on"}


def test_docs_route_abstains_when_both_scores_are_low_and_skips_the_second_round(monkeypatch):
    search, update = _docs(monkeypatch, ON, best_dense=0.2, best_keyword=1.0)
    assert len(search.calls) == 1 and search.calls[0]["document_ids"] is None
    assert update["retrievals"] == [] and update["doc_abstained"] is True
    assert update["metrics"]["llm_calls"] == 0


def test_docs_route_abstains_in_dense_mode_on_the_dense_score_alone(monkeypatch):
    search, update = _docs(monkeypatch, {**ON, "pipeline.retrieval_mode": "dense"}, best_dense=0.2, best_keyword=None)
    assert update["doc_abstained"] is True and len(search.calls) == 1


def test_docs_route_does_not_abstain_when_either_score_is_high(monkeypatch):
    for dense, keyword in ((0.8, 1.0), (0.2, 9.0), (0.8, None)):
        search, update = _docs(monkeypatch, ON, dense, keyword)
        assert len(search.calls) == 2, (dense, keyword)
        assert "doc_abstained" not in update
        assert [r["content"] for r in update["retrievals"]] == ["first", "second"]


def test_the_rule_reads_the_first_rounds_best_scores_not_the_fused_score(monkeypatch):
    """WHY: the fake's fused `score` is 0.03 (RRF-sized) on a first round whose best dense
    score is 0.9; reading the fused score would abstain on every question."""
    search, update = _docs(monkeypatch, ON, best_dense=0.9, best_keyword=None)
    assert len(search.calls) == 2 and "doc_abstained" not in update


def test_a_first_response_without_the_score_keys_counts_as_nothing_found(monkeypatch):
    """WHY: an older MCP sends no best_* keys; both read as None, which is below any threshold."""
    class _Old(_Search):
        async def __call__(self, session, query_text, **kwargs):
            self.calls.append(kwargs)
            return {"matches": [{"chunk_id": "c1", "document_id": "d", "content_md": "x", "score": 0.5}]}

    monkeypatch.setattr(uk, "MCPSession", _Session)
    monkeypatch.setattr(uk, "vector_semantic_search", _Old(None, None))
    monkeypatch.setattr(uk, "cfg", _Cfg())
    update = asyncio.run(uk.unstructured_knowledge_node(
        {"route": "docs", "normalized_query": "q", "tenant_id": "t", "flags_snapshot": ON}))
    assert update["doc_abstained"] is True and update["retrievals"] == []


def test_flag_off_is_todays_behaviour_even_when_the_scores_are_low(monkeypatch):
    for flags in ({}, {"pipeline.doc_abstain": "off"}):
        search, update = _docs(monkeypatch, flags, best_dense=0.0, best_keyword=None)
        assert len(search.calls) == 2
        assert "doc_abstained" not in update
        assert [r["content"] for r in update["retrievals"]] == ["first", "second"]
        assert update["metrics"]["trajectory_steps"] == 2


def test_both_rounds_carry_scope_and_the_abstention_does_not_widen_it(monkeypatch):
    search, _ = _docs(monkeypatch, {"pipeline.doc_scope": "dataset"}, best_dense=0.9, best_keyword=None)
    assert [c["scope"] for c in search.calls] == ["dataset", "dataset"]


# ── the synthesizer ─────────────────────────────────────────────────────────
class _ExplodingLLM:
    async def complete(self, *args, **kwargs):
        raise AssertionError("the abstention answer must not call a model")


def test_the_synthesizer_answers_the_fixed_message_with_no_model_call(monkeypatch):
    calls = []
    monkeypatch.setattr(syn, "get_agent_llm", lambda role: calls.append(role) or _ExplodingLLM())
    state = {"query_id": "q", "raw_query": "what is the airspeed of a swallow?", "retrievals": [],
             "doc_abstained": True, "flags_snapshot": {"pipeline.number_grounding": "on"}}
    out = asyncio.run(syn.synthesizer_node(state))
    assert out["answer_text"] == MESSAGE
    assert out["vega_spec"] is None
    assert out["metrics"]["llm_calls"] == 0 and out["metrics"]["tokens_in"] == 0 and out["metrics"]["tokens_out"] == 0
    assert calls == [], "no model was even resolved"
    # no citations: there is no passage and no SQL to cite
    assert syn.build_citations(None, None, []) == []


def test_the_fixed_answer_passes_what_reads_the_synthesizers_output():
    """WHY: A7 is validated again in main.py, number grounding runs on the prose, and the
    evidence tier is derived from the state; the fixed answer must pass all three cleanly."""
    safe = safe_synthesizer_output({"answer_text": MESSAGE, "chart": None, "citations": []})
    assert safe["answer_text"] == MESSAGE and safe["chart"] is None and safe["citations"] == []
    grounding = ground(MESSAGE, [], [], "what is the airspeed of a swallow?", [])
    assert grounding["unmatched"] == []                              # no figure, so no caveat
    assert evidence_tier(False, None, {"pipeline.answer_checks": "on"}, []) is None   # no SQL ran: no tier


def test_without_the_state_field_the_synthesizer_still_calls_the_model(monkeypatch):
    class _LLM:
        calls = 0

        async def complete(self, system, messages, max_tokens):
            _LLM.calls += 1
            return SimpleNamespace(text='{"answer_text": "from the model", "chart": null, "citations": []}',
                                   tokens_in=1, tokens_out=1)

    monkeypatch.setattr(syn, "get_agent_llm", lambda role: _LLM())
    out = asyncio.run(syn.synthesizer_node({"query_id": "q", "raw_query": "x", "retrievals": []}))
    assert out["answer_text"] == "from the model" and _LLM.calls == 1 and out["metrics"]["llm_calls"] == 1


# ── the hybrid (SQL + documents) route ──────────────────────────────────────
def _hybrid(monkeypatch, flags: dict, best_dense, best_keyword):
    search = _Search(best_dense, best_keyword)

    async def decompose(query_text):
        return "sql part", "doc part", 1, 1

    async def run_sql(session, question, state_dict=None):
        # no seller_id/provider_id column: the doc half uses the two-round semantic search
        return "SELECT 1", [{"n": 7}], ["n"], None, 1, 1, 1, 1

    monkeypatch.setattr(uk, "MCPSession", _Session)
    monkeypatch.setattr(uk, "vector_semantic_search", search)
    monkeypatch.setattr(uk, "_decompose", decompose)
    monkeypatch.setattr(uk, "_run_sql_subtask", run_sql)
    monkeypatch.setattr(uk, "cfg", _Cfg())
    state = {"route": "hybrid", "normalized_query": "q", "tenant_id": "t", "target_schema": "public",
             "flags_snapshot": flags}
    return search, asyncio.run(uk.unstructured_knowledge_node(state))


def test_hybrid_route_drops_the_document_retrievals_and_continues_normally(monkeypatch):
    search, update = _hybrid(monkeypatch, ON, best_dense=0.2, best_keyword=1.0)
    assert len(search.calls) == 1                                    # the expanded round was skipped
    assert update["retrievals"] == []
    assert "doc_abstained" not in update                             # stays False: no fixed message
    assert update["executed_sql"] == "SELECT 1" and update["sql_rows"] == [{"n": 7}]
    assert update["metrics"]["llm_calls"] == 2                       # decomposer + SQL, as ever

    # the synthesizer then answers from the SQL result through the model, not with the fixed message
    prompts = []

    class _LLM:
        async def complete(self, system, messages, max_tokens):
            prompts.append(messages[0]["content"])
            return SimpleNamespace(text='{"answer_text": "There are 7.", "chart": null, "citations": []}',
                                   tokens_in=1, tokens_out=1)

    monkeypatch.setattr(syn, "get_agent_llm", lambda role: _LLM())
    state = {"query_id": "q", "raw_query": "q", "sql_rows": update["sql_rows"], "sql_columns": update["sql_columns"],
             "executed_sql": update["executed_sql"], "retrievals": update["retrievals"],
             "doc_abstained": False, "flags_snapshot": {}}
    out = asyncio.run(syn.synthesizer_node(state))
    assert out["answer_text"] == "There are 7." and out["answer_text"] != MESSAGE
    assert len(prompts) == 1 and "SQL query returned 1 row" in prompts[0] and "Retrieved" not in prompts[0]


def test_hybrid_route_keeps_the_retrievals_when_a_score_is_high_or_the_flag_is_off(monkeypatch):
    search, update = _hybrid(monkeypatch, ON, best_dense=0.8, best_keyword=None)
    assert len(search.calls) == 2 and len(update["retrievals"]) == 2
    search, update = _hybrid(monkeypatch, {}, best_dense=0.0, best_keyword=None)
    assert len(search.calls) == 2 and len(update["retrievals"]) == 2 and "doc_abstained" not in update


@pytest.mark.parametrize("flags", [ON, {}])
def test_no_state_field_is_written_by_the_hybrid_route(monkeypatch, flags):
    _, update = _hybrid(monkeypatch, flags, best_dense=0.0, best_keyword=None)
    assert "doc_abstained" not in update
