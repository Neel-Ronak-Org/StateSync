"""
mcp/tools/dashboard_tools.py
MCP dashboards tool (docs/V3_PLAN.md Phase 2b, migration 019): named
dashboards per dataset, each holding tiles with a stored layout.

One JSON-RPC method, dashboards.manage, takes an `action`: list, create,
rename, delete, layout. Tiles themselves stay in tiles.manage; this module
owns the containers and where tiles sit in them.

THE SECURITY PROPERTIES, enforced here rather than trusted from a caller:

1. Every statement filters on tenant_id (bound server-side from the session,
   A0.6) AND principal_id (supplied by the authenticating orchestrator).
   Someone else's dashboard looks exactly like one that does not exist.
2. A dashboard is about one dataset, and the name must be in the MCP's own
   schema allowlist (schema_tools.ALLOWED_SCHEMAS), the authoritative check.
3. `layout` moves only tiles that belong to the same owner AND the same
   dashboard. One unknown tile_id rejects the whole request and changes
   nothing (the transaction rolls back).
4. Names, counts and grid bounds are capped from config [dashboards]; the
   database repeats the grid bounds as CHECK constraints (migration 019).

Failures are data, not transport errors ({"ok": False, "error": <code>,
"message": <text>}), the same contract as tiles.manage. Codes: not_found,
invalid, limit_reached.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from config_loader import cfg
from db import get_history_connection
from session import get_tenant_id
from tools.schema_tools import ALLOWED_SCHEMAS

logger = logging.getLogger(__name__)

_ACTIONS = frozenset({"list", "create", "rename", "delete", "layout"})
_NEEDS_DASHBOARD_ID = frozenset({"rename", "delete", "layout"})

DEFAULT_DASHBOARD_NAME = "Overview"


# ── Pure helpers (no database; unit-tested directly) ─────────────────────────

def resolve_name(name: Any) -> str | None:
    """A trimmed dashboard name within the length cap, or None if blank or not text."""
    if not isinstance(name, str) or not name.strip():
        return None
    return name.strip()[: int(cfg.dashboards.name_max_chars)]


def _whole(value: Any) -> int | None:
    """An int that is not a bool, else None (JSON gives true/false as bools)."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def resolve_layout(item: Any) -> dict[str, int] | None:
    """{x, y, w, h} within the grid, or None if anything is missing or out of bounds.

    The grid is cfg.dashboards.grid_columns wide; a tile must fit inside it.
    The same bounds are CHECK constraints in migration 019.
    """
    if not isinstance(item, dict):
        return None
    x, y, w, h = (_whole(item.get(k)) for k in ("x", "y", "w", "h"))
    if None in (x, y, w, h):
        return None
    cols = int(cfg.dashboards.grid_columns)
    if not (0 <= x < cols and 1 <= w <= cols and x + w <= cols):
        return None
    if not (0 <= y <= int(cfg.dashboards.grid_max_row)):
        return None
    if not (int(cfg.dashboards.tile_min_h) <= h <= int(cfg.dashboards.tile_max_h)):
        return None
    return {"x": x, "y": y, "w": w, "h": h}


def resolve_layout_items(items: Any) -> list[tuple[str, dict[str, int]]] | str:
    """[(tile_id, layout)] for a layout request, or a message saying what is wrong."""
    if not isinstance(items, list):
        return "items must be a list of {tile_id, x, y, w, h}."
    if len(items) > int(cfg.dashboards.max_tiles_per_dashboard):
        return f"A dashboard holds at most {cfg.dashboards.max_tiles_per_dashboard} tiles."
    resolved: list[tuple[str, dict[str, int]]] = []
    seen: set[str] = set()
    for item in items:
        tile_id = item.get("tile_id") if isinstance(item, dict) else None
        if not isinstance(tile_id, str) or not tile_id:
            return "Every item needs a tile_id."
        if tile_id in seen:
            return f"Tile {tile_id} appears twice."
        seen.add(tile_id)
        layout = resolve_layout(item)
        if layout is None:
            return (f"Tile {tile_id} is outside the grid: x+w must fit in "
                    f"{cfg.dashboards.grid_columns} columns, and h must be between "
                    f"{cfg.dashboards.tile_min_h} and {cfg.dashboards.tile_max_h}.")
        resolved.append((tile_id, layout))
    return resolved


