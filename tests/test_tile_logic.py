"""
tests/test_tile_logic.py
Pins the pure logic behind dashboard tiles (docs/V3_PLAN.md Phase 1,
mcp/tools/tile_tools.py).

WHY THIS EXISTS. A tile re-runs saved SQL and must notice when the tables under
it change shape. Each piece tested here is one way that could silently go
wrong:
- table_refs decides which tables a tile depends on. If it drops a table, a
  change to that table is never noticed; if it counts a CTE as a table, the
  tile can never be fingerprinted.
- fingerprint_of must change when a column the tile reads changes, and must
  NOT change for a reordering or a reworded comment, or every tile would be
  flagged needs_review for nothing.
- chart_template must strip the rows (they are re-bound on every read) but
  keep the chart design.
- cap_rows, is_stale, resolve_interval and resolve_title bound what a tile can
  store and how often it can hit the database.
The database paths are verified live by scripts/verify_tiles.py.
"""

from __future__ import annotations

import datetime as dt
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MCP_DIR = REPO_ROOT / "mcp"
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))
sys.path.insert(0, str(MCP_DIR))

if "psycopg_pool" not in sys.modules:
    try:
        import psycopg_pool  # noqa: F401
    except ImportError:
        import unittest.mock
        sys.modules["psycopg_pool"] = unittest.mock.MagicMock()

pytest.importorskip("pglast")
pytest.importorskip("psycopg")
import tools.tile_tools as tt  # noqa: E402

COLS = [{"name": "order_id", "type": "text", "nullable": False, "comment": None},
        {"name": "status", "type": "text", "nullable": True, "comment": "order state"}]


# ── table_refs ────────────────────────────────────────────────────────────────

def test_unqualified_tables_resolve_to_public_and_are_sorted():
    sql = "SELECT count(*) FROM sellers s JOIN locations l ON s.location_id = l.location_id"
    assert tt.table_refs(sql) == [("public", "locations"), ("public", "sellers")]


def test_qualified_tables_keep_their_schema():
    sql = "SELECT count(*) FROM olist.orders o JOIN olist.customers c USING (customer_id)"
    assert tt.table_refs(sql) == [("olist", "customers"), ("olist", "orders")]


def test_cte_names_are_not_tables_but_what_the_cte_reads_is():
    sql = "WITH d AS (SELECT * FROM olist.orders WHERE order_status = 'delivered') SELECT count(*) FROM d"
    assert tt.table_refs(sql) == [("olist", "orders")]


def test_subqueries_are_followed():
    sql = "SELECT * FROM sellers WHERE location_id IN (SELECT location_id FROM locations WHERE city = 'Pune')"
    assert tt.table_refs(sql) == [("public", "locations"), ("public", "sellers")]


# ── fingerprint_of ────────────────────────────────────────────────────────────

def test_fingerprint_ignores_order_and_comments():
    reordered = list(reversed(COLS))
    recommented = [dict(c, comment="reworded") for c in COLS]
    base = tt.fingerprint_of([("olist", "orders", COLS)])
    assert tt.fingerprint_of([("olist", "orders", reordered)]) == base
    assert tt.fingerprint_of([("olist", "orders", recommented)]) == base


@pytest.mark.parametrize("change", [
    lambda cols: cols + [{"name": "region", "type": "text", "nullable": True, "comment": None}],
    lambda cols: [dict(cols[0], type="integer"), cols[1]],
    lambda cols: [cols[0], dict(cols[1], nullable=False)],
    lambda cols: [dict(cols[0], name="id"), cols[1]],
    lambda cols: cols[:1],
])
def test_fingerprint_changes_when_a_column_changes(change):
    assert tt.fingerprint_of([("olist", "orders", change(COLS))]) != tt.fingerprint_of([("olist", "orders", COLS)])


def test_fingerprint_distinguishes_schemas():
    assert tt.fingerprint_of([("public", "orders", COLS)]) != tt.fingerprint_of([("olist", "orders", COLS)])


