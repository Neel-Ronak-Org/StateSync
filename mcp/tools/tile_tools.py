"""
mcp/tools/tile_tools.py
MCP tiles tool: dashboard tiles that re-run their SAVED SQL instead of asking
the model again (docs/V3_PLAN.md Phase 1, migration 017).

One JSON-RPC method, tiles.manage, takes an `action`: pin, list, get, refresh,
replace, update, confirm, unpin. One method rather than eight because the MCP
tool ceiling is a guard against API sprawl (mcp.tool_ceiling, raised for this
in docs/V3_PLAN.md's decision log), and Phase 3 needs room of its own.

Since migration 019 every tile sits on a dashboard (dashboard_tools.py) of the
same owner and dataset, and carries its presentation choice (viz) and grid slot
(layout). A pin without a dashboard goes to the dataset's first dashboard,
created as "Overview" if the owner has none.

THE SECURITY PROPERTIES, each enforced here rather than trusted from a caller:

1. A tile's SQL comes only from a conversation turn its owner already has.
   `pin` and `replace` take (session_id, turn_index), look the turn up scoped
   by tenant and principal, and copy the SQL the pipeline actually executed.
   No action accepts a SQL string, so this API cannot become a SQL console.
2. Every read and write filters on tenant_id (bound server-side from the
   session, A0.6) AND principal_id (supplied by the authenticating
   orchestrator). A tile_id alone never reaches a tile; someone else's tile
   looks exactly like one that does not exist.
3. A refresh runs the stored SQL through data.run_select: the same Layer 1
   parser gate, READ ONLY transaction and reader role every answer uses. The
   history pool, which can write, never executes tile SQL.
4. A schema change is caught before the numbers are trusted. Each version
   stores a fingerprint of the columns its SQL reads; a mismatch at refresh
   marks the tile needs_review and keeps the last good result instead of
   computing a possibly-wrong one. Only `confirm` (a person) clears it.
5. Stored results are capped by rows and bytes, the tile count per principal
   is capped, and refresh intervals are bounded (config [tiles]).

6. A pin made in a workspace (workspace_id given) also saves the answer as
   that workspace's approved example, source "tile" (docs/V3_PLAN.md 5.3:
   "pinned tiles become approved examples, so the chat and the dashboard
   agree"). Only when a person asked for it: the question is the stand-alone
   form the fast path matches, the turn must be a plain database answer
   (route "sql"; a hybrid turn's SQL answers only part of its question), and
   the workspace must be the owner's and use the turn's dataset. Build with AI
   pins without a workspace_id, so a drafted dashboard approves nothing. The
   example is best-effort: the tile is pinned either way and the result says
   what happened ("example"). Unpinning keeps the example; a thumbs-down or
   the workspace's Knowledge tab withdraws it.

Failures are data, not transport errors -- {"ok": False, "error": <code>,
"message": <text>} -- because the orchestrator's MCP client raises on any
non-200 response and needs to map these to HTTP statuses. Codes: not_found,
not_pinnable, invalid, limit_reached.
"""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import logging
import uuid
from typing import Any

from psycopg.types.json import Jsonb

import connections
from config_loader import cfg
from db import get_history_connection
from serialization import to_json_safe
from session import get_tenant_id
from tools.dashboard_tools import default_dashboard, owned_dashboard, resolve_layout
from tools.data_tools import _check_layer1, run_select
from tools.schema_tools import DEFAULT_SCHEMA, describe_table

try:
    import pglast
    from pglast.visitors import Visitor
    _PGLAST_AVAILABLE = True
except ImportError:  # pragma: no cover - pglast is a declared mcp dependency
    _PGLAST_AVAILABLE = False
    Visitor = object  # type: ignore[misc,assignment]

logger = logging.getLogger(__name__)

# Same convergence step history.append_turn uses to fit rows under a byte cap.
_BYTE_FIT_DROP_FRACTION: float = 0.10  # noqa: literals - algorithmic convergence step, not configuration

_ACTIONS = frozenset({"pin", "list", "get", "refresh", "replace", "update", "confirm", "unpin"})
_NEEDS_TILE_ID = _ACTIONS - {"pin", "list"}

# How a tile may be shown (migration 019's tiles_viz_ck repeats this list).
# NULL means "decided from the data" by the web app; "auto" in a request clears
# a stored choice back to NULL.
VIZ_KINDS = frozenset({"kpi", "kpi_trend", "line", "area", "bar", "hbar",
                       "donut", "breakdown", "heatmap", "scatter", "table"})
VIZ_AUTO = "auto"


# ── Pure helpers (no database; unit-tested directly) ─────────────────────────

