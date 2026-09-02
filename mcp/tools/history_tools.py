"""
mcp/tools/history_tools.py
MCP history tools: history.append_turn, history.get_recent_turns and
history.list_sessions (scoped by tenant and principal).

Backed by session_state.conversation_turns (migrations/011, 014), via db.py's
SEPARATE statesync_history pool -- not the statesync_reader pool every other
tool in this package uses. That role has INSERT+SELECT on exactly this one
table and nothing else (see migrations/011's header for the full reasoning).

All queries below are fixed-shape and parameterized. Neither function
accepts, builds, or executes a SQL string from a caller -- there is no path
from an LLM's output to a statement run on this connection. That is the
actual guarantee here, the same way memory.put_trajectory's guarantee is
that `insight` never becomes a query, just a payload value (A4.8's
_SQL_REJECT_RE backstop). tenant_id comes from the session context (A0.6),
never a parameter, same as every other tool in this file's sibling modules.
principal_id is supplied by the authenticating orchestrator and scopes all
conversation turns and sessions per identity within the tenant.
"""

from __future__ import annotations

import logging
from typing import Any

from psycopg.types.json import Jsonb

from config_loader import cfg
from db import get_history_connection
from session import get_tenant_id

logger = logging.getLogger(__name__)


# NOTE on principal_id parameter passing:
# Unlike tenant_id, principal_id IS a caller-supplied parameter rather than being
# bound server-side from the session. That is deliberate and does not weaken A0.6:
# the MCP service has no notion of identity, it is an internal service on the
# compose network that already trusts the X-Tenant-ID header, and the ORCHESTRATOR
# is the component that authenticates and is therefore the authority on which
# principal a request belongs to. The trust boundary is unchanged; it has simply
# gained a second field.


async def append_turn(
    session_id: str,
    principal_id: str,
    turn_index: int,
    raw_query: str,
    rewritten_query: str,
    rewrite_applied: bool,
    context_guessed: bool,
    answer_text: str,
    route: str | None = None,
    executed_sql: str | None = None,
    chart: dict[str, Any] | None = None,
    sql_columns: list[str] | None = None,
    sql_rows_preview: list[dict[str, Any]] | None = None,
    sql_rows_count: int | None = None,
) -> dict[str, Any]:
    """
    Insert one completed turn scoped by tenant and principal. Called once per
    query, after the response that will actually reach the user has been
    validated (A7) -- so answer_text here is always the text the user saw,
    never a pre-repair draft. tenant_id is bound server-side; principal_id is
    forwarded by the authenticating orchestrator. A caller cannot write into
    another tenant's history by any parameter it controls.

    The artifact columns (migration 012) let a resumed conversation render
    what the user saw rather than a transcript of it. sql_rows_preview is
    CLAMPED here, not merely trusted from the caller: the cap is a storage
    invariant of this table, so it is enforced at the write, and
    sql_rows_count carries the true size so a truncated preview can never be
    presented as a complete result set.
    """
    tenant_id = get_tenant_id()

    preview = sql_rows_preview
    if preview is not None:
        cap = cfg.mcp.history_row_preview_max
        if len(preview) > cap:
            logger.info(
                "history.append_turn: clamping row preview %d -> %d (tenant=%s session=%s turn=%d)",
                len(preview), cap, tenant_id, session_id, turn_index,
            )
            preview = preview[:cap]
    async with get_history_connection() as conn:
        cur = await conn.execute(
            """
            INSERT INTO session_state.conversation_turns
                (tenant_id, principal_id, session_id, turn_index, raw_query, rewritten_query,
                 rewrite_applied, context_guessed, route, executed_sql, answer_text,
                 chart, sql_columns, sql_rows_preview, sql_rows_count)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, session_id, turn_index) DO NOTHING
            RETURNING turn_id, computed_at
            """,
            (tenant_id, principal_id, session_id, turn_index, raw_query, rewritten_query,
             rewrite_applied, context_guessed, route, executed_sql, answer_text,
             Jsonb(chart) if chart is not None else None,
             Jsonb(sql_columns) if sql_columns is not None else None,
             Jsonb(preview) if preview is not None else None,
             sql_rows_count),
        )
        row = await cur.fetchone()

    if row is None:
        # ON CONFLICT DO NOTHING fired -- this (tenant, session, turn_index)
        # was already written. Not an error (a retried request should not
        # duplicate a turn); the caller gets told rather than assuming success.
        logger.warning(
            "history.append_turn: turn already recorded, no-op (tenant=%s session=%s turn=%d)",
            tenant_id, session_id, turn_index,
        )
        return {"written": False, "turn_id": None, "computed_at": None}

    return {"written": True, "turn_id": row[0], "computed_at": row[1].isoformat()}