# ── chart_template ────────────────────────────────────────────────────────────

def test_chart_template_strips_rows_keeps_design_and_does_not_mutate():
    chart = {"mark": "bar", "encoding": {"x": {"field": "state"}},
             "data": {"values": [{"state": "SP", "n": 1}]}}
    template = tt.chart_template(chart)
    assert template == {"mark": "bar", "encoding": {"x": {"field": "state"}}, "data": {}}
    assert chart["data"]["values"] == [{"state": "SP", "n": 1}]


def test_no_chart_stays_none():
    assert tt.chart_template(None) is None


# ── cap_rows ──────────────────────────────────────────────────────────────────

def test_cap_rows_by_count(monkeypatch):
    rows = [{"n": i} for i in range(int(tt.cfg.tiles.result_max_rows) + 5)]
    kept, truncated = tt.cap_rows(rows)
    assert len(kept) == int(tt.cfg.tiles.result_max_rows) and truncated


def test_cap_rows_by_bytes():
    big = "x" * (int(tt.cfg.tiles.result_max_bytes) // 10)
    kept, truncated = tt.cap_rows([{"blob": big} for _ in range(20)])
    assert truncated and 0 < len(kept) < 20


def test_small_result_untouched():
    rows = [{"n": 1}, {"n": 2}]
    assert tt.cap_rows(rows) == (rows, False)


# ── is_stale / resolve_interval / resolve_title ───────────────────────────────

def test_is_stale():
    now = dt.datetime(2026, 9, 26, 12, 0, tzinfo=dt.timezone.utc)
    assert tt.is_stale(None, 3600, now)
    assert tt.is_stale(now - dt.timedelta(seconds=3600), 3600, now)
    assert not tt.is_stale(now - dt.timedelta(seconds=3599), 3600, now)


def test_resolve_interval_bounds():
    lo, hi = int(tt.cfg.tiles.min_refresh_s), int(tt.cfg.tiles.max_refresh_s)
    assert tt.resolve_interval(None) == int(tt.cfg.tiles.default_refresh_s)
    assert tt.resolve_interval(lo) == lo and tt.resolve_interval(hi) == hi
    for bad in (lo - 1, hi + 1, "3600", 3600.0, True):
        assert tt.resolve_interval(bad) is None


def test_resolve_title():
    cap = int(tt.cfg.tiles.title_max_chars)
    assert tt.resolve_title(None, "  How many orders?  ") == "How many orders?"
    assert tt.resolve_title("  Orders  ", "fallback") == "Orders"
    assert tt.resolve_title("   ", "fallback") is None
    assert tt.resolve_title(None, None) == "Untitled tile"
    assert len(tt.resolve_title("x" * (cap + 50), None)) == cap


# ── rows as psycopg returns them (Phase 2b regression) ────────────────────────

def test_database_values_become_storable_json():
    """A tile's refresh stores rows straight from psycopg. A NUMERIC is a Decimal,
    a time bucket a datetime: json.dumps refused both, so every refresh of such a
    tile crashed and took its whole dashboard's listing down with it."""
    import datetime as _dt
    import json as _json
    from decimal import Decimal

    from serialization import to_json_safe

    rows = [{"hour": _dt.datetime(2026, 9, 27, 12, tzinfo=_dt.timezone.utc), "revenue": Decimal("1234.50"),
             "orders": Decimal("12"), "day": _dt.date(2026, 9, 27), "gap": _dt.timedelta(hours=2)}]
    safe = to_json_safe(rows)
    assert _json.loads(_json.dumps(safe)) == [{"hour": "2026-09-27T12:00:00+00:00", "revenue": 1234.5,
                                               "orders": 12, "day": "2026-09-27", "gap": 7200.0}]
    kept, truncated = tt.cap_rows(safe)
    assert kept == safe and not truncated