class _RelationCollector(Visitor):
    """(schemaname, relname) of every relation a statement names, plus CTE names."""

    def __init__(self) -> None:
        self.relations: set[tuple[str | None, str]] = set()
        self.ctes: set[str] = set()

    def visit_RangeVar(self, ancestors, node) -> None:  # noqa: N802 - pglast visitor naming
        if getattr(node, "relname", None):
            self.relations.add((getattr(node, "schemaname", None), node.relname))

    def visit_CommonTableExpr(self, ancestors, node) -> None:  # noqa: N802 - pglast visitor naming
        if getattr(node, "ctename", None):
            self.ctes.add(node.ctename)


def _default_for(dataset: str | None) -> str:
    """Where an unqualified table name resolves: the connected dataset (its search_path is its
    chosen schema, mcp/connections.py) or, as for our reader role, DEFAULT_SCHEMA."""
    return dataset if connections.is_token(dataset) else DEFAULT_SCHEMA


def table_refs(sql: str, dataset: str | None = None) -> list[tuple[str, str]]:
    """(schema, table) for every real table the SQL reads: sorted, unique.

    An unqualified name resolves to DEFAULT_SCHEMA, as the reader role's
    search_path does. An unqualified name that matches a CTE is the CTE, not a
    table, and is left out.
    """
    if not _PGLAST_AVAILABLE:  # pragma: no cover
        raise RuntimeError("pglast is required to fingerprint tile SQL.")
    collector = _RelationCollector()
    for raw in pglast.parse_sql(sql):
        collector(raw.stmt)
    return sorted({
        (schema or _default_for(dataset), name)
        for schema, name in collector.relations
        if not (schema is None and name in collector.ctes)
    })


def fingerprint_of(described: list[tuple[str, str, list[dict]]]) -> str:
    """A stable hash of the columns a query reads.

    Covers each column's name, type and nullability, per table. Order-
    independent (tables and columns are sorted) and blind to column comments,
    so rewording a comment does not flag every tile as changed.
    """
    canonical = sorted(
        [schema, table, sorted([c["name"], c["type"], bool(c["nullable"])] for c in columns)]
        for schema, table, columns in described
    )
    return hashlib.sha256(json.dumps(canonical, separators=(",", ":")).encode()).hexdigest()


def chart_template(chart: dict | None) -> dict | None:
    """The chart spec with its data rows removed.

    The synthesizer binds data.values from the SQL result into every answer's
    chart; a tile stores the spec without them and the orchestrator binds the
    tile's current rows at read time, the same way.
    """
    if not chart:
        return None
    template = copy.deepcopy(chart)
    data = template.get("data")
    if isinstance(data, dict):
        data.pop("values", None)
    return template


def cap_rows(rows: list[dict]) -> tuple[list[dict], bool]:
    """Rows kept for a tile result, and whether any were dropped."""
    max_rows = int(cfg.tiles.result_max_rows)
    max_bytes = int(cfg.tiles.result_max_bytes)
    kept = list(rows[:max_rows])
    truncated = len(rows) > max_rows
    while kept and len(json.dumps(kept, default=str).encode("utf-8")) > max_bytes:
        truncated = True
        kept = kept[: -max(1, int(len(kept) * _BYTE_FIT_DROP_FRACTION))]
    return kept, truncated


def is_stale(last_attempt: dt.datetime | None, interval_s: int, now: dt.datetime) -> bool:
    """Due for a refresh: never attempted, or last attempted interval_s or more ago."""
    return last_attempt is None or (now - last_attempt).total_seconds() >= interval_s


def resolve_interval(value: Any, target_schema: str | None = None) -> int | None:
    """The refresh interval to store, or None when out of bounds or not a whole number."""
    if value is None:
        return default_interval(target_schema)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if int(cfg.tiles.min_refresh_s) <= value <= int(cfg.tiles.max_refresh_s):
        return value
    return None


def resolve_title(title: Any, fallback: str | None) -> str | None:
    """A trimmed title within the length cap, or None if an explicit title is blank."""
    if title is not None:
        if not isinstance(title, str) or not title.strip():
            return None
        text = title.strip()
    else:
        text = (fallback or "").strip() or "Untitled tile"
    return text[: int(cfg.tiles.title_max_chars)]


def resolve_viz(value: Any) -> tuple[bool, str | None]:
    """(valid, stored value) for a viz request: a kind, or "auto" (stored as NULL)."""
    if value == VIZ_AUTO:
        return True, None
    if isinstance(value, str) and value in VIZ_KINDS:
        return True, value
    return False, None


