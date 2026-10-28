"""
tests/test_eval_financebench.py
Pins the pure scoring logic of scripts/eval_financebench.py, the harness that measures the
production MCP search path on the 150 FinanceBench questions (docs/specs/external_eval.md).

WHY THIS EXISTS. The headline of the external check is a comparison of page hit@5 and MRR@10
between configurations, so the arithmetic must be right and must not move. Three things are
easy to get wrong and would silently shift every number: the +1 between FinanceBench's 0-based
page numbers and StateSync's 1-based ones, the page+-1 widening (a secondary measure fixed
before any result), and the accounting of filings the product refused to ingest (a miss in
`overall`, out of `ingested`). The docker and HTTP parts are exercised by running the harness
against the live stack.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import eval_financebench as fb  # noqa: E402

NAME_BY_ID = {"up_a": "ACME_2022_10K", "up_b": "ACME_2021_10K"}


def question(doc: str = "ACME_2022_10K", pages: tuple[int, ...] = (59,), qtype: str = "metrics-generated", doc_type: str = "10k") -> dict:
    return {"financebench_id": "q", "doc_name": doc, "question_type": qtype, "doc_type": doc_type,
            "evidence": [{"doc_name": doc, "evidence_page_num": p, "evidence_text": "x"} for p in pages]}


def passage(doc_id: str, page: int | None, score: float = 0.5) -> dict:
    return {"document_id": doc_id, "page": page, "score": score}


def test_gold_page_is_the_zero_based_evidence_page_plus_one():
    """FinanceBench counts pages from 0 and StateSync from 1; forgetting the +1 makes every hit a miss
    (or, worse, a hit on the page before)."""
    assert fb.gold_pages(question(pages=(59,))) == {("ACME_2022_10K", 60)}
    assert fb.gold_pages(question(pages=(0, 4))) == {("ACME_2022_10K", 1), ("ACME_2022_10K", 5)}  # page 0 is page 1


def test_page_hit_needs_the_gold_filing_and_the_gold_page():
    """The same page number in another filing of the same company is not a hit; the page one before the
    gold page (the 0-based number used as if it were 1-based) is not a hit either."""
    gold = fb.gold_pages(question(pages=(59,)))
    assert fb.page_hit("ACME_2022_10K", 60, gold)
    assert not fb.page_hit("ACME_2022_10K", 59, gold)  # the un-offset number
    assert not fb.page_hit("ACME_2021_10K", 60, gold)  # right page, wrong filing
    assert not fb.page_hit(None, 60, gold)  # a passage from the demo corpus maps to no filing
    assert not fb.page_hit("ACME_2022_10K", None, gold)  # no page, no page hit


def test_page_within_one_is_a_secondary_hit_only():
    """A table often continues onto the next page, so page+-1 is reported separately. It must widen by
    exactly one page and never turn a strict miss into a strict hit."""
    gold = fb.gold_pages(question(pages=(59,)))
    assert fb.page_hit("ACME_2022_10K", 59, gold, tolerance=1) and fb.page_hit("ACME_2022_10K", 61, gold, tolerance=1)
    assert not fb.page_hit("ACME_2022_10K", 62, gold, tolerance=1)
    assert not fb.page_hit("ACME_2021_10K", 60, gold, tolerance=1)  # still the same filing


def test_score_question_ranks_hits_and_reciprocal_rank():
    """hit@k and MRR count the FIRST page hit; the page+-1 and document hits are scored on the same passages."""
    q = question(pages=(59,))
    passages = [passage("up_b", 60), passage("up_a", 10), passage("up_a", 59), passage("up_a", 60)]
    scored = fb.score_question(q, passages, NAME_BY_ID, ingested=True)
    assert scored["first_page_rank"] == 4  # the first two are the wrong filing / page, the third is the page before
    assert (scored["page_hit@1"], scored["page_hit@5"], scored["page_hit@10"]) == (False, True, True)
    assert scored["rr"] == 1 / 4
    assert scored["first_page1_rank"] == 3 and scored["page1_hit@5"] and not scored["page1_hit@1"]
    assert scored["first_doc_rank"] == 2 and scored["doc_hit@5"] and not scored["doc_hit@1"]


def test_only_the_top_ten_passages_count():
    """A hit at rank 11 is not a hit: the harness reports the top 10 only."""
    passages = [passage("up_b", 1)] * 10 + [passage("up_a", 60)]
    scored = fb.score_question(question(pages=(59,)), passages, NAME_BY_ID, ingested=True)
    assert scored["first_page_rank"] is None and not scored["page_hit@10"] and scored["rr"] == 0.0


def test_a_question_on_a_refused_filing_is_a_miss_even_if_a_passage_matches():
    """The product cannot answer a question about a filing it refused to ingest, so it is a miss by
    definition and carries gold_ingested False; a stray passage cannot rescue it."""
    scored = fb.score_question(question(pages=(59,)), [passage("up_a", 60)], NAME_BY_ID, ingested=False)
    assert scored["gold_ingested"] is False
    assert not any(scored[f"{kind}_hit@{k}"] for kind in ("page", "page1", "doc") for k in fb.KS)
    assert scored["rr"] == 0.0


def row(doc_type: str, qtype: str, rank: int | None, ingested: bool = True, latency: float = 100.0) -> dict:
    scored = fb.score_question(question(doc_type=doc_type, qtype=qtype, pages=(59,)), [passage("up_b", 1)] * ((rank or 11) - 1) + [passage("up_a", 60)],
                               NAME_BY_ID, ingested)
    return {"doc_type": doc_type, "question_type": qtype, "latency_ms": latency, **{k: v for k, v in scored.items() if k not in ("names", "flags")}}


def test_summary_keeps_refused_questions_in_overall_and_out_of_ingested():
    """Both denominators are reported: with 3 answerable-and-ingested questions (hits at rank 1, 4 and none)
    and 1 refused one, overall is over 4 and ingested over 3, and the refused group is all misses."""
    rows = [row("10k", "metrics-generated", 1), row("10k", "domain-relevant", 4), row("10q", "novel-generated", None),
            row("10k", "novel-generated", 1, ingested=False)]
    groups = fb.summarise_groups(rows)
    assert (groups["overall"]["n"], groups["overall"]["n_ingested"]) == (4, 3)
    assert groups["overall"]["page_hit@1"] == 1 / 4 and groups["overall"]["page_hit@5"] == 2 / 4
    assert abs(groups["overall"]["mrr@10"] - (1.0 + 0.25) / 4) < 1e-12
    assert (groups["ingested"]["n"], groups["ingested"]["n_ingested"]) == (3, 3)
    assert groups["ingested"]["page_hit@5"] == 2 / 3 and abs(groups["ingested"]["mrr@10"] - (1.0 + 0.25) / 3) < 1e-12
    assert (groups["refused"]["n"], groups["refused"]["n_ingested"]) == (1, 0)
    assert groups["refused"]["page_hit@10"] == 0.0 and groups["refused"]["mrr@10"] == 0.0


def test_groups_split_by_question_type_and_doc_type_over_both_denominators():
    """Per-type tables exist over all questions and over the ingested ones only, so a type whose filings were
    mostly refused is not mistaken for a retrieval failure."""
    rows = [row("10k", "metrics-generated", 1), row("10q", "metrics-generated", None), row("10k", "domain-relevant", 2, ingested=False)]
    groups = fb.summarise_groups(rows)
    assert groups["question_type:metrics-generated"]["n"] == 2 and groups["ingested/question_type:metrics-generated"]["n"] == 2
    assert groups["question_type:domain-relevant"]["n"] == 1 and "ingested/question_type:domain-relevant" not in groups
    assert groups["doc_type:10k"]["n"] == 2 and groups["ingested/doc_type:10k"]["n"] == 1
    assert groups["ingested/doc_type:10k"]["page_hit@1"] == 1.0
    assert "n/a" in fb.format_table({"refused": fb.summarise([])})  # an empty group says n/a rather than dividing by zero


def test_ingest_summary_counts_refusals_failures_and_chunks():
    """The ingest record's totals: a refused upload is not a failed ingest, and only ready filings
    contribute chunks and pages."""
    docs = [
        {"doc_name": "A", "http_status": 202, "status": "ready", "pages": 10, "chunk_count": 30, "ready_seconds": 12.0},
        {"doc_name": "B", "http_status": 422, "refusal": '{"detail": "too many pages"}', "ready_seconds": 0.5},
        {"doc_name": "C", "http_status": 202, "status": "failed", "pages": 5, "chunk_count": 0, "ready_seconds": 7.0},
        {"doc_name": "D", "http_status": 202, "status": "ready", "pages": 20, "chunk_count": 55, "ready_seconds": 20.0,
         "layout_fallback": {"lines": 2, "first_page": 138}},
    ]
    s = fb.summarise_ingest(docs)
    assert (s["filings"], s["ready"], s["refused"], s["failed_or_stuck"]) == (4, 2, 1, 1)
    assert s["refused_filings"] == ["B"] and s["failed_filings"] == ["C"]
    assert (s["chunks"], s["pages_ready"], s["seconds_upload_to_ready"]) == (85, 30, 39.5)
    assert s["layout_fallback_filings"] == ["D"]


def test_refusal_is_recorded_exactly_as_the_api_returned_it():
    assert fb.refusal_for(202, {"document": {}}) is None
    assert fb.refusal_for(422, {"detail": "The PDF has 549 pages; at most 300 are accepted."}) == '{"detail": "The PDF has 549 pages; at most 300 are accepted."}'
    assert fb.refusal_for(None, "TimeoutError: timed out") == "TimeoutError: timed out"


def test_layout_fallback_is_read_from_the_parser_log():
    """The only evidence that a filing's layout pass fell back to flat pages is the parser's WARNING lines;
    the first page named is where it stopped. No such line means no fallback was logged."""
    assert fb.layout_fallback(["2026-10-01 19:07:13,947 INFO parsed application/pdf: 10 bytes, 9 pages, 88 blocks"]) is None
    lines = ["2026-10-01 19:07:13,900 WARNING layout pass out of time at page 138; the rest is flat text",
             "2026-10-01 19:07:13,947 WARNING page 139 layout could not be read; flat text used"]
    assert fb.layout_fallback(lines) == {"lines": 2, "first_page": 138}
    assert fb.layout_fallback(["2026-10-01 19:07:13,947 WARNING layout pass failed (MemoryError); flat text used"]) == {"lines": 1, "first_page": None}


def test_compare_warns_when_the_question_files_differ():
    summary = {"overall": fb.summarise([])}
    a, b = {"fixture_sha256": "x", "summary": summary}, {"fixture_sha256": "y", "summary": summary}
    assert "WARNING" in fb.format_compare("a", a, "b", b)
    assert "WARNING" not in fb.format_compare("a", a, "b", a)
