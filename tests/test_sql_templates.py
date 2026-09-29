"""
tests/test_sql_templates.py
Pins derive_template (mcp/tools/sql_templates.py, docs/V3_PLAN.md Phase 3c slice 3).

WHY THIS EXISTS: a template is an approved query with its values pulled out, so
(a) only the literals that are really values may become slots (a literal in the
select list or a CASE arm must stay), (b) each slot's type decides how the
orchestrator validates a new value, and (c) filling a template with its own
example values must give back the same query. Anything that is not a plain single
SELECT (CTE, UNION, sub-select) must yield no template at all.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))
sys.path.insert(0, str(REPO_ROOT / "mcp"))

pytest.importorskip("pglast")
import pglast  # noqa: E402
from pglast.stream import RawStream  # noqa: E402

from config_loader import cfg  # noqa: E402
from tools.sql_templates import derive_template  # noqa: E402


def _refill(template_sql: str, slots: list[dict]) -> str:
    """Substitute each slot's example back in, by type, the way the orchestrator renders values."""
    out = template_sql
    for i in range(len(slots), 0, -1):
        slot = slots[i - 1]
        ex = slot["example"]
        literal = ex if slot["type"] in ("integer", "number") else "'" + ex.replace("'", "''") + "'"
        out = out.replace(f"${i}", literal)
    return out


def _canon(sql: str) -> str:
    return RawStream()(pglast.parse_sql(sql)[0])


def test_text_and_date_slots_from_comparisons():
    sql = ("SELECT COUNT(*) FROM olist.orders o WHERE o.order_status = 'delivered' "
           "AND o.order_purchase_timestamp >= '2018-01-01'")
    template, slots = derive_template(sql)
    assert "$1" in template and "$2" in template and "'delivered'" not in template
    assert [(s["name"], s["type"], s["column"], s["example"]) for s in slots] == [
        ("s1", "text", "o.order_status", "delivered"),
        ("s2", "date", "o.order_purchase_timestamp", "2018-01-01"),
    ]


def test_limit_is_an_integer_slot_without_a_column():
    template, slots = derive_template("SELECT a FROM t ORDER BY a DESC LIMIT 5")
    assert template.endswith("LIMIT $1")
    assert slots == [{"name": "s1", "type": "integer", "column": None, "example": "5"}]


def test_in_list_between_like_and_types():
    sql = ("SELECT a FROM t WHERE s IN ('x', 'y') AND n BETWEEN 1 AND 2.5 "
           "AND ts >= '2018-01-01 10:00:00' AND q LIKE '%a%'")
    template, slots = derive_template(sql)
    assert [s["type"] for s in slots] == ["text", "text", "integer", "number", "timestamp", "text"]
    assert [s["column"] for s in slots] == ["s", "s", "n", "n", "ts", "q"]
    assert "IN ($1, $2)" in template and "BETWEEN $3 AND $4" in template


def test_cast_around_a_date_slot_is_kept_and_other_casts_are_not_slots():
    template, slots = derive_template("SELECT a FROM t WHERE d > date '2018-01-01' AND n = CAST('5' AS int)")
    assert len(slots) == 1 and slots[0]["type"] == "date"
    assert "CAST($1 AS date)" in template and "'5'" in template


def test_literals_outside_comparisons_stay_literal():
    template, slots = derive_template("SELECT CASE WHEN a = 'x' THEN 1 END, upper('z') FROM t WHERE f(a) = 3 AND b = 4")
    assert len(slots) == 1 and slots[0]["column"] == "b"
    assert "'x'" in template and "'z'" in template and "f(a) = 3" in template


def test_join_condition_literal_is_a_slot():
    _t, slots = derive_template("SELECT 1 FROM t JOIN u ON t.id = u.id AND u.k = 'z'")
    assert [s["column"] for s in slots] == ["u.k"]


def test_no_literals_gives_none():
    assert derive_template("SELECT a, count(*) FROM t GROUP BY a") is None


@pytest.mark.parametrize("sql", [
    "WITH c AS (SELECT 1) SELECT * FROM c WHERE x = 1",
    "SELECT a FROM t WHERE x = 1 UNION SELECT a FROM u WHERE x = 2",
    "SELECT a FROM t WHERE x IN (SELECT y FROM u) AND z = 3",
    "SELECT a FROM (SELECT a FROM t WHERE x = 1) s",
    "INSERT INTO t VALUES (1)",
    "SELECT 1; SELECT 2",
    "not sql at all",
    "SELECT '$1' FROM t WHERE x = 1",
])
def test_unsupported_shapes_give_none(sql):
    assert derive_template(sql) is None


def test_more_than_max_slots_gives_none():
    n = int(cfg.templates.max_slots)
    ok = " AND ".join(f"c{i} = {i + 2}" for i in range(n))
    over = ok + f" AND c{n} = {n + 2}"
    assert derive_template(f"SELECT a FROM t WHERE {ok}") is not None
    assert derive_template(f"SELECT a FROM t WHERE {over}") is None


@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) FROM olist.orders o WHERE o.order_status = 'delivered' AND o.order_purchase_timestamp >= '2018-01-01'",
    "SELECT a FROM t WHERE s IN ('x', 'y') AND n BETWEEN 1 AND 2.5 AND q LIKE '%a%' ORDER BY a LIMIT 5",
    "SELECT a FROM t WHERE d > date '2018-01-01' AND ts <= '2018-01-01 10:00:00'",
])
def test_refilling_with_the_examples_reproduces_the_query(sql):
    template, slots = derive_template(sql)
    assert "$1" in template
    assert _canon(_refill(template, slots)) == _canon(sql)