def title_fallback(raw_query: str | None, rewritten_query: str | None, rewrite_applied: bool | None) -> str | None:
    """The default title for a pinned turn.

    A follow-up ("And bottom?") only makes sense inside its conversation; the
    pipeline's rewrite of it ("Bottom 5 states by number of customers") stands
    on its own, so a pin uses that when a rewrite was applied.
    """
    if rewrite_applied and isinstance(rewritten_query, str) and rewritten_query.strip():
        return rewritten_query
    return raw_query


def default_interval(target_schema: str | None) -> int:
    """The refresh interval a pin gets when none is asked for ([tiles] default_refresh_s)."""
    return int(cfg.tiles.default_refresh_s)


def _fail(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": code, "message": message}


def _interval_message() -> str:
    return (f"refresh_interval_s must be a whole number of seconds between "
            f"{cfg.tiles.min_refresh_s} and {cfg.tiles.max_refresh_s}.")


def _error_text(error: Any) -> str:
    if isinstance(error, dict):
        return str(error.get("message") or error.get("detail") or error)
    return str(error)


# ── Database access (every statement scoped by tenant AND principal) ──────────

async def _fingerprint(sql: str, dataset: str | None = None) -> str | None:
    """Fingerprint of the columns `sql` reads, or None if any table is gone."""
    described = []
    for schema, table in table_refs(sql, dataset):
        try:
            desc = await describe_table(table, schema)
        except ValueError:
            return None
        described.append((schema, table, desc.get("columns", [])))
    return fingerprint_of(described)


async def _load_turn(tenant_id: str, principal_id: str, session_id: str, turn_index: int):
    """The owner's stored turn, or None: (executed_sql, chart, sql_columns, raw_query,
    rewritten_query, rewrite_applied, target_schema, route)."""
    async with get_history_connection() as conn:
        cur = await conn.execute(
            """
            SELECT executed_sql, chart, sql_columns, raw_query,
                   rewritten_query, rewrite_applied, target_schema, route
            FROM session_state.conversation_turns
            WHERE tenant_id = %s AND principal_id = %s AND session_id = %s AND turn_index = %s
            """,
            (tenant_id, principal_id, session_id, turn_index),
        )
        return await cur.fetchone()


async def _pinnable_sql(tenant_id: str, principal_id: str, session_id: Any, turn_index: Any):
    """Resolve a turn to (sql, chart, columns, title, fingerprint, target_schema, route), or a failure dict.
    The title doubles as the stand-alone question (a follow-up's rewrite), which is what a
    workspace example is matched on."""
    if not isinstance(session_id, str) or not session_id:
        return _fail("invalid", "session_id is required.")
    if isinstance(turn_index, bool) or not isinstance(turn_index, int):
        return _fail("invalid", "turn_index must be a whole number.")
    turn = await _load_turn(tenant_id, principal_id, session_id, turn_index)
    if turn is None:
        return _fail("not_found", "No such conversation turn.")
    sql, chart, columns, raw_query, rewritten_query, rewrite_applied, target_schema, route = turn
    if not sql:
        return _fail("not_pinnable", "Only answers computed from the database can be pinned.")
    rejection = _check_layer1(sql)
    if rejection:
        return _fail("not_pinnable", f"This answer's SQL does not pass the SQL gate: {rejection}")
    fingerprint = await _fingerprint(sql, target_schema)
    if fingerprint is None:
        return _fail("not_pinnable", "A table this answer reads no longer exists.")
    return (sql, chart, columns, title_fallback(raw_query, rewritten_query, rewrite_applied),
            fingerprint, target_schema or DEFAULT_SCHEMA, route)


_VIEW_COLUMNS = """
    t.tile_id, t.title, t.state, t.current_version, t.refresh_interval_s,
    t.created_at, t.updated_at, t.last_error, t.error_at,
    v.sql_text, v.chart, v.sql_columns, v.source_session_id, v.source_turn_index,
    r.rows, r.row_count, r.truncated, r.computed_at, r.version,
    t.dashboard_id, t.target_schema, t.viz, t.layout_x, t.layout_y, t.layout_w, t.layout_h
"""
_VIEW_FROM = """
    FROM session_state.tiles t
    JOIN session_state.tile_versions v ON v.tile_id = t.tile_id AND v.version = t.current_version
    LEFT JOIN session_state.tile_results r ON r.tile_id = t.tile_id
"""
_SELECT_ONE = f"SELECT {_VIEW_COLUMNS} {_VIEW_FROM} WHERE t.tenant_id = %s AND t.principal_id = %s AND t.tile_id = %s"
_SELECT_ALL = f"SELECT {_VIEW_COLUMNS} {_VIEW_FROM} WHERE t.tenant_id = %s AND t.principal_id = %s ORDER BY t.created_at"
_SELECT_DASHBOARD = (f"SELECT {_VIEW_COLUMNS} {_VIEW_FROM} WHERE t.tenant_id = %s AND t.principal_id = %s "
                     "AND t.dashboard_id = %s ORDER BY t.created_at")


def _last_attempt(computed_at: dt.datetime | None, error_at: dt.datetime | None) -> dt.datetime | None:
    stamps = [s for s in (computed_at, error_at) if s is not None]
    return max(stamps) if stamps else None


def _to_view(row: tuple, now: dt.datetime) -> dict[str, Any]:
    (tile_id, title, state, version, interval, created_at, updated_at, last_error, error_at,
     sql_text, chart, sql_columns, source_session, source_turn,
     rows, row_count, truncated, computed_at, result_version,
     dashboard_id, target_schema, viz, layout_x, layout_y, layout_w, layout_h) = row
    return {
        "tile_id": tile_id,
        "dashboard_id": dashboard_id,
        "target_schema": target_schema,
        "viz": viz,
        "layout": None if layout_x is None else {"x": layout_x, "y": layout_y, "w": layout_w, "h": layout_h},
        "title": title,
        "state": state,
        "version": version,
        "refresh_interval_s": interval,
        "created_at": created_at.isoformat(),
        "updated_at": updated_at.isoformat(),
        "sql": sql_text,
        "chart": chart,
        "columns": sql_columns,
        "schemas": sorted({schema for schema, _ in table_refs(sql_text)}),
        "source": {"session_id": source_session, "turn_index": source_turn},
        "result": None if computed_at is None else {
            "rows": rows,
            "row_count": row_count,
            "truncated": truncated,
            "computed_at": computed_at.isoformat(),
            "version": result_version,
        },
        "stale": state != "needs_review" and is_stale(_last_attempt(computed_at, error_at), interval, now),
        "last_error": last_error,
        "error_at": error_at.isoformat() if error_at else None,
    }


async def _view(tenant_id: str, principal_id: str, tile_id: str, with_versions: bool = False):
    async with get_history_connection() as conn:
        row = await (await conn.execute(_SELECT_ONE, (tenant_id, principal_id, tile_id))).fetchone()
        if row is None:
            return None
        view = _to_view(row, dt.datetime.now(dt.timezone.utc))
        if with_versions:
            # Ownership was just established by the scoped query above.
            cur = await conn.execute(
                """
                SELECT version, created_at, source_session_id, source_turn_index
                FROM session_state.tile_versions WHERE tile_id = %s ORDER BY version
                """,
                (tile_id,),
            )
            view["versions"] = [
                {"version": v, "created_at": c.isoformat(),
                 "source": {"session_id": s, "turn_index": n}}
                for v, c, s, n in await cur.fetchall()
            ]
    return view


async def _refresh(tenant_id: str, principal_id: str, tile_id: str, force: bool) -> dict | None:
    """Re-run the tile's current SQL when it is due (or forced).

    Returns a failure dict only for not_found; a failed query or a detected
    schema change is recorded on the tile and is not an API failure.
    """
    async with get_history_connection() as conn:
        row = await (await conn.execute(
            """
            SELECT t.state, t.refresh_interval_s, t.current_version, t.error_at,
                   v.sql_text, v.fingerprint, r.computed_at, t.target_schema
            FROM session_state.tiles t
            JOIN session_state.tile_versions v ON v.tile_id = t.tile_id AND v.version = t.current_version
            LEFT JOIN session_state.tile_results r ON r.tile_id = t.tile_id
            WHERE t.tenant_id = %s AND t.principal_id = %s AND t.tile_id = %s
            """,
            (tenant_id, principal_id, tile_id),
        )).fetchone()
    if row is None:
        return _fail("not_found", "No such tile.")
    state, interval, version, error_at, sql, stored_fp, computed_at, dataset = row

    if state == "needs_review":
        return None                      # a person must confirm first
    if not force and not is_stale(_last_attempt(computed_at, error_at), interval,
                                  dt.datetime.now(dt.timezone.utc)):
        return None

    # Both of these read through the reader pool; no write connection is held.
    if await _fingerprint(sql, dataset) != stored_fp:
        async with get_history_connection() as conn:
            await conn.execute(
                """
                UPDATE session_state.tiles SET state = 'needs_review', updated_at = now()
                WHERE tenant_id = %s AND principal_id = %s AND tile_id = %s
                """,
                (tenant_id, principal_id, tile_id),
            )
        logger.warning("tiles: schema changed under tile %s; marked needs_review (tenant=%s)",
                       tile_id, tenant_id)
        return None

    result = await run_select(sql, max_rows=int(cfg.tiles.result_max_rows), dataset=dataset)

    async with get_history_connection() as conn:
        if result.get("ok"):
            rows, truncated = cap_rows(to_json_safe(result.get("rows") or []))
            await conn.execute(
                """
                INSERT INTO session_state.tile_results (tile_id, version, rows, row_count, truncated, computed_at)
                VALUES (%s, %s, %s, %s, %s, now())
                ON CONFLICT (tile_id) DO UPDATE SET
                    version = EXCLUDED.version, rows = EXCLUDED.rows, row_count = EXCLUDED.row_count,
                    truncated = EXCLUDED.truncated, computed_at = EXCLUDED.computed_at
                """,
                (tile_id, version, Jsonb(rows), len(rows), truncated or bool(result.get("truncated"))),
            )
            await conn.execute(
                """
                UPDATE session_state.tiles
                SET state = 'ok', last_error = NULL, error_at = NULL, updated_at = now()
                WHERE tenant_id = %s AND principal_id = %s AND tile_id = %s
                """,
                (tenant_id, principal_id, tile_id),
            )
        else:
            message = _error_text(result.get("error"))
            await conn.execute(
                """
                UPDATE session_state.tiles
                SET state = 'error', last_error = %s, error_at = now(), updated_at = now()
                WHERE tenant_id = %s AND principal_id = %s AND tile_id = %s
                """,
                (message, tenant_id, principal_id, tile_id),
            )
            logger.warning("tiles: refresh of %s failed (tenant=%s): %s", tile_id, tenant_id, message)
    return None


async def _record_failure(tenant_id: str, principal_id: str, tile_id: str, exc: Exception) -> None:
    """Mark a tile whose refresh raised as failed, instead of failing the whole list."""
    logger.error("tiles: refresh of %s raised (tenant=%s): %s", tile_id, tenant_id, exc, exc_info=True)
    async with get_history_connection() as conn:
        await conn.execute(
            """
            UPDATE session_state.tiles
            SET state = 'error', last_error = %s, error_at = now(), updated_at = now()
            WHERE tenant_id = %s AND principal_id = %s AND tile_id = %s
            """,
            ("The refresh failed inside StateSync. It will be retried.", tenant_id, principal_id, tile_id),
        )


# ── Actions ───────────────────────────────────────────────────────────────────

class _Abort(Exception):
    """Raised inside a history transaction to roll it back and report a failure."""

    def __init__(self, failure: dict[str, Any]) -> None:
        super().__init__(failure.get("message"))
        self.failure = failure


async def _target_dashboard(conn, tenant_id, principal_id, dashboard_id, target_schema) -> str:
    """The dashboard a tile goes on: the one asked for (same owner, same dataset) or the default."""
    if dashboard_id is None:
        return await default_dashboard(conn, tenant_id, principal_id, target_schema)
    if not isinstance(dashboard_id, str) or not dashboard_id:
        raise _Abort(_fail("invalid", "dashboard_id must be a dashboard id."))
    owned = await owned_dashboard(conn, tenant_id, principal_id, dashboard_id)
    if owned is None:
        raise _Abort(_fail("not_found", "No such dashboard."))
    if owned[1] != target_schema:
        raise _Abort(_fail("invalid", f"This answer is from the {target_schema} dataset; "
                                      f"that dashboard is for {owned[1]}."))
    return dashboard_id


async def _pin(tenant_id, principal_id, session_id, turn_index, title, refresh_interval_s,
               dashboard_id=None, viz=None, layout=None, workspace_id=None):
    ok_viz, stored_viz = resolve_viz(viz) if viz is not None else (True, None)
    if not ok_viz:
        return _fail("invalid", f"viz must be one of {sorted(VIZ_KINDS)} or {VIZ_AUTO!r}.")
    slot = None
    if layout is not None:
        slot = resolve_layout(layout)
        if slot is None:
            return _fail("invalid", "layout must be {x, y, w, h} inside the dashboard grid.")
    resolved = await _pinnable_sql(tenant_id, principal_id, session_id, turn_index)
    if isinstance(resolved, dict):
        return resolved
    sql, chart, columns, fallback_title, fingerprint, target_schema, route = resolved
    interval = resolve_interval(refresh_interval_s, target_schema)
    if interval is None:
        return _fail("invalid", _interval_message())
    tile_title = resolve_title(title, fallback_title)
    if tile_title is None:
        return _fail("invalid", "title must not be blank.")

    tile_id = str(uuid.uuid4())
    try:
        async with get_history_connection() as conn:
            count = (await (await conn.execute(
                "SELECT count(*) FROM session_state.tiles WHERE tenant_id = %s AND principal_id = %s",
                (tenant_id, principal_id),
            )).fetchone())[0]
            if count >= int(cfg.tiles.max_per_principal):
                raise _Abort(_fail("limit_reached",
                                   f"You already have {count} tiles; the limit is {cfg.tiles.max_per_principal}."))
            board = await _target_dashboard(conn, tenant_id, principal_id, dashboard_id, target_schema)
            on_board = (await (await conn.execute(
                "SELECT count(*) FROM session_state.tiles "
                "WHERE tenant_id = %s AND principal_id = %s AND dashboard_id = %s",
                (tenant_id, principal_id, board),
            )).fetchone())[0]
            if on_board >= int(cfg.dashboards.max_tiles_per_dashboard):
                raise _Abort(_fail("limit_reached",
                                   f"That dashboard already has {on_board} tiles; "
                                   f"the limit is {cfg.dashboards.max_tiles_per_dashboard}."))
            await conn.execute(
                """
                INSERT INTO session_state.tiles
                    (tile_id, tenant_id, principal_id, title, current_version, refresh_interval_s,
                     dashboard_id, target_schema, viz, layout_x, layout_y, layout_w, layout_h)
                VALUES (%s, %s, %s, %s, 1, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (tile_id, tenant_id, principal_id, tile_title, interval, board, target_schema, stored_viz,
                 *(slot[k] if slot else None for k in ("x", "y", "w", "h"))),
            )
            await conn.execute(
                """
                INSERT INTO session_state.tile_versions
                    (tile_id, version, sql_text, chart, sql_columns, fingerprint,
                     source_session_id, source_turn_index)
                VALUES (%s, 1, %s, %s, %s, %s, %s, %s)
                """,
                (tile_id, sql, Jsonb(chart_template(chart)) if chart else None,
                 Jsonb(columns) if columns is not None else None, fingerprint,
                 session_id, turn_index),
            )
    except _Abort as abort:
        return abort.failure
    logger.info("tiles: pinned %s from session=%s turn=%s to dashboard %s (tenant=%s principal=%s)",
                tile_id, session_id, turn_index, board, tenant_id, principal_id)
    await _refresh(tenant_id, principal_id, tile_id, force=True)
    result: dict[str, Any] = {"ok": True, "tile": await _view(tenant_id, principal_id, tile_id, with_versions=True)}
    if workspace_id is not None:
        result["example"] = await _example_from_pin(tenant_id, principal_id, workspace_id, fallback_title,
                                                    sql, target_schema, route)
    return result


def example_skip_reason(question: str | None, route: str | None) -> str | None:
    """Why a pinned turn cannot become an approved example, or None if it can (property 6)."""
    if route != "sql":
        return "Only a plain database answer becomes an approved example; this one also used documents or no SQL."
    if not isinstance(question, str) or not question.strip():
        return "The answer has no question to match on."
    return None


async def _example_from_pin(tenant_id, principal_id, workspace_id, question, sql, target_schema, route):
    """{"saved": bool, "example_id"?: str, "reason"?: str}; never raises, never undoes the pin."""
    reason = example_skip_reason(question, route)
    if reason is not None:
        return {"saved": False, "reason": reason}
    from tools.workspace_tools import add_pinned_example  # local: workspace_tools imports the data layer too
    try:
        added = await add_pinned_example(tenant_id, principal_id, workspace_id, question, sql, target_schema)
    except Exception as exc:  # noqa: BLE001 -- the pin already succeeded; the example is best-effort
        logger.warning("tiles: approved example from pin failed (workspace=%s): %s", workspace_id, exc)
        return {"saved": False, "reason": "The approved example could not be saved."}
    if not added.get("ok"):
        return {"saved": False, "reason": added.get("message") or "The approved example could not be saved."}
    return {"saved": True, "example_id": added["example_id"]}


async def _new_version(tenant_id, principal_id, tile_id, sql, chart, columns, fingerprint,
                       session_id, turn_index):
    """Append a version and make it current, serialised on the tile row. None if not found."""
    async with get_history_connection() as conn:
        row = await (await conn.execute(
            """
            SELECT current_version FROM session_state.tiles
            WHERE tenant_id = %s AND principal_id = %s AND tile_id = %s
            FOR UPDATE
            """,
            (tenant_id, principal_id, tile_id),
        )).fetchone()
        if row is None:
            return None
        version = row[0] + 1
        await conn.execute(
            """
            INSERT INTO session_state.tile_versions
                (tile_id, version, sql_text, chart, sql_columns, fingerprint,
                 source_session_id, source_turn_index)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (tile_id, version, sql, Jsonb(chart) if chart else None,
             Jsonb(columns) if columns is not None else None, fingerprint, session_id, turn_index),
        )
        await conn.execute(
            """
            UPDATE session_state.tiles
            SET current_version = %s, state = 'ok', last_error = NULL, error_at = NULL, updated_at = now()
            WHERE tenant_id = %s AND principal_id = %s AND tile_id = %s
            """,
            (version, tenant_id, principal_id, tile_id),
        )
    return version


async def _replace(tenant_id, principal_id, tile_id, session_id, turn_index):
    if await _view(tenant_id, principal_id, tile_id) is None:
        return _fail("not_found", "No such tile.")
    resolved = await _pinnable_sql(tenant_id, principal_id, session_id, turn_index)
    if isinstance(resolved, dict):
        return resolved
    sql, chart, columns, _title, fingerprint, _schema, _route = resolved
    version = await _new_version(tenant_id, principal_id, tile_id, sql, chart_template(chart),
                                 columns, fingerprint, session_id, turn_index)
    if version is None:
        return _fail("not_found", "No such tile.")
    await _refresh(tenant_id, principal_id, tile_id, force=True)
    return {"ok": True, "tile": await _view(tenant_id, principal_id, tile_id, with_versions=True)}


async def _confirm(tenant_id, principal_id, tile_id):
    """A person accepts the current schema: re-fingerprint as a new version, then refresh."""
    async with get_history_connection() as conn:
        row = await (await conn.execute(
            """
            SELECT v.sql_text, v.chart, v.sql_columns, v.source_session_id, v.source_turn_index, t.target_schema
            FROM session_state.tiles t
            JOIN session_state.tile_versions v ON v.tile_id = t.tile_id AND v.version = t.current_version
            WHERE t.tenant_id = %s AND t.principal_id = %s AND t.tile_id = %s
            """,
            (tenant_id, principal_id, tile_id),
        )).fetchone()
    if row is None:
        return _fail("not_found", "No such tile.")
    sql, chart, columns, session_id, turn_index, dataset = row
    fingerprint = await _fingerprint(sql, dataset)
    if fingerprint is None:
        return _fail("not_pinnable",
                     "A table this tile reads no longer exists. Replace it from a new answer instead.")
    version = await _new_version(tenant_id, principal_id, tile_id, sql, chart, columns, fingerprint,
                                 session_id, turn_index)
    if version is None:
        return _fail("not_found", "No such tile.")
    await _refresh(tenant_id, principal_id, tile_id, force=True)
    return {"ok": True, "tile": await _view(tenant_id, principal_id, tile_id, with_versions=True)}


async def _update(tenant_id, principal_id, tile_id, title, refresh_interval_s,
                  viz=None, dashboard_id=None):
    if title is None and refresh_interval_s is None and viz is None and dashboard_id is None:
        return _fail("invalid",
                     "Nothing to update: pass title, refresh_interval_s, viz and/or dashboard_id.")
    new_title = resolve_title(title, None) if title is not None else None
    if title is not None and new_title is None:
        return _fail("invalid", "title must not be blank.")
    interval = None
    if refresh_interval_s is not None:
        interval = resolve_interval(refresh_interval_s)
        if interval is None:
            return _fail("invalid", _interval_message())
    ok_viz, stored_viz = resolve_viz(viz) if viz is not None else (True, None)
    if not ok_viz:
        return _fail("invalid", f"viz must be one of {sorted(VIZ_KINDS)} or {VIZ_AUTO!r}.")
    try:
        async with get_history_connection() as conn:
            current = await (await conn.execute(
                """
                SELECT target_schema, dashboard_id FROM session_state.tiles
                WHERE tenant_id = %s AND principal_id = %s AND tile_id = %s
                FOR UPDATE
                """,
                (tenant_id, principal_id, tile_id),
            )).fetchone()
            if current is None:
                raise _Abort(_fail("not_found", "No such tile."))
            target_schema, current_board = current
            await conn.execute(
                """
                UPDATE session_state.tiles
                SET title = COALESCE(%s, title),
                    refresh_interval_s = COALESCE(%s, refresh_interval_s),
                    updated_at = now()
                WHERE tenant_id = %s AND principal_id = %s AND tile_id = %s
                """,
                (new_title, interval, tenant_id, principal_id, tile_id),
            )
            if viz is not None:
                await conn.execute(
                    "UPDATE session_state.tiles SET viz = %s WHERE tenant_id = %s AND principal_id = %s AND tile_id = %s",
                    (stored_viz, tenant_id, principal_id, tile_id),
                )
            if dashboard_id is not None and dashboard_id != current_board:
                board = await _target_dashboard(conn, tenant_id, principal_id, dashboard_id, target_schema)
                # A moved tile is placed afresh at the bottom of its new dashboard.
                await conn.execute(
                    """
                    UPDATE session_state.tiles
                    SET dashboard_id = %s, layout_x = NULL, layout_y = NULL, layout_w = NULL, layout_h = NULL
                    WHERE tenant_id = %s AND principal_id = %s AND tile_id = %s
                    """,
                    (board, tenant_id, principal_id, tile_id),
                )
    except _Abort as abort:
        return abort.failure
    return {"ok": True, "tile": await _view(tenant_id, principal_id, tile_id)}


async def _unpin(tenant_id, principal_id, tile_id):
    async with get_history_connection() as conn:
        row = await (await conn.execute(
            """
            DELETE FROM session_state.tiles
            WHERE tenant_id = %s AND principal_id = %s AND tile_id = %s
            RETURNING tile_id
            """,
            (tenant_id, principal_id, tile_id),
        )).fetchone()
    if row is None:
        return _fail("not_found", "No such tile.")
    logger.info("tiles: unpinned %s (tenant=%s principal=%s)", tile_id, tenant_id, principal_id)
    return {"ok": True, "tile_id": tile_id}


async def _list(tenant_id, principal_id, dashboard_id=None):
    """The principal's tiles (on one dashboard, if given), each refreshed first if it is due."""
    async with get_history_connection() as conn:
        if dashboard_id is not None:
            if not isinstance(dashboard_id, str) or not dashboard_id:
                return _fail("invalid", "dashboard_id must be a dashboard id.")
            if await owned_dashboard(conn, tenant_id, principal_id, dashboard_id) is None:
                return _fail("not_found", "No such dashboard.")
            ids = [r[0] for r in await (await conn.execute(
                "SELECT tile_id FROM session_state.tiles "
                "WHERE tenant_id = %s AND principal_id = %s AND dashboard_id = %s ORDER BY created_at",
                (tenant_id, principal_id, dashboard_id),
            )).fetchall()]
        else:
            ids = [r[0] for r in await (await conn.execute(
                "SELECT tile_id FROM session_state.tiles WHERE tenant_id = %s AND principal_id = %s "
                "ORDER BY created_at",
                (tenant_id, principal_id),
            )).fetchall()]
    for tile_id in ids:                  # bounded by tiles.max_per_principal
        try:
            await _refresh(tenant_id, principal_id, tile_id, force=False)
        except Exception as exc:  # noqa: BLE001 - one broken tile must not take the dashboard down
            await _record_failure(tenant_id, principal_id, tile_id, exc)
    now = dt.datetime.now(dt.timezone.utc)
    async with get_history_connection() as conn:
        if dashboard_id is not None:
            rows = await (await conn.execute(_SELECT_DASHBOARD, (tenant_id, principal_id, dashboard_id))).fetchall()
        else:
            rows = await (await conn.execute(_SELECT_ALL, (tenant_id, principal_id))).fetchall()
    return {"ok": True, "tiles": [_to_view(r, now) for r in rows]}


async def manage(
    action: str,
    principal_id: str,
    tile_id: str | None = None,
    session_id: str | None = None,
    turn_index: int | None = None,
    title: str | None = None,
    refresh_interval_s: int | None = None,
    dashboard_id: str | None = None,
    viz: str | None = None,
    layout: dict | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    """tiles.manage -- see the module docstring for the actions and guarantees."""
    tenant_id = get_tenant_id()
    if not isinstance(principal_id, str) or not principal_id:
        return _fail("invalid", "principal_id is required.")
    if action not in _ACTIONS:
        return _fail("invalid", f"Unknown action {action!r}. Valid: {sorted(_ACTIONS)}.")
    if action in _NEEDS_TILE_ID and (not isinstance(tile_id, str) or not tile_id):
        return _fail("invalid", f"tile_id is required for {action!r}.")

    if action == "pin":
        return await _pin(tenant_id, principal_id, session_id, turn_index, title, refresh_interval_s,
                          dashboard_id=dashboard_id, viz=viz, layout=layout, workspace_id=workspace_id)
    if action == "list":
        return await _list(tenant_id, principal_id, dashboard_id)
    if action == "replace":
        return await _replace(tenant_id, principal_id, tile_id, session_id, turn_index)
    if action == "update":
        return await _update(tenant_id, principal_id, tile_id, title, refresh_interval_s,
                             viz=viz, dashboard_id=dashboard_id)
    if action == "confirm":
        return await _confirm(tenant_id, principal_id, tile_id)
    if action == "unpin":
        return await _unpin(tenant_id, principal_id, tile_id)

    # get / refresh
    failure = await _refresh(tenant_id, principal_id, tile_id, force=(action == "refresh"))
    if failure:
        return failure
    view = await _view(tenant_id, principal_id, tile_id, with_versions=(action == "get"))
    if view is None:
        return _fail("not_found", "No such tile.")
    return {"ok": True, "tile": view}
