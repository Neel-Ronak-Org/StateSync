"""
tests/test_tile_render.py
Unit tests for pure tile rendering helpers (orchestrator/tile_render.py).
"""

from __future__ import annotations

import copy
import os
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))

from tile_render import bind_chart, error_status, render_tile


# ── error_status ─────────────────────────────────────────────────────────────

def test_error_status_known_codes():
    assert error_status("not_found") == 404
    assert error_status("not_pinnable") == 422
    assert error_status("invalid") == 422
    assert error_status("limit_reached") == 409


def test_error_status_unknown_code():
    assert error_status("unrecognized_code") == 500
    assert error_status("internal_error") == 500


def test_error_status_none():
    assert error_status(None) == 500


# ── bind_chart ───────────────────────────────────────────────────────────────

def test_bind_chart_binds_rows():
    template = {"mark": "bar", "encoding": {"x": {"field": "category"}}, "data": {}}
    rows = [{"category": "A", "val": 10}, {"category": "B", "val": 20}]
    bound = bind_chart(template, rows, max_rows=10)
    assert bound is not None
    assert bound["mark"] == "bar"
    assert bound["data"] == {"values": [{"category": "A", "val": 10}, {"category": "B", "val": 20}]}


def test_bind_chart_caps_at_max_rows():
    template = {"mark": "bar", "data": {}}
    rows = [{"n": i} for i in range(10)]
    bound = bind_chart(template, rows, max_rows=3)
    assert bound is not None
    assert bound["data"]["values"] == [{"n": 0}, {"n": 1}, {"n": 2}]
    assert len(bound["data"]["values"]) == 3


def test_bind_chart_returns_none_for_none_template():
    assert bind_chart(None, [{"n": 1}], max_rows=10) is None


def test_bind_chart_handles_rows_none_as_empty_list():
    template = {"mark": "line", "data": {}}
    bound = bind_chart(template, None, max_rows=10)
    assert bound is not None
    assert bound["data"] == {"values": []}


def test_bind_chart_does_not_mutate_template():
    template = {"mark": "bar", "data": {}}
    template_copy = copy.deepcopy(template)
    rows = [{"n": 1}, {"n": 2}]
    bound = bind_chart(template, rows, max_rows=10)

    assert bound is not None
    assert "values" in bound["data"]
    # Assert the original template has no "values" after the call
    assert "values" not in template.get("data", {})
    assert template == template_copy


# ── render_tile ──────────────────────────────────────────────────────────────

def test_render_tile_replaces_chart_and_keeps_every_other_key():
    view = {
        "tile_id": "tile-123",
        "title": "Revenue by Region",
        "state": "ok",
        "version": 2,
        "refresh_interval_s": 3600,
        "created_at": "2026-09-26T10:00:00Z",
        "updated_at": "2026-09-26T11:00:00Z",
        "sql": "SELECT region, sum(revenue) FROM orders GROUP BY region",
        "chart": {"mark": "bar", "encoding": {"x": {"field": "region"}}, "data": {}},
        "columns": ["region", "sum"],
        "schemas": ["public"],
        "source": {"session_id": "sess-abc", "turn_index": 1},
        "result": {
            "rows": [{"region": "North", "sum": 500}, {"region": "South", "sum": 300}],
            "row_count": 2,
            "truncated": False,
            "computed_at": "2026-09-26T11:00:00Z",
            "version": 2,
        },
        "stale": False,
        "last_error": None,
        "error_at": None,
        "versions": [{"version": 1}, {"version": 2}],
    }
    rendered = render_tile(view, max_rows=100)

    # Chart replaced with bound values
    assert rendered["chart"] is not None
    assert rendered["chart"]["data"]["values"] == [
        {"region": "North", "sum": 500},
        {"region": "South", "sum": 300},
    ]

    # Every other key preserved
    for key in view:
        if key != "chart":
            assert rendered[key] == view[key]


def test_render_tile_does_not_mutate_input():
    chart_template = {"mark": "bar", "data": {}}
    view = {
        "tile_id": "tile-xyz",
        "title": "Unchanged Tile",
        "chart": chart_template,
        "result": {"rows": [{"a": 1}]},
    }
    view_copy = copy.deepcopy(view)
    rendered = render_tile(view, max_rows=10)

    assert "values" not in view["chart"].get("data", {})
    assert view == view_copy
    assert rendered is not view


def test_render_tile_with_result_none_binds_empty_list():
    view = {
        "tile_id": "tile-no-result",
        "title": "Pending Tile",
        "chart": {"mark": "point", "data": {}},
        "result": None,
    }
    rendered = render_tile(view, max_rows=50)
    assert rendered["chart"] is not None
    assert rendered["chart"]["data"] == {"values": []}


def test_render_tile_with_chart_none():
    view = {
        "tile_id": "tile-no-chart",
        "title": "Numeric Only",
        "chart": None,
        "result": {"rows": [{"count": 42}]},
    }
    rendered = render_tile(view, max_rows=50)
    assert rendered["chart"] is None