def _fail(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": code, "message": message}


class _Abort(Exception):
    """Raised inside a history transaction to roll it back and report a failure."""

    def __init__(self, failure: dict[str, Any]) -> None:
        super().__init__(failure.get("message"))
        self.failure = failure


# ── Database access (every statement scoped by tenant AND principal) ──────────

def _to_view(row: tuple) -> dict[str, Any]:
    dashboard_id, name, target_schema, position, created_at, updated_at, tile_count = row
    return {
        "dashboard_id": dashboard_id,
        "name": name,
        "target_schema": target_schema,
        "position": position,
        "tile_count": tile_count,
        "created_at": created_at.isoformat(),
        "updated_at": updated_at.isoformat(),
    }


_SELECT = """
    SELECT d.dashboard_id, d.name, d.target_schema, d.position, d.created_at, d.updated_at,
           count(t.tile_id)
    FROM session_state.dashboards d
    LEFT JOIN session_state.tiles t
      ON t.dashboard_id = d.dashboard_id AND t.tenant_id = d.tenant_id AND t.principal_id = d.principal_id
    WHERE d.tenant_id = %s AND d.principal_id = %s
"""
_GROUP = " GROUP BY d.dashboard_id ORDER BY d.target_schema, d.position, d.created_at"


async def owned_dashboard(conn: Any, tenant_id: str, principal_id: str,
                          dashboard_id: str) -> tuple[str, str] | None:
    """(dashboard_id, target_schema) if the principal owns it, else None."""
    row = await (await conn.execute(
        """
        SELECT dashboard_id, target_schema FROM session_state.dashboards
        WHERE tenant_id = %s AND principal_id = %s AND dashboard_id = %s
        """,
        (tenant_id, principal_id, dashboard_id),
    )).fetchone()
    return (row[0], row[1]) if row else None


async def default_dashboard(conn: Any, tenant_id: str, principal_id: str, target_schema: str) -> str:
    """The owner's first dashboard for a dataset, creating "Overview" if there is none.

    Used when an answer is pinned from chat without choosing a dashboard.
    """
    row = await (await conn.execute(
        """
        SELECT dashboard_id FROM session_state.dashboards
        WHERE tenant_id = %s AND principal_id = %s AND target_schema = %s
        ORDER BY position, created_at
        LIMIT 1
        """,
        (tenant_id, principal_id, target_schema),
    )).fetchone()
    if row:
        return row[0]
    return await _insert(conn, tenant_id, principal_id, DEFAULT_DASHBOARD_NAME, target_schema)


async def _insert(conn: Any, tenant_id: str, principal_id: str, name: str, target_schema: str) -> str:
    dashboard_id = str(uuid.uuid4())
    await conn.execute(
        """
        INSERT INTO session_state.dashboards (dashboard_id, tenant_id, principal_id, name, target_schema, position)
        SELECT %s, %s, %s, %s, %s, COALESCE(max(position) + 1, 0)
        FROM session_state.dashboards
        WHERE tenant_id = %s AND principal_id = %s AND target_schema = %s
        """,
        (dashboard_id, tenant_id, principal_id, name, target_schema,
         tenant_id, principal_id, target_schema),
    )
    logger.info("dashboards: created %s (%s) on %s (tenant=%s principal=%s)",
                dashboard_id, name, target_schema, tenant_id, principal_id)
    return dashboard_id


async def _view(conn: Any, tenant_id: str, principal_id: str, dashboard_id: str) -> dict[str, Any] | None:
    row = await (await conn.execute(
        _SELECT + " AND d.dashboard_id = %s" + _GROUP, (tenant_id, principal_id, dashboard_id),
    )).fetchone()
    return _to_view(row) if row else None


# ── Actions ───────────────────────────────────────────────────────────────────

async def _list(tenant_id: str, principal_id: str, target_schema: Any) -> dict[str, Any]:
    if target_schema is not None and target_schema not in ALLOWED_SCHEMAS:
        return _fail("invalid", f"Unknown dataset {target_schema!r}.")
    async with get_history_connection() as conn:
        if target_schema is None:
            rows = await (await conn.execute(_SELECT + _GROUP, (tenant_id, principal_id))).fetchall()
        else:
            rows = await (await conn.execute(
                _SELECT + " AND d.target_schema = %s" + _GROUP, (tenant_id, principal_id, target_schema),
            )).fetchall()
    return {"ok": True, "dashboards": [_to_view(r) for r in rows]}


async def _create(tenant_id: str, principal_id: str, name: Any, target_schema: Any) -> dict[str, Any]:
    clean = resolve_name(name)
    if clean is None:
        return _fail("invalid", "name must not be blank.")
    if target_schema not in ALLOWED_SCHEMAS:
        return _fail("invalid", f"Unknown dataset {target_schema!r}.")
    async with get_history_connection() as conn:
        count = (await (await conn.execute(
            "SELECT count(*) FROM session_state.dashboards WHERE tenant_id = %s AND principal_id = %s",
            (tenant_id, principal_id),
        )).fetchone())[0]
        if count >= int(cfg.dashboards.max_per_principal):
            return _fail("limit_reached",
                         f"You already have {count} dashboards; the limit is {cfg.dashboards.max_per_principal}.")
        dashboard_id = await _insert(conn, tenant_id, principal_id, clean, target_schema)
        return {"ok": True, "dashboard": await _view(conn, tenant_id, principal_id, dashboard_id)}


async def _rename(tenant_id: str, principal_id: str, dashboard_id: str, name: Any) -> dict[str, Any]:
    clean = resolve_name(name)
    if clean is None:
        return _fail("invalid", "name must not be blank.")
    async with get_history_connection() as conn:
        row = await (await conn.execute(
            """
            UPDATE session_state.dashboards SET name = %s, updated_at = now()
            WHERE tenant_id = %s AND principal_id = %s AND dashboard_id = %s
            RETURNING dashboard_id
            """,
            (clean, tenant_id, principal_id, dashboard_id),
        )).fetchone()
        if row is None:
            return _fail("not_found", "No such dashboard.")
        return {"ok": True, "dashboard": await _view(conn, tenant_id, principal_id, dashboard_id)}


async def _delete(tenant_id: str, principal_id: str, dashboard_id: str) -> dict[str, Any]:
    """Delete a dashboard and, by cascade, its tiles."""
    async with get_history_connection() as conn:
        row = await (await conn.execute(
            """
            DELETE FROM session_state.dashboards
            WHERE tenant_id = %s AND principal_id = %s AND dashboard_id = %s
            RETURNING dashboard_id
            """,
            (tenant_id, principal_id, dashboard_id),
        )).fetchone()
    if row is None:
        return _fail("not_found", "No such dashboard.")
    logger.info("dashboards: deleted %s (tenant=%s principal=%s)", dashboard_id, tenant_id, principal_id)
    return {"ok": True, "dashboard_id": dashboard_id}


async def _layout(tenant_id: str, principal_id: str, dashboard_id: str, items: Any) -> dict[str, Any]:
    """Store every listed tile's slot, all or nothing."""
    resolved = resolve_layout_items(items)
    if isinstance(resolved, str):
        return _fail("invalid", resolved)
    try:
        async with get_history_connection() as conn:
            if await owned_dashboard(conn, tenant_id, principal_id, dashboard_id) is None:
                raise _Abort(_fail("not_found", "No such dashboard."))
            for tile_id, slot in resolved:
                row = await (await conn.execute(
                    """
                    UPDATE session_state.tiles
                    SET layout_x = %s, layout_y = %s, layout_w = %s, layout_h = %s
                    WHERE tenant_id = %s AND principal_id = %s AND dashboard_id = %s AND tile_id = %s
                    RETURNING tile_id
                    """,
                    (slot["x"], slot["y"], slot["w"], slot["h"],
                     tenant_id, principal_id, dashboard_id, tile_id),
                )).fetchone()
                if row is None:
                    raise _Abort(_fail("not_found", f"No tile {tile_id} on this dashboard."))
            await conn.execute(
                """
                UPDATE session_state.dashboards SET updated_at = now()
                WHERE tenant_id = %s AND principal_id = %s AND dashboard_id = %s
                """,
                (tenant_id, principal_id, dashboard_id),
            )
    except _Abort as abort:
        return abort.failure
    return {"ok": True, "dashboard_id": dashboard_id, "updated": len(resolved)}


async def manage(
    action: str,
    principal_id: str,
    dashboard_id: str | None = None,
    name: str | None = None,
    target_schema: str | None = None,
    items: list | None = None,
) -> dict[str, Any]:
    """dashboards.manage -- see the module docstring for the actions and guarantees."""
    tenant_id = get_tenant_id()
    if not isinstance(principal_id, str) or not principal_id:
        return _fail("invalid", "principal_id is required.")
    if action not in _ACTIONS:
        return _fail("invalid", f"Unknown action {action!r}. Valid: {sorted(_ACTIONS)}.")
    if action in _NEEDS_DASHBOARD_ID and (not isinstance(dashboard_id, str) or not dashboard_id):
        return _fail("invalid", f"dashboard_id is required for {action!r}.")

    if action == "list":
        return await _list(tenant_id, principal_id, target_schema)
    if action == "create":
        return await _create(tenant_id, principal_id, name, target_schema)
    if action == "rename":
        return await _rename(tenant_id, principal_id, dashboard_id, name)
    if action == "delete":
        return await _delete(tenant_id, principal_id, dashboard_id)
    return await _layout(tenant_id, principal_id, dashboard_id, items)
