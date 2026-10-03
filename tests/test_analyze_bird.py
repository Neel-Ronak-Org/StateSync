"""
tests/test_analyze_bird.py
Protocol v2's analysis (scripts/analyze_bird.py; docs/research/evidence_tiers_study.md §5-§6).

WHY THIS EXISTS. The primary comparison puts a yes/no check and a continuous judge on the same
footing: wrong answers caught at the check's own flag rate, with the judge's ties broken at random.
That expectation, the risk-coverage numbers and the exact binomial tools are computed in closed
form, so each is pinned against a brute-force average over every tie-breaking order, or a known
value. Holm and the bootstrap must be deterministic for a fixed seed.
"""

from __future__ import annotations

import itertools
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import analyze_bird as ab  # noqa: E402

RISK = [0.9, 0.9, 0.9, 0.5, 0.1, 0.1]
WRONG = [True, False, False, True, False, True]


def _orders(risk, descending):
    """Every order consistent with the risks (ties in any order)."""
    idx = list(range(len(risk)))
    for perm in itertools.permutations(idx):
        r = [risk[i] for i in perm]
        if all((a >= b) if descending else (a <= b) for a, b in zip(r, r[1:])):
            yield perm


def test_matched_k_is_the_average_over_tie_orders():
    for k in range(1, 7):
        brute = [sum(WRONG[i] for i in perm[:k]) for perm in _orders(RISK, descending=True)]
        assert abs(ab.matched_k_caught(RISK, WRONG, k) - sum(brute) / len(brute)) < 1e-9


def test_aurc_and_selective_accuracy_are_the_average_over_tie_orders():
    curves = []
    for perm in _orders(RISK, descending=False):
        w = [WRONG[i] for i in perm]
        curves.append([sum(w[:m]) / m for m in range(1, 7)])
    brute_aurc = sum(sum(c) / 6 for c in curves) / len(curves)
    assert abs(ab.aurc(RISK, WRONG) - brute_aurc) < 1e-9
    m = round(0.8 * 6)
    brute_sel = sum(1 - c[m - 1] for c in curves) / len(curves)
    assert abs(ab.selective_accuracy(RISK, WRONG, 0.8) - brute_sel) < 1e-9


def test_a_perfect_ranking_has_the_lowest_aurc():
    wrong = [False, False, True, True]
    assert ab.aurc([0.1, 0.2, 0.8, 0.9], wrong) < ab.aurc([0.5] * 4, wrong) < ab.aurc([0.9, 0.8, 0.2, 0.1], wrong)


def test_auroc_of_a_flag_is_half_plus_half_the_gap():
    flags = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0]
    wrong = [True, True, True, False, False, False]
    recall, false_alarm = 1 / 3, 1 / 3
    assert abs(ab.auroc(flags, wrong) - (0.5 + (recall - false_alarm) / 2)) < 1e-9


def test_binomial_tools():
    assert abs(ab.cp_upper(0, 59) - (1 - 0.05 ** (1 / 59))) < 1e-6
    assert ab.cp_upper(5, 5) == 1.0 and ab.cp_upper(0, 0) == 1.0
    assert abs(ab.sign_test_p(5, 0) - 1 / 32) < 1e-12 and ab.sign_test_p(0, 0) == 1.0
    assert abs(ab.binom_cdf(10, 10, 0.3) - 1.0) < 1e-12


def test_holm_steps_down():
    assert ab.holm({"H1": 0.01, "H2": 0.03, "H3": 0.04}) == {"H1": True, "H2": False, "H3": False}
    assert ab.holm({"H1": 0.01, "H2": 0.02, "H3": 0.04}) == {"H1": True, "H2": True, "H3": True}


def test_bootstrap_is_stratified_and_reproducible():
    strata = ["a", "a", "b", "b", "b"]
    seen = []
    ab.stratified_bootstrap(strata, lambda idx: seen.append(sorted(idx)) or 0.0, resamples=50)
    assert all(sum(i < 2 for i in s) == 2 and sum(i >= 2 for i in s) == 3 for s in seen)
    one = ab.stratified_bootstrap(strata, lambda idx: float(sum(idx)), resamples=20)
    assert one == ab.stratified_bootstrap(strata, lambda idx: float(sum(idx)), resamples=20)


def test_h4_is_untestable_below_its_precondition():
    rows = [{"question_id": i, "db_id": "d", "correct": True, "answer_correct_before": True, "answer_correct_after": True,
             "signals": {"repair_triggered": i < 3}} for i in range(20)]
    out = ab.analyse(rows, judges={})
    assert out["tests"]["H4"]["status"].startswith("untestable") and "H4" not in out["holm"]["pvalues"]
