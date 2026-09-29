"""
tests/test_sql_explain.py
Pins the deterministic "How I got this" explanation of executed SQL.

WHY THIS EXISTS: the sentence is shown to business owners as a way to check an answer, so
it must say exactly what the SQL says, be identical on every run, and never raise (a
failure here must cost the explanation, not the answer). No model, no network.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))

pytest.importorskip("pglast")

from sql_explain import explain_sql  # noqa: E402

MIN_EXPLAINED = 0.8  # share of golden ground-truth queries that must get an explanation


def test_group_by_count_sorted():
    sql = ("SELECT order_status, count(*) AS order_count FROM olist.orders "
           "GROUP BY order_status ORDER BY order_count DESC")
    assert explain_sql(sql) == (
        "Counted rows in orders, grouped by order status, sorted by order count, highest first.")


def test_filters_and_default_limit_omitted():
    sql = ("SELECT COUNT(*) FROM olist.orders o WHERE o.order_status = 'delivered' "
           "AND o.order_delivered_customer_date IS NULL LIMIT 1000")
    assert explain_sql(sql) == (
        "Counted rows in orders where order status is 'delivered' "
        "and has no order delivered customer date.")


def test_sum_with_join_and_limit():
    sql = ("SELECT SUM(p.payment_value) FROM olist.orders o "
           "JOIN olist.order_payments p ON p.order_id = o.order_id LIMIT 5")
    assert explain_sql(sql) == (
        "Added up payment value in orders joined with order_payments, top 5.")


def test_plain_columns_sorted_ascending():
    assert explain_sql("SELECT order_id, price, freight_value, extra FROM items ORDER BY price") == (
        "Listed order id, price, freight value and 1 more in items, sorted by price, lowest first.")


def test_operators():
    sql = ("SELECT count(DISTINCT customer_id) FROM t WHERE a > 3 AND b <> 'x' AND c IS NOT NULL "
           "AND d IN ('p', 'q') AND e ILIKE 'z%' AND f BETWEEN 1 AND 2 AND g <= 9")
    assert explain_sql(sql) == (
        "Counted distinct customer id in t where a is above 3 and b is not 'x' and has a c "
        "and d is one of 'p', 'q' and e matches 'z%' and f between 1 and 2 and g is at most 9.")


def test_complex_condition_and_calculated_argument():
    assert explain_sql("SELECT AVG(a * b) FROM t WHERE x = 1 OR y = 2") == (
        "Averaged a calculated value in t with other conditions.")


def test_cte_and_union_fall_back_to_tables():
    cte = "WITH a AS (SELECT * FROM orders) SELECT count(*) FROM a JOIN customers c ON true"
    assert explain_sql(cte) == "Ran a query over customers and orders."
    assert explain_sql("SELECT 1 FROM a UNION SELECT 1 FROM b") == "Ran a query over a and b."
    assert explain_sql("SELECT (SELECT max(x) FROM u) FROM v") == "Ran a query over v and u."


@pytest.mark.parametrize("bad", [None, "", "   ", "SELEC nope", "DELETE FROM t",
                                 "SELECT 1; SELECT 2", "SELECT 1"])
def test_unexplainable_input_is_none(bad):
    assert explain_sql(bad) is None


def test_long_explanation_is_cut_at_a_clause_boundary():
    cols = ", ".join(f"c{i}" for i in range(40))
    sql = f"SELECT count(*) FROM t WHERE a = 'x' GROUP BY {cols} ORDER BY a DESC"
    out = explain_sql(sql)
    assert out is not None and out.endswith(".") and len(out) <= 300
    assert explain_sql(sql) == out


def _ground_truth_sql(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "ground_truth_sql" and isinstance(value, str) and value.strip():
                yield value
            else:
                yield from _ground_truth_sql(value)
    elif isinstance(node, list):
        for item in node:
            yield from _ground_truth_sql(item)


def test_golden_ground_truth_sql_is_explained():
    queries = []
    for path in sorted((REPO_ROOT / "fixtures" / "golden").glob("*.json")):
        queries.extend(_ground_truth_sql(json.loads(path.read_text())))
    assert queries, "no golden ground_truth_sql found"
    results = [explain_sql(q) for q in queries]  # must not raise
    for text in results:
        assert text is None or (text.strip() and text.endswith("."))
    explained = sum(t is not None for t in results)
    assert explained / len(results) >= MIN_EXPLAINED, f"{explained}/{len(results)} explained"
