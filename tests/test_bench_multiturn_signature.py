"""
tests/test_bench_multiturn_signature.py
Pins signature generation and float normalization logic in multi-turn benchmarking.

WHY THIS EXISTS.

During cycle 007 benchmark evaluations, multi-turn conversational benchmark tests
against Postgres ground truth suffered from false-failure regressions:

1. Aggregation precision mismatch: Ground truth queries frequently format numbers
   using `ROUND(AVG(...), 2)` for visual clarity, while generated candidate queries
   compute raw `AVG(...)` without rounding. Postgres returns full unrounded precision
   for candidate queries (e.g. 19.9903199289835775 vs 19.99). An un-normalized comparison
   scores mathematically correct answers as benchmark failures.
2. Column aliasing and row ordering: SQL solutions that produce the exact same data
   often vary in arbitrary column aliases (`cnt` vs `order_count`) or row ordering
   when `ORDER BY` is not strictly specified.

The `_sig` comparison signature builder normalizes float values via `_round_floats`,
orders row representations, and structures signatures based on row values and column
count rather than surface-level syntax differences, while still strictly discriminating
genuinely different query results.

This suite pins float rounding, type stability, invariance under precision/alias/order
variations, and dataset discrimination.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from bench_multiturn import (
    _round_floats,
    _sig,
)


def test_round_floats_rounds_high_precision_float_to_two_decimal_places():
    assert _round_floats(19.9903199289835775) == 19.99
    assert _round_floats(12.3456) == 12.35


def test_round_floats_preserves_integers_as_int_type():
    result = _round_floats(42)
    assert result == 42
    assert isinstance(result, int)
    assert not isinstance(result, float)


def test_round_floats_passes_through_strings_none_and_booleans_unchanged():
    assert _round_floats("string_value") == "string_value"
    assert _round_floats(None) is None
    assert _round_floats(True) is True
    assert _round_floats(False) is False
    assert isinstance(_round_floats(True), bool)


def test_sig_avg_precision_insensitivity_matches_rounded_and_unrounded_averages():
    """
    Guards against false failures where ground truth uses ROUND(AVG(...), 2)
    for readability while generated SQL computes raw AVG(...) returning full
    Postgres floating point precision (19.9903199289835775 vs 19.99).
    """
    cols_gt = ["avg_amount"]
    rows_gt = [{"avg_amount": 19.99}]

    cols_gen = ["avg_amount"]
    rows_gen = [{"avg_amount": 19.9903199289835775}]

    assert _sig(cols_gt, rows_gt) == _sig(cols_gen, rows_gen)


def test_sig_alias_insensitivity_matches_different_column_names_with_same_count():
    cols_a = ["cnt"]
    rows_a = [{"cnt": 5}]

    cols_b = ["order_count"]
    rows_b = [{"order_count": 5}]

    assert _sig(cols_a, rows_a) == _sig(cols_b, rows_b)


def test_sig_row_order_insensitivity_matches_differently_ordered_result_sets():
    cols = ["id", "status"]
    rows_forward = [
        {"id": 1, "status": "completed"},
        {"id": 2, "status": "pending"},
    ]
    rows_reversed = [
        {"id": 2, "status": "pending"},
        {"id": 1, "status": "completed"},
    ]
    assert _sig(cols, rows_forward) == _sig(cols, rows_reversed)


def test_sig_discriminates_genuinely_different_row_values():
    cols = ["val"]
    rows_a = [{"val": 100}]
    rows_b = [{"val": 200}]
    assert _sig(cols, rows_a) != _sig(cols, rows_b)


def test_sig_discriminates_differing_column_counts():
    cols_one = ["col_a"]
    rows_one = [{"col_a": 10}]

    cols_two = ["col_a", "col_b"]
    rows_two = [{"col_a": 10, "col_b": 20}]

    assert _sig(cols_one, rows_one) != _sig(cols_two, rows_two)


def test_sig_discriminates_float_differences_larger_than_rounding_threshold():
    cols = ["price"]
    rows_a = [{"price": 19.99}]
    rows_b = [{"price": 24.50}]
    assert _sig(cols, rows_a) != _sig(cols, rows_b)


def test_sig_empty_rows_list_produces_stable_valid_json_string():
    cols = ["col1", "col2"]
    sig_1 = _sig(cols, [])
    sig_2 = _sig(cols, [])
    assert sig_1 == sig_2

    parsed = json.loads(sig_1)
    assert parsed["n_columns"] == 2
    assert parsed["rows"] == []


def test_sig_returns_parseable_json_containing_n_columns_metadata():
    cols = ["product_id", "quantity"]
    rows = [{"product_id": "P101", "quantity": 3}]
    signature_str = _sig(cols, rows)

    parsed = json.loads(signature_str)
    assert isinstance(parsed, dict)
    assert "n_columns" in parsed
    assert parsed["n_columns"] == 2
    assert "rows" in parsed
