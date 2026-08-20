"""
tests/test_grounding_scorer.py
M6's first test. Before this file, `scripts/bench_grounding.py` had zero
test coverage -- audit 003 F-003-6: E1-V3's own pass condition is a
requirement on the scoring script ("an ungrounded claim is a failure
regardless of whether it is factually true -- the scoring script must
enforce this rather than crediting lucky accuracy"), and nothing asserted
it. `verify_fv3.sh` records "E1-V3 (M6) PASS" on exit code alone, and
`bench_grounding.py` cannot exit non-zero for any measured outcome.

WHY THIS EXISTS, per finding. Audit 003 opened the RAG half three cycles had
named as an unexamined blind spot (docs/cycles/000.md §3.2, docs/cycles/002.md
§1) and found three of the four published M6 components measuring something
other than their name:

  F-003-1  the faithfulness judge was never shown the SQL rows a hybrid
           answer's synthesizer prompt actually included, while being told
           document passages were "what the system actually saw"
  F-003-2  _build_reference() embedded expected_row_count as "row_count=N"
           and called it "database ground truth" -- a row COUNT, not an
           answer value; three demonstrably true claims were scored
           factually incorrect against it
  F-003-3  total_ungrounded_but_true was an unvalidated integer the judge
           typed once, disagreeing with its own itemised claims array on
           2 of 16 queries in the published run (4 reported, 2 supported)
  F-003-4  context precision divides by CITED chunks (capped at
           citation_max_chunks=3), not retrieved context; with 2
           expected_chunk_ids per fixture entry the doc-route ceiling is
           0.667, and 7 of 8 doc queries sit exactly on it WITH RECALL 1.0

These tests pin the REPAIRS for F-003-3 and F-003-4 with pure-function unit
tests (no LLM call needed -- the defects were arithmetic on the judge's
output, not the judging itself), and pin the cross-check added for F-003-6's
"must enforce" condition. F-003-1 and F-003-2 require a live judge/MCP call
respectively and are exercised by the M1/M2 paired bench_grounding.py runs
(docs/cycles/003.md) instead of here.

None of this loosens or re-tunes the original faithfulness.json scoring --
`test_faithfulness_score_untouched_by_repair` pins that byte-for-byte.

Run from the repo root: pytest tests/test_grounding_scorer.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from bench_grounding import (  # noqa: E402
    _context_precision,
    _recount_ungrounded_but_true,
    _recompute_faithfulness_from_claims,
)


# ── F-003-3: ungrounded_but_true recomputed from the claims array ──────────

def test_ubt_recount_matches_when_judge_agrees_with_itself():
    claims = [
        {"claim": "a", "grounded_in_retrieved_context": True, "factually_correct": True},
        {"claim": "b", "grounded_in_retrieved_context": False, "factually_correct": True},
    ]
    assert _recount_ungrounded_but_true(claims) == 1


def test_ubt_recount_ignores_the_judges_separate_self_reported_field():
    """
    The published run's actual failure shape (audit 003 F-003-3,
    e1_hybrid_001/e1_hybrid_002): the judge's free-typed integer said more
    than its own claims array supports. The recount must be driven only by
    the claims array, never by a second number the judge also typed.
    """
    claims = [
        {"claim": "only claim", "grounded_in_retrieved_context": True, "factually_correct": True},
    ]
    # A judge that (incorrectly) self-reports 1 ungrounded-but-true claim
    # despite its own claims array containing none -- recount must not
    # trust that number.
    assert _recount_ungrounded_but_true(claims) == 0


def test_ubt_recount_does_not_count_ungrounded_but_false_claims():
    claims = [
        {"claim": "false and ungrounded", "grounded_in_retrieved_context": False,
         "factually_correct": False},
    ]
    assert _recount_ungrounded_but_true(claims) == 0


def test_ubt_recount_does_not_credit_a_claim_missing_factually_correct():
    """A 7B judge does not always populate every required field. A claim
    with no factually_correct verdict is not a CONFIRMED true claim, so it
    must not count toward ungrounded-but-true."""
    claims = [
        {"claim": "unverified", "grounded_in_retrieved_context": False},
    ]
    assert _recount_ungrounded_but_true(claims) == 0


def test_ubt_recount_reproduces_the_published_runs_correction():
    """The published report's own two disagreeing queries, reconstructed:
    e1_hybrid_002 had one claim tagged factually_correct=False (because the
    reference read "row_count=1") while still being counted as one
    ungrounded-but-true by the judge's self-report -- recount must give 0,
    not 1."""
    e1_hybrid_002_claims = [
        {"claim": "Seller S014 has had a total of 2 disputes.",
         "grounded_in_retrieved_context": False, "factually_correct": False},
    ]
    assert _recount_ungrounded_but_true(e1_hybrid_002_claims) == 0


# ── F-003-6: "must enforce -- rather than crediting lucky accuracy" ────────

def test_faithfulness_recompute_enforces_grounding_not_lucky_accuracy():
    """
    E1-V3's pass condition verbatim: an ungrounded claim is a failure
    REGARDLESS of whether it is factually true. An implementation that
    accidentally credited factually_correct instead of
    grounded_in_retrieved_context would score this claims list 1.0 (both
    claims are factually_correct). The rubric's actual definition scores
    it 0.5 (only one of the two is grounded) -- this pins that the
    cross-check enforces the rubric's stated definition, not the wrong one.
    """
    claims = [
        {"claim": "grounded and true", "grounded_in_retrieved_context": True,
         "factually_correct": True},
        {"claim": "ungrounded but true -- the lucky-accuracy trap",
         "grounded_in_retrieved_context": False, "factually_correct": True},
    ]
    assert _recompute_faithfulness_from_claims(claims) == 0.5
    # The wrong (crediting-lucky-accuracy) implementation this guards against:
    wrong_impl = sum(1 for c in claims if c["factually_correct"]) / len(claims)
    assert wrong_impl == 1.0
    assert _recompute_faithfulness_from_claims(claims) != wrong_impl


def test_faithfulness_recompute_vacuously_faithful_on_zero_claims():
    """Matches the rubric's own stated convention (faithfulness.json
    scoring.faithfulness_score): "An answer with zero claims scores 1.0"."""
    assert _recompute_faithfulness_from_claims([]) == 1.0


def test_faithfulness_recompute_all_grounded_scores_one():
    claims = [
        {"claim": "a", "grounded_in_retrieved_context": True, "factually_correct": True},
        {"claim": "b", "grounded_in_retrieved_context": True, "factually_correct": False},
    ]
    assert _recompute_faithfulness_from_claims(claims) == 1.0


def test_faithfulness_recompute_all_ungrounded_scores_zero():
    claims = [
        {"claim": "a", "grounded_in_retrieved_context": False, "factually_correct": True},
        {"claim": "b", "grounded_in_retrieved_context": False, "factually_correct": False},
    ]
    assert _recompute_faithfulness_from_claims(claims) == 0.0


# ── F-003-4: context precision over cited vs. retrieved, and the ceiling ──

def test_precision_over_cited_and_over_retrieved_differ_when_citations_are_capped():
    """
    Reproduces the published run's own shape (e1_doc_001-family): 2 expected
    chunks, 3 cited (citation_max_chunks), many more retrieved. Over cited,
    precision is 2/3. Over retrieved, it is far lower -- this is the gap
    V2_STRATEGY.md's "~38% of retrieved context is irrelevant" gloss
    described despite being computed from the cited figure.
    """
    expected = ["doc_a_c000", "doc_a_c001"]
    citations = [
        {"kind": "chunk", "ref": "doc_a_c000"},
        {"kind": "chunk", "ref": "doc_a_c001"},
        {"kind": "chunk", "ref": "doc_b_c000"},  # the 3rd, capped-in citation
    ]
    # 14 retrieved total (matches the published run's doc-route retrieval
    # count), only the 2 expected among them.
    retrievals = [{"provenance": {"chunk_id": "doc_a_c000"}},
                  {"provenance": {"chunk_id": "doc_a_c001"}}]
    retrievals += [{"provenance": {"chunk_id": f"doc_noise_c{i:03d}"}} for i in range(12)]

    result = _context_precision(expected, citations, retrievals)
    assert result["precision"] == pytest.approx(2 / 3)
    assert result["precision_over_retrieved"] == pytest.approx(2 / 14)
    assert result["precision"] != result["precision_over_retrieved"]


def test_precision_ceiling_is_visible_even_at_perfect_recall():
    """
    The published run's actual shape: recall 1.0 (both expected chunks
    retrieved AND cited) but precision capped below 1.0 because a third,
    unrelated chunk was also cited -- citation_max_chunks=3 with 2 expected
    puts a hard 0.667 ceiling under a query, however good retrieval is.
    max_attainable_precision makes that ceiling visible per query rather
    than reading as a retrieval failure.
    """
    expected = ["doc_a_c000", "doc_a_c001"]
    citations = [
        {"kind": "chunk", "ref": "doc_a_c000"},
        {"kind": "chunk", "ref": "doc_a_c001"},
        {"kind": "chunk", "ref": "doc_unrelated_c000"},
    ]
    retrievals = [{"provenance": {"chunk_id": "doc_a_c000"}},
                  {"provenance": {"chunk_id": "doc_a_c001"}}]

    result = _context_precision(expected, citations, retrievals)
    assert result["recall"] == 1.0
    assert result["precision"] == pytest.approx(2 / 3)
    assert result["max_attainable_precision"] == pytest.approx(2 / 3)
    assert result["precision"] < 1.0


def test_precision_is_none_when_no_expected_chunks_by_design():
    """e1_hybrid_001 -- expected sellers depend on live data by design, per
    the fixture generator's own note; must stay excluded (n=15, not 16)."""
    result = _context_precision([], [{"kind": "chunk", "ref": "x"}], [])
    assert result["precision"] is None
    assert result["recall"] is None
    assert result["precision_over_retrieved"] is None
    assert result["max_attainable_precision"] is None


