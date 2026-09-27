"""
mcp/tools/history_tools.py
MCP history tools: history.append_turn, history.get_recent_turns,
history.check_session, history.get_turn_rows and history.list_sessions (scoped by
tenant and principal).

Backed by session_state.conversation_turns (migrations/011, 014) and
session_state.turn_rows (migration 015), via db.py's SEPARATE statesync_history pool
-- not the statesync_reader pool every other tool in this package uses. That role
has INSERT+SELECT on conversation_turns, and INSERT+SELECT+DELETE on turn_rows,
and nothing else (see migrations/011 and 015 headers for the full reasoning).

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

import json
import logging
from typing import Any

from psycopg.types.json import Jsonb

from config_loader import cfg
from db import get_history_connection
from session import get_tenant_id

logger = logging.getLogger(__name__)

# Algorithmic convergence parameters for byte-budget fitting (not configuration constants under A0.2).
_BYTE_FIT_DROP_FRACTION: float = 0.10  # noqa: literals - algorithmic convergence step, not configuration


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
    sql_rows_full: list[dict[str, Any]] | None = None,
    target_schema: str | None = None,
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

    Full result sets (migration 015) are persisted in session_state.turn_rows
    as an enrichment inside the same connection block. Clamped by row count
    (history_full_rows_max) and byte size (history_full_rows_max_bytes), followed
    by bounded retention pruning (history_full_rows_retention_days).

    THE TURN NUMBER IS ASSIGNED HERE, NOT BY THE CALLER (docs/V3_PLAN.md 0.1).
    The orchestrator used to send len(recent_turns) + 1, but recent turns are a
    3-turn window, so from the 5th question on it sent 4 again, hit the unique
    key, and ON CONFLICT DO NOTHING dropped the turn without an error. Now the
    insert computes MAX(turn_index) + 1 for the session in the same statement.
    Two concurrent writes can still pick the same number; the loser retries
    (mcp.history_append_max_attempts).

    A SESSION BELONGS TO THE PRINCIPAL WHO WROTE ITS FIRST TURN (0.2). session_id
    is client-generated, so another principal who learns it could otherwise have
    their turns silently dropped or interleaved. The insert only fires while
    every existing row in the session belongs to this principal; otherwise the
    result says so -- {"written": False, "error": ...} -- instead of a no-op.
    """
    tenant_id = get_tenant_id()

    preview = sql_rows_preview
    if preview is not None:
        cap = cfg.mcp.history_row_preview_max
        if len(preview) > cap:
            logger.info(
                "history.append_turn: clamping row preview %d -> %d (tenant=%s session=%s)",
                len(preview), cap, tenant_id, session_id,
            )
            preview = preview[:cap]

    params = {
        "tenant": tenant_id,
        "principal": principal_id,
        "session": session_id,
        "raw_query": raw_query,
        "rewritten_query": rewritten_query,
        "rewrite_applied": rewrite_applied,
        "context_guessed": context_guessed,
        "route": route,
        "executed_sql": executed_sql,
        "answer_text": answer_text,
        "chart": Jsonb(chart) if chart is not None else None,
        "sql_columns": Jsonb(sql_columns) if sql_columns is not None else None,
        "preview": Jsonb(preview) if preview is not None else None,
        "rows_count": sql_rows_count,
        "target_schema": target_schema,
    }
    max_attempts = cfg.mcp.history_append_max_attempts
    async with get_history_connection() as conn:
        row = None
        for attempt in range(max_attempts):
            # An aggregate with no GROUP BY yields exactly one row even for a new
            # session (MAX -> NULL -> 1, BOOL_AND -> NULL -> TRUE), and none when
            # HAVING fails because another principal owns the session. The casts
            # are needed because INSERT ... SELECT, unlike VALUES, does not infer
            # parameter types from the target columns.
            cur = await conn.execute(
                """
                INSERT INTO session_state.conversation_turns
                    (tenant_id, principal_id, session_id, turn_index, raw_query, rewritten_query,
                     rewrite_applied, context_guessed, route, executed_sql, answer_text,
                     chart, sql_columns, sql_rows_preview, sql_rows_count, target_schema)
                SELECT %(tenant)s, %(principal)s, %(session)s,
                       COALESCE(MAX(t.turn_index), 0) + 1,
                       %(raw_query)s::text, %(rewritten_query)s::text,
                       %(rewrite_applied)s::boolean, %(context_guessed)s::boolean,
                       %(route)s::text, %(executed_sql)s::text, %(answer_text)s::text,
                       %(chart)s::jsonb, %(sql_columns)s::jsonb, %(preview)s::jsonb,
                       %(rows_count)s::integer, %(target_schema)s::text
                FROM session_state.conversation_turns t
                WHERE t.tenant_id = %(tenant)s AND t.session_id = %(session)s
                HAVING COALESCE(BOOL_AND(t.principal_id = %(principal)s), TRUE)
                ON CONFLICT (tenant_id, session_id, turn_index) DO NOTHING
                RETURNING turn_id, computed_at, turn_index
                """,
                params,
            )
            row = await cur.fetchone()
            if row is not None:
                break

            owner = await _session_owner(conn, tenant_id, session_id)
            if owner is not None and owner != principal_id:
                logger.warning(
                    "history.append_turn: refused, session belongs to another principal "
                    "(tenant=%s session=%s principal=%s)",
                    tenant_id, session_id, principal_id,
                )
                return {"written": False, "error": "session_owned_by_another_principal",
                        "turn_id": None, "computed_at": None, "turn_index": None}
            logger.info(
                "history.append_turn: turn number taken by a concurrent write, retrying "
                "(attempt %d/%d, tenant=%s session=%s)",
                attempt + 1, max_attempts, tenant_id, session_id,
            )

        if row is None:
            logger.error(
                "history.append_turn: gave up after %d attempts; turn NOT recorded "
                "(tenant=%s session=%s)",
                max_attempts, tenant_id, session_id,
            )
            return {"written": False, "error": "turn_index_contention",
                    "turn_id": None, "computed_at": None, "turn_index": None}

        turn_id, computed_at, turn_index = row

        # Full result sets (migration 015): enrichment persisted in session_state.turn_rows
        # inside the same atomic connection block.
        if cfg.mcp.history_full_rows_enabled and sql_rows_full:
            initial_count = len(sql_rows_full)
            payload_rows = list(sql_rows_full)
            row_capped = False
            max_rows = cfg.mcp.history_full_rows_max
            if len(payload_rows) > max_rows:
                row_capped = True
                payload_rows = payload_rows[:max_rows]

            payload_bytes = json.dumps(payload_rows, default=str).encode("utf-8")
            byte_capped = False
            max_bytes = cfg.mcp.history_full_rows_max_bytes

            while payload_rows and len(payload_bytes) > max_bytes:
                byte_capped = True
                drop_count = max(1, int(len(payload_rows) * _BYTE_FIT_DROP_FRACTION))
                payload_rows = payload_rows[:-drop_count]
                payload_bytes = json.dumps(payload_rows, default=str).encode("utf-8")

            if not payload_rows:
                logger.info(
                    "history.append_turn: full rows discarded, byte cap %d bytes exceeded (tenant=%s session=%s turn=%d initial_rows=%d)",
                    max_bytes, tenant_id, session_id, turn_index, initial_count,
                )
            else:
                stored_row_count = len(payload_rows)
                byte_size = len(payload_bytes)

                if row_capped and byte_capped:
                    logger.info(
                        "history.append_turn: full rows truncated by row cap and byte cap %d -> %d (tenant=%s session=%s turn=%d)",
                        initial_count, stored_row_count, tenant_id, session_id, turn_index,
                    )
                elif row_capped:
                    logger.info(
                        "history.append_turn: full rows truncated by row cap %d -> %d (tenant=%s session=%s turn=%d)",
                        initial_count, stored_row_count, tenant_id, session_id, turn_index,
                    )
                elif byte_capped:
                    logger.info(
                        "history.append_turn: full rows truncated by byte cap %d -> %d (tenant=%s session=%s turn=%d)",
                        initial_count, stored_row_count, tenant_id, session_id, turn_index,
                    )

                await conn.execute(
                    """
                    INSERT INTO session_state.turn_rows
                        (turn_id, tenant_id, principal_id, rows, stored_row_count, byte_size)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (turn_id) DO NOTHING
                    """,
                    (turn_id, tenant_id, principal_id, Jsonb(payload_rows), stored_row_count, byte_size),
                )

        # Retention pruning: inline deletion of expired rows (migration 015).
        # Bounded by cfg.mcp.history_prune_batch_limit to avoid long transactions.
        if cfg.mcp.history_full_rows_enabled:
            try:
                cur_prune = await conn.execute(
                    """
                    DELETE FROM session_state.turn_rows
                    WHERE turn_id IN (
                        SELECT turn_id FROM session_state.turn_rows
                        WHERE computed_at < now() - make_interval(days => %s)
                        ORDER BY computed_at
                        LIMIT %s
                    )
                    """,
                    (cfg.mcp.history_full_rows_retention_days, cfg.mcp.history_prune_batch_limit),
                )
                if cur_prune.rowcount and cur_prune.rowcount > 0:
                    logger.info(
                        "history.append_turn: pruned %d expired turn rows",
                        cur_prune.rowcount,
                    )
            except Exception as exc:
                logger.warning(
                    "history.append_turn: pruning expired turn rows failed (non-fatal): %s",
                    exc,
                )

    return {"written": True, "turn_id": turn_id, "computed_at": computed_at.isoformat(),
            "turn_index": turn_index}


