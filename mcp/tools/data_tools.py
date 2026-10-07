"""
mcp/tools/data_tools.py
MCP data tools: data.sample_rows (A4.3) and data.run_select (A4.4).

SELECT-only enforcement — three independent layers (A4.5):

  Layer 1 — statement gate (this file):
    Parse with pglast (real PostgreSQL parser, not regex).
    Reject unless single SELECT statement at the top of the parse tree.
    Reject data-modifying CTEs (WITH ... AS (INSERT ...)).
    Reject the full list of non-SELECT statement types.

  Layer 2 — session guards (db.py):
    Every connection: SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY
    statement_timeout, idle_in_transaction_session_timeout.
    Every request runs inside an explicit READ ONLY transaction, always rollback.

  Layer 3 — role grants (migrations/002_schema.sql):
    statesync_reader has CONNECT + USAGE + SELECT only.
    A write reaching the engine fails with SQLSTATE 42501.
    This is the layer the security claim rests on (B-V5).

A failed query returns ok: false with a populated error field.
It does NOT raise a transport-level fault (A4.4 contract).
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import psycopg

import connections
from db import get_connection
from config_loader import cfg as _cfg

logger = logging.getLogger(__name__)

# ── Layer 1: pglast parser gate ───────────────────────────────────────────────
try:
    import pglast
    from pglast import parse_sql
    from pglast.enums import NodeTag
    from pglast.visitors import Visitor
    _PGLAST_AVAILABLE = True
except ImportError:
    logger.warning(
        "pglast not available — Layer 1 parser gate is DISABLED. "
        "Install pglast to enable the parser gate."
    )
    _PGLAST_AVAILABLE = False
    Visitor = object  # type: ignore[assignment,misc]

# Statement types that must never appear in submitted SQL
_REJECTED_STATEMENT_TYPES = {
    "InsertStmt", "UpdateStmt", "DeleteStmt", "CreateStmt", "DropStmt",
    "AlterTableStmt", "TruncateStmt", "GrantStmt", "CopyStmt",
    "TransactionStmt", "CallStmt", "DoStmt",
    # EXPLAIN ANALYZE reaches the engine; plain EXPLAIN does not but we reject both
    "ExplainStmt",
}

# Hard caps, from config/statesync.toml (the single source; until 2026-10-02 these were
# Dockerfile ENV values that merely repeated the config and could drift from it).
_SAMPLE_ROWS_HARD_CAP: int = int(_cfg.mcp.sample_rows_hard_cap)
_MAX_ROWS_HARD_CAP:    int = int(_cfg.mcp.max_rows_hard_cap)
_MAX_ROWS_DEFAULT:     int = int(_cfg.mcp.max_rows_default)
_MAX_SQL_LENGTH:       int = int(_cfg.sql.max_sql_length)


# Functions SQL from the model may never call, on any dataset: file I/O,
# admin/superuser, network (dblink), long sleeps. Ordinary functions (COUNT,
# AVG, EXTRACT, NOW, string/date/math, ss_embed) stay callable. Applied in Layer
# 1, so it covers data.run_select, vector.hybrid_select, tile refresh, approved
# examples and templates alike (until 2026-10-01 it covered hybrid_select only).
DENIED_FUNCTIONS = frozenset({
    "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_ls_logdir",
    "pg_ls_waldir", "pg_ls_tmpdir", "pg_stat_file",
    "lo_import", "lo_export", "lo_read", "lo_write", "lo_open", "lo_creat",
    "dblink", "dblink_connect", "dblink_exec", "dblink_connect_u",
    "pg_sleep", "pg_sleep_for", "pg_sleep_until",
    "set_config", "pg_reload_conf", "pg_rotate_logfile",
    "pg_terminate_backend", "pg_cancel_backend",
    "pg_read_server_files", "pg_write_server_files", "pg_execute_server_program",
    "pg_promote", "pg_switch_wal", "pg_create_restore_point",
})


class _TableReferenceCollector(Visitor if _PGLAST_AVAILABLE else object):
    """
    Collects relation references (RangeVar.relname) and CTE definitions
    (CommonTableExpr.ctename) across the parsed query AST.

    Used by Layer 1 to enforce that every SELECT reads from at least one real
    table. Pure constant projections (e.g. fabricated credentials or shell output)
    and system introspection functions (e.g. current_user, inet_server_addr)
    reference no real tables and must be rejected at this gate.
    """

    def __init__(self) -> None:
        self.tables: set[str] = set()
        self.ctes: set[str] = set()
        self.schemas: set[str] = set()
        self.functions: set[str] = set()

    def visit_FuncCall(self, ancestors, node) -> None:  # noqa: N802 — pglast visitor naming
        names = [getattr(n, "sval", "") for n in (node.funcname or ())]
        if names:
            self.functions.add(names[-1].lower())

    def visit_RangeVar(self, ancestors, node) -> None:  # noqa: N802 — pglast visitor naming
        relname = getattr(node, "relname", None)
        if relname:
            self.tables.add(relname)
        schemaname = getattr(node, "schemaname", None)
        if schemaname:
            self.schemas.add(schemaname.lower())

    def visit_CommonTableExpr(self, ancestors, node) -> None:  # noqa: N802 — pglast visitor naming
        ctename = getattr(node, "ctename", None)
        if ctename:
            self.ctes.add(ctename)


def _check_layer1(sql: str) -> str | None:
    """
    Layer 1: Parse the SQL with pglast and validate the parse tree.

    Returns:
        None if the SQL is acceptable.
        A rejection reason string if it must be rejected.
    """
    if len(sql) > _MAX_SQL_LENGTH:
        return f"SQL exceeds maximum length of {_MAX_SQL_LENGTH} characters."

    if not _PGLAST_AVAILABLE:
        # If pglast is not installed, fall through to Layer 2/3.
        # Log a strong warning — this should never happen in production.
        logger.error(
            "SECURITY: pglast is not installed. Layer 1 parser gate is bypassed. "
            "Install pglast immediately."
        )
        return None

    try:
        parsed = pglast.parse_sql(sql)
    except pglast.Error as exc:
        return f"SQL parse error: {exc}"

    if parsed is None:
        return "Empty SQL statement."

    # Must be exactly one statement
    if len(parsed) != 1:
        return (
            f"Rejected: expected exactly 1 SQL statement, got {len(parsed)}. "
            "Multi-statement submissions are not permitted."
        )

    stmt = parsed[0]
    # The top-level node must be a SelectStmt
    stmt_type = type(stmt).__name__
    if stmt_type != "RawStmt":
        return f"Rejected: unexpected top-level node type {stmt_type!r}."

    inner = stmt.stmt
    inner_type = type(inner).__name__

    if inner_type in _REJECTED_STATEMENT_TYPES:
        return (
            f"Rejected: statement type {inner_type!r} is not permitted. "
            "Only SELECT statements are accepted."
        )

    if inner_type != "SelectStmt":
        return (
            f"Rejected: top-level statement must be SelectStmt, got {inner_type!r}."
        )

    # Check for data-modifying CTEs: WITH x AS (INSERT ...) SELECT ...
    # This is the case a regex-based gate misses — pglast catches it.
    select_stmt = inner
    if hasattr(select_stmt, "withClause") and select_stmt.withClause is not None:
        cte_list = select_stmt.withClause.ctes
        if cte_list:
            for cte in cte_list:
                if hasattr(cte, "ctequery"):
                    cte_query_type = type(cte.ctequery).__name__
                    if cte_query_type in _REJECTED_STATEMENT_TYPES:
                        return (
                            f"Rejected: data-modifying CTE ({cte_query_type!r}) "
                            "inside WITH clause is not permitted."
                        )

    # Ensure the query reads at least one real table.
    # Reject table-less SELECT statements (constant projections, system introspection).
    # CTE names defined in WITH clauses are not real schema tables and must be
    # subtracted so a CTE-wrapped constant cannot bypass this structural gate.
    collector = _TableReferenceCollector()
    collector(stmt)
    cte_names = {c.lower() for c in collector.ctes}
    real_tables = {
        r for r in collector.tables
        if r not in collector.ctes and r.lower() not in cte_names
    }
    if not real_tables:
        return (
            "Rejected: statement reads no table. Queries must read data from the "
            "schema rather than compute constants or server introspection. "
            "This rejection is based on query shape, not content safety."
        )

    denied = sorted(collector.functions & DENIED_FUNCTIONS)
    if denied:
        return f"Rejected: function {denied[0]!r} is not permitted."

    return None  # accepted


def check_internal_schemas(sql: str) -> str | None:
    """A refusal when SQL for a built-in dataset names one of StateSync's internal schemas.

    Not applied to an owner's own database (connections.py), whose schemas may
    legitimately be called anything.
    """
    if not _PGLAST_AVAILABLE:
        return None
    try:
        parsed = parse_sql(sql)
    except Exception:  # noqa: BLE001 - Layer 1 reports parse errors itself
        return None
    collector = _TableReferenceCollector()
    for raw in parsed:
        collector(raw)
    internal = sorted(collector.schemas & {s.lower() for s in _cfg.mcp.internal_schemas})
    if internal:
        return (f"Rejected: this query names {internal[0]!r}, which holds StateSync's own data; "
                "questions may read only their dataset's tables.")
    return None


def _classify_error(sqlstate: str | None, message: str, rows: list | None) -> dict:
    """
    Load the error taxonomy from config and classify the error.
    Kept inline here to avoid a circular import; the taxonomy file path
    is read from CONFIG_DIR env var.
    """
    import json
    config_dir = os.environ.get("CONFIG_DIR", "/app/config")
    taxonomy_path = os.path.join(config_dir, "error_taxonomy.json")
    try:
        with open(taxonomy_path) as f:
            taxonomy = json.load(f)
    except OSError:
        return {"category": "unclassified", "hint": "Error taxonomy not available."}

    # Check empty_result (classified success at engine level)
    if rows is not None and len(rows) == 0 and sqlstate is None:
        for rule in taxonomy.get("rules", []):
            if rule.get("match", {}).get("empty_result"):
                return {"category": rule["category"], "hint": rule["hint"]}

    for rule in taxonomy.get("rules", []):
        match = rule.get("match", {})
        if "sqlstate" in match and match["sqlstate"] == sqlstate:
            if "message_regex" in match:
                if re.search(match["message_regex"], message or "", re.IGNORECASE):
                    return {"category": rule["category"], "hint": rule["hint"]}
            else:
                return {"category": rule["category"], "hint": rule["hint"]}
        elif "message_regex" in match and "sqlstate" not in match:
            if re.search(match["message_regex"], message or "", re.IGNORECASE):
                return {"category": rule["category"], "hint": rule["hint"]}

    fallback = taxonomy.get("fallback", {})
    return {
        "category": fallback.get("category", "unclassified"),
        "hint": fallback.get("hint", "Unrecognized failure."),
    }


# ── A4.3 data.sample_rows ────────────────────────────────────────────────────
async def sample_rows(
    table: str,
    columns: list[str] | None = None,
    limit: int | None = None,
    schema: str | None = None,
) -> dict[str, Any]:
    """
    A4.3 data.sample_rows
    Hard-capped at SAMPLE_ROWS_HARD_CAP rows server-side (A4.3 spec).
    limit defaults to cfg.mcp.sample_rows_default_limit when not provided.
    """
    from tools.schema_tools import IDENTIFIER
    if not IDENTIFIER.match(table):
        raise ValueError(f"Invalid table name: {table!r}")
    # Schema qualification. `orders` exists in BOTH public (A5 synthetic) and
    # olist (real), with DIFFERENT columns -- so an unqualified sample silently
    # hits public and fails with "column customer_id does not exist" while the
    # agent believes it is inspecting the real corpus. Validated against the
    # same allowlist the discovery tools use, so this cannot become a way to
    # reach pg_catalog.
    from tools.schema_tools import ALLOWED_SCHEMAS, DEFAULT_SCHEMA, source
    schema = schema or DEFAULT_SCHEMA
    if schema not in ALLOWED_SCHEMAS and not connections.is_token(schema):
        raise ValueError(
            f"Schema {schema!r} is not allowed. Allowed: {sorted(ALLOWED_SCHEMAS)}.")

    # Enforce caps
    effective_limit_in = limit if limit is not None else _cfg.mcp.sample_rows_default_limit
    effective_limit = min(max(1, effective_limit_in), _SAMPLE_ROWS_HARD_CAP)

    if columns is not None and (not isinstance(columns, list) or not all(isinstance(c, str) for c in columns)):
        raise ValueError("columns must be a list of column names.")

    async with source(schema) as (conn, real_schema):
        col_list = "*"
        if columns:
            # A column is accepted when it EXISTS in this table (the catalog is the allowlist) and
            # is always quoted, so real-world names such as "Academic Year" or "Charter School (Y/N)"
            # work and nothing else can be interpolated. Until 2026-10-03 a strict identifier
            # pattern refused such names and one refused sample failed the whole question (BIRD).
            cur = await conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = %s",
                (real_schema, table))
            existing = {r[0] for r in await cur.fetchall()}
            unknown = [c for c in columns if c not in existing]
            if unknown:
                raise ValueError(f"Invalid column name: {unknown[0]!r}")
            col_list = ", ".join(connections.quote_ident(c) for c in columns[:_cfg.mcp.sample_rows_max_columns])
        sql = f'SELECT {col_list} FROM {connections.quote_ident(real_schema)}."{table}" LIMIT {effective_limit}'
        # Run through Layer 1 gate (even though we built the SQL, belt-and-suspenders)
        rejection = _check_layer1(sql)
        if rejection:
            raise ValueError(f"Internal gate rejection for sample query: {rejection}")
        cur = await conn.execute(sql)
        rows = await cur.fetchall()
        col_names = [desc.name for desc in cur.description] if cur.description else []

        result_rows = [dict(zip(col_names, row)) for row in rows]
        truncated = len(result_rows) >= effective_limit

        logger.debug(
            "data.sample_rows: table=%s.%s limit=%d returned=%d",
            schema, table, effective_limit, len(result_rows),
        )
        return {"rows": result_rows, "truncated": truncated}


# ── A4.4 data.run_select ─────────────────────────────────────────────────────
def _rows_result(cur: Any, rows: list, effective_max: int) -> dict[str, Any]:
    col_names = [desc.name for desc in cur.description] if cur.description else []
    return {"ok": True, "columns": col_names, "rows": [dict(zip(col_names, row)) for row in rows[:effective_max]],
            "row_count": min(len(rows), effective_max), "truncated": len(rows) > effective_max, "error": None}


async def _run_external(sql: str, dataset: str, effective_max: int) -> dict[str, Any]:
    """run_select on the owner's database (mcp/connections.py): map, gate again, run read-only."""
    try:
        async with connections.external(dataset) as (conn, real_schema):
            try:
                mapped = connections.map_sql(sql, dataset, real_schema)
            except connections.Refused as exc:
                return _refused(str(exc))
            rejection = _check_layer1(mapped)
            if rejection:
                return _refused(rejection)
            try:
                cur = await conn.execute(mapped)
                rows = await cur.fetchmany(effective_max + 1)
            except psycopg.Error as exc:
                sqlstate = getattr(exc, "sqlstate", None)
                logger.warning("data.run_select (connected %s): sqlstate=%s message=%s", dataset, sqlstate, exc)
                return {"ok": False, "columns": [], "rows": [], "row_count": 0, "truncated": False,
                        "error": {"sqlstate": sqlstate, "message": str(exc)}}
            result = _rows_result(cur, rows, effective_max)
            logger.info("data.run_select (connected %s): ok=True rows=%d truncated=%s", dataset,
                        result["row_count"], result["truncated"])
            return result
    except connections.Refused as exc:
        return {"ok": False, "columns": [], "rows": [], "row_count": 0, "truncated": False,
                "error": {"sqlstate": None, "message": str(exc)}}


