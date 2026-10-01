"""
tests/test_eval_retrieval.py
Pins the pure scoring logic of scripts/eval_retrieval.py, the harness that
measures the production MCP search path on fixtures/golden/retrieval_v2.json.

WHY THIS EXISTS. Every later phase of docs/specs/rag_v2.md (layout parsing,
structure-aware chunking, hybrid search, scoping) is judged by this harness's
numbers, so the arithmetic must be right and must not move: a hit needs the
gold document AND an evidence string (lower-cased, whitespace collapsed), hit@k
and MRR@10 count the first hit only, unanswerable questions never enter the hit
rates, and the percentiles interpolate. The docker and HTTP parts are
exercised by running the harness against the live stack.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import eval_retrieval as er  # noqa: E402

GOLD = {"doc_a"}
EVIDENCE = ["Metro NCR | 36 hours | 2% penalty"]


def passage(doc: str, text: str, score: float = 0.5) -> dict:
    return {"document_id": doc, "content_md": text, "score": score}


def question(category: str = "table", evidence: list[str] | None = None) -> dict:
    return {"id": "q", "corpus": "sample", "dataset": "public", "category": category, "question": "?",
            "gold_document": "doc_a", "evidence": EVIDENCE if evidence is None else evidence}


def test_normalize_lowercases_and_collapses_whitespace():
    assert er.normalize("  Metro   NCR |\n 36\thours ") == "metro ncr | 36 hours"


def test_hit_needs_the_gold_document_and_an_evidence_string():
    right = passage("doc_a", "| Zone | SLA |\n| Metro NCR |   36 HOURS | 2% penalty |")
    assert er.passage_hits(passage("doc_a", "x\nMetro NCR | 36 hours | 2% penalty\ny"), GOLD, EVIDENCE)
    assert er.passage_hits(passage("doc_a", "metro  ncr | 36   hours | 2% PENALTY"), GOLD, EVIDENCE)
    assert not er.passage_hits(right, GOLD, ["Metro NCR | 36 hours | 3% penalty"])
    assert not er.passage_hits(passage("doc_b", "Metro NCR | 36 hours | 2% penalty"), GOLD, EVIDENCE)  # wrong document
    assert not er.passage_hits(passage("doc_a", "something else"), GOLD, EVIDENCE)  # right document, no evidence
    assert not er.passage_hits(passage("doc_a", "anything"), GOLD, [])  # no evidence never matches
    assert not er.passage_hits(passage("doc_a", "anything"), GOLD, [""])  # an empty string is not evidence
    assert er.passage_hits(passage("doc_a", "second string here"), GOLD, ["absent", "Second String"])  # any one will do


def test_first_hit_rank_is_one_based_and_limited_to_k():
    miss = passage("doc_b", "no")
    hit = passage("doc_a", "Metro NCR | 36 hours | 2% penalty")
    assert er.first_hit_rank([hit, miss], GOLD, EVIDENCE) == 1
    assert er.first_hit_rank([miss, miss, hit, hit], GOLD, EVIDENCE) == 3
    assert er.first_hit_rank([miss, miss], GOLD, EVIDENCE) is None
    assert er.first_hit_rank([], GOLD, EVIDENCE) is None
    assert er.first_hit_rank([miss, miss, hit], GOLD, EVIDENCE, k=2) is None  # rank 3 is outside the top 2


def test_score_question_hit_flags_and_reciprocal_rank():
    hit = passage("doc_a", "Metro NCR | 36 hours | 2% penalty", 0.7)
    miss = passage("doc_b", "no", 0.8)
    scored = er.score_question(question(), [miss] * 5 + [hit], GOLD)  # first hit at rank 6
    assert scored["first_hit_rank"] == 6
    assert (scored["hit@1"], scored["hit@5"], scored["hit@10"]) == (False, False, True)
    assert scored["rr"] == 1 / 6
    assert scored["top1_score"] == 0.8 and scored["top1_document"] == "doc_b"
    first = er.score_question(question(), [hit, miss], GOLD)
    assert (first["hit@1"], first["hit@5"], first["hit@10"], first["rr"]) == (True, True, True, 1.0)
    none = er.score_question(question(), [miss], GOLD)
    assert none["first_hit_rank"] is None and none["rr"] == 0.0 and not none["hit@10"]
    empty = er.score_question(question(), [], GOLD)
    assert empty["top1_score"] is None and empty["rr"] == 0.0


def test_unanswerable_questions_carry_only_the_top_score():
    scored = er.score_question(question("unanswerable", []), [passage("doc_a", "x", 0.64)], set())
    assert scored == {"top1_score": 0.64, "top1_document": "doc_a"}


def test_percentile_interpolates_linearly():
    assert er.percentile([], 50) is None
    assert er.percentile([7.0], 90) == 7.0
    assert er.percentile([10.0, 20.0, 30.0, 40.0], 50) == 25.0
    assert er.percentile([40.0, 10.0, 30.0, 20.0], 90) == 37.0  # unsorted input; pos 2.7 -> 30 + 0.7 * 10
    assert er.percentile([1.0, 2.0, 3.0], 0) == 1.0 and er.percentile([1.0, 2.0, 3.0], 100) == 3.0


def row(category: str, corpus: str, rank: int | None, latency: float, score: float = 0.6) -> dict:
    base = {"corpus": corpus, "category": category, "latency_ms": latency, "top1_score": score}
    if category == "unanswerable":
        return base
    return {**base, "first_hit_rank": rank, **{f"hit@{k}": rank is not None and rank <= k for k in er.KS},
            "rr": 1.0 / rank if rank else 0.0}


def test_summary_counts_unanswerable_questions_out_of_the_hit_rates():
    rows = [row("table", "sample", 1, 100.0, 0.8), row("table", "sample", 4, 200.0, 0.7), row("fact", "upload", None, 300.0, 0.6),
            row("unanswerable", "sample", None, 400.0, 0.5)]
    s = er.summarise(rows)
    assert (s["n"], s["n_answerable"], s["n_unanswerable"]) == (4, 3, 1)
    assert s["hit@1"] == 1 / 3 and s["hit@5"] == 2 / 3 and s["hit@10"] == 2 / 3
    assert abs(s["mrr@10"] - (1.0 + 0.25 + 0.0) / 3) < 1e-12
    assert s["latency_p50_ms"] == 250.0  # over all four rows
    assert s["unanswerable_top1"]["n"] == 1 and s["unanswerable_top1"]["median"] == 0.5
    assert s["answerable_top1"]["n"] == 3 and abs(s["answerable_top1"]["mean"] - 0.7) < 1e-12


def test_summary_of_only_unanswerable_rows_has_no_hit_rates():
    s = er.summarise([row("unanswerable", "sample", None, 10.0)])
    assert s["hit@1"] is None and s["mrr@10"] is None and s["n_answerable"] == 0


def test_groups_cover_overall_corpus_and_category():
    rows = [row("table", "sample", 1, 1.0), row("fact", "upload", 2, 1.0), row("unanswerable", "upload", None, 1.0)]
    groups = er.summarise_groups(rows)
    assert list(groups) == ["overall", "corpus:sample", "corpus:upload", "category:fact", "category:table", "category:unanswerable"]
    assert groups["corpus:upload"]["n"] == 2 and groups["category:fact"]["hit@1"] == 0.0 and groups["category:fact"]["hit@5"] == 1.0
    assert "n/a" in er.format_table(groups)  # the unanswerable group has no hit rates, and says so


def test_extra_params_are_forwarded_only_when_given():
    class Args:
        mode = None
        scope = None

    sample, upload = question(), {**question(), "corpus": "upload", "dataset": "upload"}
    assert er.extra_params(Args, sample) == {}  # today's MCP accepts neither: the baseline passes nothing
    Args.mode = "hybrid"
    assert er.extra_params(Args, sample) == {"mode": "hybrid"}
    Args.scope = "dataset"
    assert er.extra_params(Args, sample) == {"mode": "hybrid", "scope": "dataset", "dataset": "public"}
    assert er.extra_params(Args, upload)["dataset"] == er.WORKSPACE_DATASET


def test_the_frozen_test_set_has_the_stated_shape():
    doc = json.loads(er.FIXTURE_PATH.read_text(encoding="utf-8"))
    qs = doc["questions"]
    assert len(qs) >= 105 and len({q["id"] for q in qs}) == len(qs)
    for q in qs:
        assert q["corpus"] in ("sample", "upload") and q["dataset"] in ("public", "olist", "upload")
        assert q["category"] in ("table", "definition", "fact", "paraphrase", "id", "cross_section", "unanswerable")
        if q["category"] == "unanswerable":
            assert q["gold_document"] is None and q["evidence"] == []
        else:
            assert q["gold_document"] and q["evidence"] and all(e.strip() for e in q["evidence"])
    mix = Counter((q["corpus"], q["category"]) for q in qs)
    assert mix[("sample", "unanswerable")] == 5 and mix[("upload", "unanswerable")] == 5
    assert sum(1 for q in qs if q["corpus"] == "upload" and q["category"] != "unanswerable") == 25
    assert sum(1 for q in qs if q["dataset"] == "olist") == 20  # the review-digest id probes


def test_compare_flags_different_test_sets():
    run = {"fixture_sha256": "aa", "summary": er.summarise_groups([row("table", "sample", 1, 1.0)])}
    assert "WARNING" not in er.format_compare("a", run, "b", run)
    assert "WARNING" in er.format_compare("a", run, "b", {**run, "fixture_sha256": "bb"})
