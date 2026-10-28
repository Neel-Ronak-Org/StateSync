"""
mcp/tools/data_tools.py
data.run_select and data.sample_rows: the only way a model-written query reaches a database.

A model's SQL is untrusted, so it passes three independent layers; any one of them alone stops a write:

  Layer 1, the statement gate (this file): the SQL is parsed with pglast, the real PostgreSQL parser (not a
    regex). It must be exactly one SELECT at the top of the parse tree: no second statement, no data-
    modifying CTE (WITH ... AS (INSERT ...)), no dangerous function (file access, sleeping, server
    administration), no StateSync internal schema, and it must read a table (a query that reads none
    usually means the database does not hold the answer).
  Layer 2, the session (db.py): a READ ONLY transaction with a statement time limit
    ([mcp] statement_timeout_ms), always rolled back.
  Layer 3, the login (migrations): the role the query runs as may only SELECT. A write that somehow reached
    the database would fail with SQLSTATE 42501.

For a workspace's connected database (cn_...) the query runs on the owner's database through
mcp/connections.py, whose login was checked to be read-only when it was connected.
A failed query returns ok: false with the error; it never raises a protocol-level fault.
"""

from __future__ import annotations

import logging
from typing import Any

import psycopg

import connections
from db import get_connection
from config_loader import cfg as _cfg

logger = logging.getLogger(__name__)

# ── Layer 1: the pglast parser gate ──────────────────────────────────────────
# A hard dependency: without the parser the server does not start, so layer 1 can never be silently skipped.
import pglast  # noqa: E402
from pglast import parse_sql  # noqa: E402
from pglast.visitors import Visitor  # noqa: E402

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


class _TableReferenceCollector(Visitor):
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


# ── data.sample_rows ─────────────────────────────────────────────────────────
async def sample_rows(
    table: str,
    columns: list[str] | None = None,
    limit: int | None = None,
    schema: str | None = None,
) -> dict[str, Any]:
    """
    data.sample_rows: a few rows of one table, so the SQL writer sees how values are stored.
    At most [mcp] sample_rows_hard_cap rows, whatever is asked.
    limit defaults to cfg.mcp.sample_rows_default_limit when not provided.
    """
    from tools.schema_tools import IDENTIFIER
    if not IDENTIFIER.match(table):
        raise ValueError(f"Invalid table name: {table!r}")
    # The schema is always named explicitly and checked against the same allowlist the schema tools use,
    # so a sample can never land in another schema or reach pg_catalog.
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


# ── data.run_select ──────────────────────────────────────────────────────────
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
    data.run_select: the only way a model's SQL runs (see the module docstring for the three layers).
    Returns ok: false with the error on a query failure; protocol errors are kept for protocol failures.
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

            # An empty result is ok: true with no rows; the SQL agent decides whether to retry.
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
            # SQLSTATE 42501: layer 3 (the read-only login) refused it
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