def _refused(message: str) -> dict[str, Any]:
    return {"ok": False, "columns": [], "rows": [], "row_count": 0, "truncated": False,
            "error": {"sqlstate": None, "message": f"[Layer 1 Gate] {message}"}}


async def run_select(
    sql: str,
    max_rows: int = _MAX_ROWS_DEFAULT,
    dataset: str | None = None,
) -> dict[str, Any]:
    """
    A4.4 data.run_select — the only relational execution path.

    Returns ok: false with error on query failure — never raises a
    transport-level fault for query failures (A4.4 contract).
    JSON-RPC errors are reserved for protocol failures only.
    """
    # Enforce max_rows bounds
    effective_max = min(max(1, max_rows), _MAX_ROWS_HARD_CAP)

    # ── Layer 1: parser gate ──────────────────────────────────────────────────
    rejection = _check_layer1(sql)
    if rejection:
        logger.warning("Layer 1 rejected SQL: %s", rejection)
        return {
            "ok": False,
            "columns": [],
            "rows": [],
            "row_count": 0,
            "truncated": False,
            "error": {
                "sqlstate": None,
                "message": f"[Layer 1 Gate] {rejection}",
            },
        }

    # ── Which database (mcp/connections.py, rule 4) ──────────────────────────
    # A connected dataset's SQL runs on the owner's database, with its cn_
    # qualifier mapped to the real schema and gated again; SQL for a built-in
    # dataset may not name a connection at all.
    try:
        named, _ = connections.datasets_named(sql)
    except Exception:  # noqa: BLE001 - Layer 1 already parsed this SQL; never block on a second look
        named = set()
    if connections.is_token(dataset):
        return await _run_external(sql, dataset, effective_max)
    if named:
        return _refused("This query names a connected database; ask it in a workspace that uses that database.")
    internal = check_internal_schemas(sql)
    if internal:
        logger.warning("Layer 1 rejected SQL: %s", internal)
        return _refused(internal)

    # ── Layer 2 (enforced in db.get_connection) + Layer 3 (role grants) ──────
    async with get_connection() as conn:
        try:
            cur = await conn.execute(sql)
            rows = await cur.fetchmany(effective_max + 1)
            col_names = (
                [desc.name for desc in cur.description]
                if cur.description else []
            )
            truncated = len(rows) > effective_max
            result_rows = [dict(zip(col_names, row)) for row in rows[:effective_max]]

            # Empty result is classified (A3: "empty_result is a classified success")
            # We do NOT raise an error for empty; we return ok: true with empty rows.
            # The caller (correction loop in C2) handles empty_result classification.
            logger.info(
                "data.run_select: ok=True rows=%d truncated=%s",
                len(result_rows), truncated,
            )
            return {
                "ok": True,
                "columns": col_names,
                "rows": result_rows,
                "row_count": len(result_rows),
                "truncated": truncated,
                "error": None,
            }

        except psycopg.errors.InsufficientPrivilege as exc:
            # SQLSTATE 42501 — Layer 3 enforcement visible (B-V5)
            logger.warning("data.run_select: permission denied (42501): %s", exc)
            return {
                "ok": False,
                "columns": [],
                "rows": [],
                "row_count": 0,
                "truncated": False,
                "error": {"sqlstate": "42501", "message": str(exc)},
            }

        except psycopg.Error as exc:
            sqlstate = exc.sqlstate if hasattr(exc, "sqlstate") else None
            message = str(exc)
            logger.warning(
                "data.run_select: query error sqlstate=%s message=%s",
                sqlstate, message,
            )
            return {
                "ok": False,
                "columns": [],
                "rows": [],
                "row_count": 0,
                "truncated": False,
                "error": {"sqlstate": sqlstate, "message": message},
            }
