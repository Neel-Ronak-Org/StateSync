"""
orchestrator/tile_render.py
Pure rendering helpers for dashboard tiles.

WHY THIS EXISTS: The server stores the chart design without rows and rows are
bound at read time, the way the synthesizer binds data.values for a live answer,
so a refreshed tile always charts its current rows.
"""

from __future__ import annotations

import copy
from typing import Any

ERROR_STATUS: dict[str, int] = {
    "not_found": 404,
    "not_pinnable": 422,
    "invalid": 422,
    "limit_reached": 409,
}


def error_status(code: str | None) -> int:
    """Map an MCP error code to an HTTP status code, defaulting to 500."""
    return ERROR_STATUS.get(code, 500) if code is not None else 500


def bind_chart(
    template: dict[str, Any] | None,
    rows: list[dict[str, Any]] | None,
    max_rows: int,
) -> dict[str, Any] | None:
    """Bind result rows into a chart template spec.

    Returns None if template is None; otherwise returns a deep copy of template
    with 'data' set to {'values': list(rows or [])[:max_rows]}. Does not mutate the input.
    """
    if template is None:
        return None
    bound = copy.deepcopy(template)
    bound["data"] = {"values": list(rows or [])[:max_rows]}
    return bound


def render_tile(view: dict[str, Any], max_rows: int) -> dict[str, Any]:
    """Render a tile view by binding its current result rows into its chart template.

    Returns a shallow copy of view with 'chart' replaced by bind_chart(...).
    Does not mutate the input.
    """
    rendered = dict(view)
    rows = (view.get("result") or {}).get("rows")
    rendered["chart"] = bind_chart(view.get("chart"), rows, max_rows)
    return rendered