async def get_recent_turns(session_id: str, principal_id: str, limit: int | None = None) -> dict[str, Any]:
    """
    Return the last `limit` turns for (tenant_id, principal_id, session_id),
    OLDEST first (the order a rewrite prompt wants to read them in), scoped by
    tenant server-side and principal from the orchestrator. Empty list on turn 1
    / an unknown session_id -- not an error; a session with no history is the
    ordinary case, not a fault.
    """
    tenant_id = get_tenant_id()
    hard_cap = cfg.mcp.history_get_turns_hard_cap
    effective_limit = min(limit, hard_cap) if limit else hard_cap

    async with get_history_connection() as conn:
        cur = await conn.execute(
            """
            SELECT turn_index, raw_query, rewritten_query, rewrite_applied,
                   context_guessed, route, executed_sql, answer_text, computed_at,
                   chart, sql_columns, sql_rows_preview, sql_rows_count
            FROM session_state.conversation_turns
            WHERE tenant_id = %s AND principal_id = %s AND session_id = %s
            ORDER BY turn_index DESC
            LIMIT %s
            """,
            (tenant_id, principal_id, session_id, effective_limit),
        )
        rows = await cur.fetchall()

    turns = []
    for r in reversed(rows):  # DESC fetch, ASC (oldest-first) return
        (turn_index, raw_query, rewritten_query, rewrite_applied,
         context_guessed, route, executed_sql, answer_text, computed_at,
         chart, sql_columns, sql_rows_preview, sql_rows_count) = r
        turns.append({
            "turn_index": turn_index,
            "raw_query": raw_query,
            "rewritten_query": rewritten_query,
            "rewrite_applied": rewrite_applied,
            "context_guessed": context_guessed,
            "route": route,
            "executed_sql": executed_sql,
            "answer_text": answer_text,
            "computed_at": computed_at.isoformat(),
            # NULL means "written before migration 012", not "had no chart".
            # The caller distinguishes them: a turn with sql_columns but no
            # preview is a pre-012 turn; one with neither is a docs turn.
            "chart": chart,
            "sql_columns": sql_columns,
            "sql_rows_preview": sql_rows_preview,
            "sql_rows_count": sql_rows_count,
        })
    return {"turns": turns, "count": len(turns)}


async def list_sessions(principal_id: str, limit: int | None = None) -> dict[str, Any]:
    """
    Every conversation this principal has within the tenant, most recently
    active first, with the turn count and the opening question so a caller can
    label it.

    WHY THIS IS NOT SIMPLY THE MISSING HALF OF get_recent_turns. Every other
    read in this file is scoped to a session_id the caller already holds --
    it can only return a conversation you were already in. This one
    ENUMERATES, so it returns conversations the caller may never have had.
    On a single shared tenant that is every user's history, which is exactly
    why V2_STRATEGY §3b sequences auth BEFORE history: "whose history?" is
    unanswerable without identity, and this tool is where that becomes
    concrete rather than theoretical.

    The tool is scoped by tenant server-side (A0.6) and principal_id from the
    authenticating orchestrator, preventing cross-principal exposure within a
    shared tenant. The orchestrator keeps its HTTP surface behind an explicit
    opt-in env var (STATESYNC_SESSION_BROWSER) that is off unless a developer
    turns it on; registering the tool does not by itself publish anything.

    Fixed-shape and parameterized, same as its siblings: no caller string
    ever reaches the statement.
    """
    tenant_id = get_tenant_id()
    hard_cap = cfg.mcp.history_get_turns_hard_cap
    effective_limit = min(limit, hard_cap) if limit else hard_cap

    async with get_history_connection() as conn:
        cur = await conn.execute(
            """
            SELECT session_id,
                   count(*)                                        AS turn_count,
                   min(computed_at)                                AS started_at,
                   max(computed_at)                                AS last_activity,
                   (array_agg(raw_query ORDER BY turn_index))[1]   AS first_query
            FROM session_state.conversation_turns
            WHERE tenant_id = %s AND principal_id = %s
            GROUP BY session_id
            ORDER BY max(computed_at) DESC
            LIMIT %s
            """,
            (tenant_id, principal_id, effective_limit),
        )
        rows = await cur.fetchall()

    sessions = [
        {
            "session_id":    session_id,
            "turn_count":    turn_count,
            "started_at":    started_at.isoformat(),
            "last_activity": last_activity.isoformat(),
            "first_query":   first_query,
        }
        for (session_id, turn_count, started_at, last_activity, first_query) in rows
    ]
    return {"sessions": sessions, "count": len(sessions)}


_ALLOWED_OUTCOMES = frozenset({
    "allowed",
    "denied_auth",
    "denied_rate",
    "error",
    "denied_tenant",
})


async def record_audit(
    principal_id: str,
    route: str,
    outcome: str,
    query_id: str | None = None,
    session_id: str | None = None,
    client_ip: str | None = None,
    detail: str | None = None,
) -> dict[str, Any]:
    """
    Record an identity-scoped audit event in session_state.audit_log.

    tenant_id is bound server-side from session context (A0.6), never accepted
    as a caller parameter. outcome is validated against the allowed set matching
    the database CHECK constraint. detail is clamped to cfg.mcp.audit_detail_max_chars to
    prevent storage amplification vectors. Uses a single parameterized INSERT
    via get_history_connection() and returns the generated audit_id.
    """
    tenant_id = get_tenant_id()

    if outcome not in _ALLOWED_OUTCOMES:
        raise ValueError(
            f"Invalid outcome: {outcome!r}. Must be one of {sorted(_ALLOWED_OUTCOMES)}"
        )

    truncated_detail = detail[:cfg.mcp.audit_detail_max_chars] if detail is not None else None

    async with get_history_connection() as conn:
        cur = await conn.execute(
            """
            INSERT INTO session_state.audit_log
                (principal_id, tenant_id, route, outcome,
                 query_id, session_id, client_ip, detail)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING audit_id
            """,
            (
                principal_id,
                tenant_id,
                route,
                outcome,
                query_id,
                session_id,
                client_ip,
                truncated_detail,
            ),
        )
        row = await cur.fetchone()

    audit_id: int = int(row[0])  # type: ignore[index]
    logger.info(
        "audit.record: recorded audit %d (principal=%s tenant=%s route=%s outcome=%s)",
        audit_id,
        principal_id,
        tenant_id,
        route,
        outcome,
    )
    return {"ok": True, "audit_id": audit_id}