def test_precision_over_retrieved_zero_when_nothing_retrieved_matches():
    expected = ["doc_a_c000"]
    citations = [{"kind": "chunk", "ref": "doc_b_c000"}]
    retrievals = [{"provenance": {"chunk_id": "doc_b_c000"}}]
    result = _context_precision(expected, citations, retrievals)
    assert result["precision"] == 0.0
    assert result["precision_over_retrieved"] == 0.0


# ── A4's non-negotiable: faithfulness.json's own scoring is untouched ─────

def test_faithfulness_score_untouched_by_repair():
    """
    A4's acceptance criterion, verbatim: "faithfulness_score ... byte-
    identical to their pre-cycle state ... the scoring expression being
    untouched." _judge_query must still assign faithfulness_score directly
    from the judge's own parsed field -- the recompute cross-check
    (_recompute_faithfulness_from_claims) is additive, under a different
    key, and must never be what faithfulness_score is set from.
    """
    import inspect
    from bench_grounding import _judge_query
    src = inspect.getsource(_judge_query)
    assert '"faithfulness_score": judge_faithfulness' in src
    assert "judge_faithfulness = float(parsed.get(\"faithfulness_score\"" in src


def test_rubric_v1_file_is_unmodified():
    """The rubric this cycle must not edit, checked by content rather than
    by trusting the git history: same fields, same scoring description."""
    import json
    rubric = json.loads((REPO_ROOT / "fixtures" / "rubrics" / "faithfulness.json").read_text())
    assert rubric["rubric_name"] == "faithfulness"
    assert "crediting lucky accuracy" in rubric["description"]
    assert rubric["scoring"]["faithfulness_score"].startswith(
        "fraction of claims with grounded_in_retrieved_context = true")
