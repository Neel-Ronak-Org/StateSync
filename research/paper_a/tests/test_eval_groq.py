"""
tests/test_eval_groq.py
Pins the pure logic of scripts/eval_groq.py, the fixed 30-question Groq
baseline (fixtures/golden/groq_baseline.json).

WHY THIS EXISTS. A baseline is only worth its number if the question set is
the one the rule says (30, the three unfailable queries excluded, same order
every time), the scoring rules are the stated ones (a cited chunk from the
right document counts for a document question), the summary arithmetic is
right, and a resumed run does not ask again what it already recorded. The
network and docker parts are exercised by `--dry-run` against the live stack.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import eval_groq as eg  # noqa: E402

EXCLUDED = {"c2g_03", "c2g_10", "c2g_18"}


def selection() -> dict:
    return json.loads(eg.SELECTION_PATH.read_text(encoding="utf-8"))


def test_selection_resolves_to_thirty_in_stated_shape():
    qs = eg.resolve_selection(selection())
    assert len(qs) == 30
    by_source = {}
    for q in qs:
        by_source[q["source"]] = by_source.get(q["source"], 0) + 1
    assert by_source == {"olist.json": 5, "olist_hard.json": 5, "phase_c2.json": 8, "phase_e1.json": 6, "multi_turn.json": 6}
    assert len({q["id"] for q in qs}) == 30


def test_selection_honours_exclusions_and_eligibility():
    qs = eg.resolve_selection(selection())
    assert not EXCLUDED & {q["id"] for q in qs}
    for q in qs:
        if q["kind"] in ("sql", "conversation"):
            assert q["expected_route"] == "sql" and q["reference_sql"]
        else:
            assert q["kind"] == "docs" and q["expected_route"] == "docs" and q["expected_chunk_ids"]


def test_selection_is_deterministic_and_conversations_are_ordered_pairs():
    assert eg.resolve_selection(selection()) == eg.resolve_selection(selection())
    convs = [q for q in eg.resolve_selection(selection()) if q["conversation_id"]]
    assert [(q["conversation_id"], q["turn_index"]) for q in convs] == [
        ("mt_01", 1), ("mt_01", 2), ("mt_02", 1), ("mt_02", 2), ("mt_03", 1), ("mt_03", 2)]


def test_missing_id_fails_loudly():
    bad = {"items": [{"source": "olist.json", "id": "nope", "schema": "olist", "kind": "sql"}], "conversations": []}
    try:
        eg.resolve_selection(bad)
    except KeyError:
        return
    raise AssertionError("a missing id must raise")


def test_document_id_strips_chunk_suffix():
    assert eg.document_id("doc_supplier_agreement_S014_c001") == "doc_supplier_agreement_S014"


def test_docs_scoring_exact_same_document_and_miss():
    expected = ["doc_sop_cold_chain_c000", "doc_sop_cold_chain_c001"]
    cite = lambda *refs: [{"kind": "sql", "ref": "SELECT 1", "page": None}] + [{"kind": "chunk", "ref": r, "page": None} for r in refs]
    assert eg.score_docs(cite("doc_sop_cold_chain_c001"), expected)[0]
    assert eg.score_docs(cite("doc_other_c000", "doc_sop_cold_chain_c007"), expected)[0]
    assert not eg.score_docs(cite("doc_other_c000"), expected)[0]
    assert not eg.score_docs([], expected)[0]


class FakeScorer:
    def score(self, generated, reference):
        return {"match": generated == reference, "generated_row_count": len(generated),
                "reference_row_count": len(reference), "row_recall": 1.0}


def test_sql_scoring_needs_executed_sql():
    assert eg.score_sql({"executed_sql": "SELECT 1", "sql_rows": [{"n": 1}]}, [{"n": 1}], FakeScorer())[0]
    assert not eg.score_sql({"executed_sql": "SELECT 1", "sql_rows": [{"n": 2}]}, [{"n": 1}], FakeScorer())[0]
    assert not eg.score_sql({"executed_sql": None, "sql_rows": []}, [], FakeScorer())[0]


def rec(id_, kind, source, correct, **kw):
    base = {"id": id_, "kind": kind, "source": source, "correct": correct, "route": "sql", "expected_route": "sql",
            "tokens_in": 100, "tokens_out": 10, "llm_calls": 2, "latency_ms": 1000, "error": None}
    return {**base, **kw}


def test_summary_math():
    records = [
        rec("a", "sql", "olist.json", True, latency_ms=100),
        rec("b", "sql", "olist.json", False, latency_ms=200, route="docs"),
        rec("c", "docs", "phase_e1.json", True, latency_ms=300, tokens_in=300),
        rec("d", "docs", "phase_e1.json", False, latency_ms=400, error="HTTP 500", tokens_in=None, tokens_out=None,
            llm_calls=None),
    ]
    s = eg.summarize(records, {"label": "t"})
    assert (s["recorded"], s["answered"], s["errors"], s["correct"]) == (4, 3, 1, 2)
    assert s["accuracy"] == 0.5
    assert s["accuracy_by_kind"]["sql"] == {"n": 2, "correct": 1, "accuracy": 0.5}
    assert s["accuracy_by_source"]["phase_e1.json"]["accuracy"] == 0.5
    assert s["route_accuracy"] == 0.75
    assert s["tokens_in_mean"] == (100 + 100 + 300) / 3 and s["tokens_in_median"] == 100
    assert s["total_tokens"] == 500 + 30
    assert (s["latency_ms_p50"], s["latency_ms_p90"]) == (200, 400)
    assert s["label"] == "t"


def test_percentile_nearest_rank_and_empty():
    assert eg.percentile([], 50) is None
    assert eg.percentile(list(range(1, 11)), 50) == 5
    assert eg.percentile(list(range(1, 11)), 90) == 9
    assert eg.percentile([7], 90) == 7


def test_resume_skips_recorded_ids_and_keeps_order():
    qs = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    assert [q["id"] for q in eg.pending(qs, [{"id": "b"}])] == ["a", "c"]
    assert eg.pending(qs, [{"id": "a"}, {"id": "b"}, {"id": "c"}]) == []


def test_stop_signals():
    assert eg.is_stop_signal(429, "")
    assert eg.is_stop_signal(503, "Model is Rate-Limited, try later")
    assert not eg.is_stop_signal(503, "database down")
    assert not eg.is_stop_signal(500, "rate-limited")