async def _session_head(conn: Any, tenant_id: str, session_id: str) -> tuple[str, str | None] | None:
    """(owner principal, dataset) from a session's first turn, or None if it has no turns."""
    cur = await conn.execute(
        """
        SELECT principal_id, target_schema FROM session_state.conversation_turns
        WHERE tenant_id = %s AND session_id = %s
        ORDER BY turn_index
        LIMIT 1
        """,
        (tenant_id, session_id),
    )
    row = await cur.fetchone()
    return (row[0], row[1]) if row else None


async def _session_owner(conn: Any, tenant_id: str, session_id: str) -> str | None:
    """The principal that owns a session (wrote its first turn), or None if it has no turns."""
    head = await _session_head(conn, tenant_id, session_id)
    return head[0] if head else None


async def check_session(session_id: str, principal_id: str) -> dict[str, Any]:
    """
    May this principal use this session_id? Called by the orchestrator before a
    query runs, so a session owned by someone else is refused up front rather
    than answered and then silently not recorded (docs/V3_PLAN.md 0.2).

    A session_id with no turns is usable: the web client generates the id, and
    its first write claims it. Also reports the dataset the conversation was
    started on (migration 018; None for older turns or a new session), so /query
    can refuse a follow-up aimed at a different dataset.
    Returns {"exists": bool, "usable": bool, "target_schema": str | None}.
    """
    tenant_id = get_tenant_id()
    async with get_history_connection() as conn:
        head = await _session_head(conn, tenant_id, session_id)
    owner, schema = head if head else (None, None)
    usable = owner is None or owner == principal_id
    return {"exists": owner is not None, "usable": usable,
            # never disclose another principal's dataset
            "target_schema": schema if usable else None}


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
                   chart, sql_columns, sql_rows_preview, sql_rows_count, target_schema
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
         chart, sql_columns, sql_rows_preview, sql_rows_count, target_schema) = r
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
            "target_schema": target_schema,
        })
    # rows[0] is the newest turn (DESC fetch), so this is correct however
    # small `limit` is -- the bug 0.1 fixed came from counting the window.
    next_turn_index = rows[0][0] + 1 if rows else 1
    return {"turns": turns, "count": len(turns), "next_turn_index": next_turn_index}


