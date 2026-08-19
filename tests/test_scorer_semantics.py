"""
tests/test_scorer_semantics.py
Pins the semantics of the three result-comparison definitions this project
reports numbers under, so PROJECT_DIRECTION §3's table is enforced by code
rather than asserted in prose.

WHY THIS EXISTS. §3 states that three definitions coexist deliberately and
answer different questions:

    execution           (column_NAME, value) pairs -- alias-sensitive
    C2-V8 signature     ignores names, includes the column COUNT
    execution_accuracy  result sets as value tuples -- the published
                        BIRD/Spider definition

Nothing checked any of that. `execution_accuracy` was registered in both
registries by 124bf8e and then invoked by nothing: it is absent from
config/statesync.toml's `evaluation.scorers` (["spec", "execution"]), so the
evaluator pipeline never calls it, and verify_seams only asserts that names
RESOLVE. A scorer that no committed check ever executes is a scorer whose
behaviour is a claim.

That gap had already cost something. diagnose_m4.py carried its own second
implementation of "standard EX" which sorted values WITHIN each row -- so a
generated row (5, 3) matched a ground-truth row (3, 5) -- and every published
"standard EX (BIRD/Spider definition)" figure came from it rather than from the
registered scorer (f46e0bf). Two implementations of one named concept, silently
disagreeing, in one repo.

These tests do NOT loosen or re-tune anything. They assert the definitions are
what the documents say, INCLUDING the places where the strictness is
deliberate and inconvenient (empty-vs-empty differing under C2-V8; alias
sensitivity in `execution`). If a future change relaxes a scorer to make a
number look better, these fail.

Run from the repo root: pytest tests/test_scorer_semantics.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))

from scorers import (  # noqa: E402
    ExecutionAccuracyScorer,
    ExecutionScorer,
    get_scorer,
    registered_scorer_names,
)


def c2v8_signature(columns, rows):
    """check_c2v8.py::_signature, copied verbatim in shape -- the historical M4
    definition. Column COUNT participates; values are sorted WITHIN a row."""
    row_tuples = [
        tuple(sorted(json.dumps(v, sort_keys=True, default=str) for v in row.values()))
        for row in rows
    ]
    return json.dumps({"n_columns": len(columns), "rows": sorted(row_tuples)}, default=str)


# ── The registry itself ───────────────────────────────────────────────────────

def test_all_four_scorers_are_registered():
    assert set(registered_scorer_names()) == {
        "spec", "execution", "execution_accuracy", "vision"
    }


def test_unresolvable_name_raises_rather_than_defaulting():
    """A8: an unresolvable name is a startup failure, not a silent default."""
    with pytest.raises(RuntimeError):
        get_scorer("no_such_scorer")


# ── execution_accuracy: the published BIRD/Spider definition ─────────────────

def test_execution_accuracy_ignores_column_names():
    """Independently authored SQL aliases differ; the values are the answer."""
    scorer = ExecutionAccuracyScorer()
    gen = [{"total": 5}, {"total": 3}]
    ref = [{"sum_value": 5}, {"sum_value": 3}]
    assert scorer.score(gen, ref)["match"] is True


def test_execution_accuracy_is_order_insensitive_across_rows():
    """SQL result order is unspecified without ORDER BY."""
    scorer = ExecutionAccuracyScorer()
    assert scorer.score([{"a": 1}, {"a": 2}], [{"a": 2}, {"a": 1}])["match"] is True


def test_execution_accuracy_RESPECTS_column_order_within_a_row():
    """
    THE REGRESSION GUARD. BIRD/Spider compares each row as a tuple in column
    order. diagnose_m4.py's since-deleted private helper sorted values within
    the row, so these two scored EQUAL and every published standard-EX figure
    came from that looser definition.
    """
    scorer = ExecutionAccuracyScorer()
    gen = [{"x": 5, "y": 3}]
    ref = [{"x": 3, "y": 5}]
    assert scorer.score(gen, ref)["match"] is False

    # ... and the legacy definition really did call them equal, which is why
    # the distinction is worth a test rather than a comment.
    legacy = sorted(tuple(sorted(str(v) for v in row.values())) for row in gen)
    legacy_ref = sorted(tuple(sorted(str(v) for v in row.values())) for row in ref)
    assert legacy == legacy_ref


def test_execution_accuracy_treats_two_empty_results_as_equal():
    """Where it diverges from C2-V8: an empty answer is an answer."""
    assert ExecutionAccuracyScorer().score([], [])["match"] is True


def test_execution_accuracy_is_a_multiset_not_a_set():
    """Duplicate rows are meaningful in a SQL result; sorted(), not set()."""
    scorer = ExecutionAccuracyScorer()
    assert scorer.score([{"a": 1}, {"a": 1}], [{"a": 1}])["match"] is False


def test_execution_accuracy_reports_row_recall_so_a_near_miss_stays_visible():
    out = ExecutionAccuracyScorer().score([{"a": 1}], [{"a": 1}, {"a": 2}])
    assert out["match"] is False
    assert out["row_recall"] == pytest.approx(0.5)


def test_execution_accuracy_row_recall_is_a_multiset_not_a_set():
    """
    Audit 001 F-001-5. `match` (two tests above) already treats duplicate rows
    as meaningful -- sorted(), not set(). row_recall used to disagree with its
    own neighbour: it de-duplicated via `set(ref) & set(gen)`, so a reference
    containing the same row twice could have that row recalled ONCE and be
    reported as if both copies were recovered.

    reference has ("a", 1) TWICE and ("a", 2) once -- 3 rows, one duplicated
    value. generated recovers ("a", 1) once.

      SET semantics (the bug):    1 distinct value in common / 2 distinct
                                   values in ref = 0.5
      MULTISET semantics (fixed): min(1 in gen, 2 in ref) = 1 row recovered,
                                   with multiplicity / 3 rows in ref = 1/3

    0.5 here would mean the fix regressed back to the set-based definition.
    Measured before this test was written: 0 of 237 previously-scored queries
    across every committed docs/m4_diagnosis*.json report contain a duplicate
    reference row, so the fix this test pins moved no published number --
    verified by recomputing both definitions over every committed report, not
    inferred from this synthetic case.
    """
    ref = [{"a": 1}, {"a": 1}, {"a": 2}]
    gen = [{"a": 1}]
    out = ExecutionAccuracyScorer().score(gen, ref)
    assert out["match"] is False
    assert out["row_recall"] == pytest.approx(1 / 3)


# ── execution: deliberately stricter, alias-sensitive ────────────────────────

def test_execution_scorer_is_alias_sensitive_and_that_is_deliberate():
    """The difference from execution_accuracy, pinned. §3 calls this the
    strictest internal check; if it ever stops being stricter, §3 is wrong."""
    gen, ref = [{"total": 5}], [{"sum_value": 5}]
    assert ExecutionScorer().score(gen, ref)["match"] is False
    assert ExecutionAccuracyScorer().score(gen, ref)["match"] is True


# ── C2-V8 signature: counts columns, even on an empty result ─────────────────

def test_c2v8_distinguishes_empty_results_with_different_projections():
    """
    M4's known strictness, asserted rather than described. c2g_10 is the worked
    example in limitations.md: semantically correct SQL, the expected 0 rows,
    scored 0 because it projected 4 columns where the reference projected 2.
    Deliberately NOT loosened (A0.4) -- so it is pinned here, and the standard
    EX scorer above is what gets quoted against BIRD.
    """
    four_cols = c2v8_signature(["a", "b", "c", "d"], [])
    two_cols = c2v8_signature(["a", "b"], [])
    assert four_cols != two_cols
    assert ExecutionAccuracyScorer().score([], [])["match"] is True


def test_c2v8_ignores_column_order_within_a_row_by_design():
    """The within-row sort is correct HERE (and wrong for BIRD) -- that is the
    whole reason two definitions exist instead of one."""
    assert (c2v8_signature(["x", "y"], [{"x": 5, "y": 3}])
            == c2v8_signature(["y", "x"], [{"y": 3, "x": 5}]))


# ── The three definitions must not silently converge ────────────────────────

def test_the_three_definitions_genuinely_disagree_on_one_case_each():
    """
    If a future edit collapses them, the separate reporting §3 mandates becomes
    theatre. One discriminating case per pair.
    """
    alias_gen, alias_ref = [{"total": 5}], [{"sum": 5}]
    assert ExecutionScorer().score(alias_gen, alias_ref)["match"] is False
    assert ExecutionAccuracyScorer().score(alias_gen, alias_ref)["match"] is True

    assert c2v8_signature(["a", "b"], []) != c2v8_signature(["a"], [])
    assert ExecutionAccuracyScorer().score([], [])["match"] is True

    swapped_gen, swapped_ref = [{"x": 5, "y": 3}], [{"x": 3, "y": 5}]
    assert ExecutionAccuracyScorer().score(swapped_gen, swapped_ref)["match"] is False
    assert (c2v8_signature(["x", "y"], swapped_gen)
            == c2v8_signature(["x", "y"], swapped_ref))
