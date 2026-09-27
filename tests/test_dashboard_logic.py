"""
tests/test_dashboard_logic.py
Pins the pure logic behind dashboards (docs/V3_PLAN.md Phase 2b,
mcp/tools/dashboard_tools.py and the Phase 2b additions to tile_tools.py).

WHY THIS EXISTS. Each helper here is a boundary a request crosses before it
touches the database:
- resolve_layout / resolve_layout_items keep every tile inside the 12-column
  grid. The database repeats the bounds as a CHECK constraint, but a violation
  there is a 500; here it is a clear 422 that names the tile.
- resolve_name keeps dashboard names non-blank and bounded.
- resolve_viz is the only way a presentation choice is stored; "auto" must
  clear it, and anything outside the list must be refused.
- title_fallback decides what a pinned follow-up is called. "And bottom?"
  means nothing on a dashboard; its rewrite does.
- default_interval gives tiles on the live dataset a 30 s refresh and every
  other dataset the global default.
The database paths are verified live by scripts/verify_dashboards.py.
"""

from __future__ import annotations

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
import tools.dashboard_tools as dt  # noqa: E402
import tools.tile_tools as tt  # noqa: E402

COLS = int(dt.cfg.dashboards.grid_columns)
MIN_H = int(dt.cfg.dashboards.tile_min_h)
MAX_H = int(dt.cfg.dashboards.tile_max_h)


# ── resolve_layout ────────────────────────────────────────────────────────────

def test_a_slot_inside_the_grid_is_kept():
    assert dt.resolve_layout({"x": 0, "y": 0, "w": COLS, "h": MIN_H}) == {"x": 0, "y": 0, "w": COLS, "h": MIN_H}
    assert dt.resolve_layout({"x": COLS - 3, "y": 40, "w": 3, "h": MAX_H}) == {"x": COLS - 3, "y": 40, "w": 3, "h": MAX_H}


@pytest.mark.parametrize("slot", [
    {"x": COLS - 2, "y": 0, "w": 3, "h": 4},     # overflows the right edge
    {"x": -1, "y": 0, "w": 3, "h": 4},
    {"x": 0, "y": -1, "w": 3, "h": 4},
    {"x": 0, "y": 0, "w": 0, "h": 4},
    {"x": 0, "y": 0, "w": COLS + 1, "h": 4},
    {"x": 0, "y": 0, "w": 3, "h": MIN_H - 1},
    {"x": 0, "y": 0, "w": 3, "h": MAX_H + 1},
    {"x": 0, "y": 0, "w": 3},                    # missing h
    {"x": "0", "y": 0, "w": 3, "h": 4},          # text is not a number
    {"x": 0.0, "y": 0, "w": 3, "h": 4},
    {"x": True, "y": 0, "w": 3, "h": 4},          # JSON true is not 1
])
def test_a_slot_outside_the_grid_or_malformed_is_refused(slot):
    assert dt.resolve_layout(slot) is None


def test_not_a_dict_is_refused():
    assert dt.resolve_layout(None) is None and dt.resolve_layout([0, 0, 3, 4]) is None


# ── resolve_layout_items ──────────────────────────────────────────────────────

def test_layout_items_resolve_in_order():
    items = [{"tile_id": "a", "x": 0, "y": 0, "w": 6, "h": 8}, {"tile_id": "b", "x": 6, "y": 0, "w": 6, "h": 8}]
    assert dt.resolve_layout_items(items) == [("a", {"x": 0, "y": 0, "w": 6, "h": 8}),
                                              ("b", {"x": 6, "y": 0, "w": 6, "h": 8})]


def test_an_empty_layout_is_allowed():
    assert dt.resolve_layout_items([]) == []


def test_a_duplicate_tile_is_refused_and_named():
    items = [{"tile_id": "a", "x": 0, "y": 0, "w": 6, "h": 8}, {"tile_id": "a", "x": 6, "y": 0, "w": 6, "h": 8}]
    message = dt.resolve_layout_items(items)
    assert isinstance(message, str) and "a" in message


def test_one_bad_slot_refuses_the_whole_request_and_names_the_tile():
    items = [{"tile_id": "ok", "x": 0, "y": 0, "w": 6, "h": 8}, {"tile_id": "bad", "x": 11, "y": 0, "w": 6, "h": 8}]
    message = dt.resolve_layout_items(items)
    assert isinstance(message, str) and "bad" in message


@pytest.mark.parametrize("items", [None, {"tile_id": "a"}, [{"x": 0, "y": 0, "w": 6, "h": 8}], ["a"]])
def test_malformed_layout_requests_are_refused(items):
    assert isinstance(dt.resolve_layout_items(items), str)


def test_more_items_than_a_dashboard_holds_are_refused():
    cap = int(dt.cfg.dashboards.max_tiles_per_dashboard)
    items = [{"tile_id": f"t{i}", "x": 0, "y": i, "w": 3, "h": MIN_H} for i in range(cap + 1)]
    assert isinstance(dt.resolve_layout_items(items), str)


# ── resolve_name ──────────────────────────────────────────────────────────────

def test_resolve_name():
    cap = int(dt.cfg.dashboards.name_max_chars)
    assert dt.resolve_name("  Sales  ") == "Sales"
    assert dt.resolve_name("   ") is None and dt.resolve_name(None) is None and dt.resolve_name(3) is None
    assert len(dt.resolve_name("x" * (cap + 10))) == cap


# ── resolve_viz ───────────────────────────────────────────────────────────────

def test_every_kind_is_accepted_and_auto_clears():
    for kind in tt.VIZ_KINDS:
        assert tt.resolve_viz(kind) == (True, kind)
    assert tt.resolve_viz("auto") == (True, None)


@pytest.mark.parametrize("value", ["pie", "", "KPI", None, 3, "bar; drop table"])
def test_unknown_viz_is_refused(value):
    assert tt.resolve_viz(value) == (False, None)


def test_viz_kinds_match_the_database_constraint():
    migration = (REPO_ROOT / "migrations" / "019_dashboards.sql").read_text()
    for kind in tt.VIZ_KINDS:
        assert f"'{kind}'" in migration, f"{kind} missing from tiles_viz_ck"


# ── title_fallback ────────────────────────────────────────────────────────────

def test_a_rewritten_follow_up_is_titled_by_its_rewrite():
    assert tt.title_fallback("And bottom?", "Bottom 5 states by number of customers", True) == \
        "Bottom 5 states by number of customers"


def test_without_a_rewrite_the_question_is_the_title():
    assert tt.title_fallback("How many orders?", "How many orders?", False) == "How many orders?"
    assert tt.title_fallback("How many orders?", None, True) == "How many orders?"
    assert tt.title_fallback("How many orders?", "   ", True) == "How many orders?"


# ── default_interval / resolve_interval ───────────────────────────────────────

def test_live_tiles_default_to_the_live_interval_and_others_to_the_global_default():
    assert tt.default_interval("live") == int(tt.cfg.tiles.schema_default_refresh_s.live)
    assert tt.default_interval("olist") == int(tt.cfg.tiles.default_refresh_s)
    assert tt.default_interval(None) == int(tt.cfg.tiles.default_refresh_s)
    assert tt.resolve_interval(None, "live") == int(tt.cfg.tiles.schema_default_refresh_s.live)


def test_the_live_default_is_itself_inside_the_bounds():
    live = int(tt.cfg.tiles.schema_default_refresh_s.live)
    assert tt.resolve_interval(live) == live