async def get_turn_rows(session_id: str, principal_id: str, turn_index: int) -> dict[str, Any]:
    """
    Fetch the full result-set rows for a specific conversation turn (migration 015).

    Scoped by tenant_id bound server-side from session context (A0.6) and
    principal_id from the authenticating orchestrator. Joins turn_rows to
    conversation_turns to enforce tenant and principal ownership; without this
    join filter, any caller could read arbitrary turn rows by guessing a turn_id.

    Returns {"rows": [...], "stored_row_count": int, "byte_size": int, "available": True}
    when present. When absent or pruned, returns {"rows": [], "stored_row_count": 0,
    "byte_size": 0, "available": False}. available: False is a normal, expected state,
    not an error; the caller should fall back to the turn's sql_rows_preview when
    available is False.
    """
    tenant_id = get_tenant_id()

    async with get_history_connection() as conn:
        cur = await conn.execute(
            """
            SELECT tr.rows, tr.stored_row_count, tr.byte_size
            FROM session_state.turn_rows tr
            JOIN session_state.conversation_turns ct ON tr.turn_id = ct.turn_id
            WHERE ct.tenant_id = %s AND ct.principal_id = %s AND ct.session_id = %s AND ct.turn_index = %s
            """,
            (tenant_id, principal_id, session_id, turn_index),
        )
        row = await cur.fetchone()

    if row is None:
        return {"rows": [], "stored_row_count": 0, "byte_size": 0, "available": False}

    rows, stored_row_count, byte_size = row
    return {
        "rows": rows if isinstance(rows, list) else [],
        "stored_row_count": stored_row_count,
        "byte_size": byte_size,
        "available": True,
    }


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
    shared tenant. Its HTTP surface (GET /sessions) is open to any authenticated
    principal and returns only that principal's conversations; the developer
    opt-in env var that used to gate it was removed once history became
    principal-scoped (migration 014).

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
                   (array_agg(raw_query ORDER BY turn_index))[1]   AS first_query,
                   (array_agg(target_schema ORDER BY turn_index))[1] AS target_schema
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
            "target_schema": target_schema,
        }
        for (session_id, turn_count, started_at, last_activity, first_query, target_schema) in rows
    ]
    return {"sessions": sessions, "count": len(sessions)}


_ALLOWED_OUTCOMES = frozenset({
    "allowed",
    "denied_auth",
    "denied_rate",
    "error",
    "denied_tenant",
    "denied_session",   # migration 016: a session id owned by another principal
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

