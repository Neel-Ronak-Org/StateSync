"""
mcp/db.py
Database connection pool for the MCP service.

Connects exclusively as statesync_reader (A1 / A4.5 Layer 3).
Every connection has session guards applied at checkout (Layer 2):
  - SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY
  - statement_timeout
  - idle_in_transaction_session_timeout

Password is read from a Docker secret file (never from an env var directly
in production — B-V4 pass condition requires zero credential material in
environment or logs).

Cycle 007 adds a SECOND, fully independent pool below (get_history_connection)
for statesync_history — the one role in this codebase with a write grant, and
it is granted on exactly one table (migrations/011). This is not an extension
of the reader pool: different role, different DSN, different secret, no
shared connections, and NOT wrapped in "BEGIN READ ONLY" -- deliberately, since
its entire purpose is to write. The A4.5 SELECT-only guarantee this file's
first half implements is about statesync_reader and the agent's SQL-execution
path (data.run_select); it is completely unchanged by the second half below.
What makes the second half safe is not a role grant alone -- it is that
mcp/tools/history_tools.py, the ONLY caller, builds fixed-shape parameterized
queries and never accepts or executes a SQL string handed to it by an LLM.
That invariant lives in history_tools.py, not here; this file only opens the
connection.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

import psycopg
from psycopg_pool import AsyncConnectionPool

from config_loader import cfg

logger = logging.getLogger(__name__)

# ── Config: config/statesync.toml [mcp], the single source (these were Dockerfile ENV
# values repeating the config until 2026-10-02) ─────────────────────────────────
_statement_timeout_ms: int = int(cfg.mcp.statement_timeout_ms)
_session_idle_timeout_ms: int = int(cfg.mcp.session_idle_timeout_ms)
_pool: AsyncConnectionPool | None = None


def _read_pg_password() -> str:
    """Read the statesync_reader password from Docker secret file."""
    secret_file = os.environ.get("PG_READER_PASSWORD_FILE", "/run/secrets/pg_reader_password")
    try:
        return open(secret_file).read().strip()
    except OSError:
        # Fallback for local dev without Docker secrets
        pw = os.environ.get("PG_READER_PASSWORD", "")
        if not pw:
            raise RuntimeError(
                "statesync_reader password not found. "
                "Set PG_READER_PASSWORD_FILE or PG_READER_PASSWORD."
            )
        return pw


def _build_dsn() -> str:
    """Build the postgres DSN for statesync_reader."""
    pg_host = os.environ.get("POSTGRES_HOST", "postgres")
    pg_port = os.environ.get("POSTGRES_PORT", "5432")
    pg_db   = os.environ.get("POSTGRES_DB", "statesync")
    pg_user = "statesync_reader"
    pg_pass = _read_pg_password()
    return f"postgresql://{pg_user}:{pg_pass}@{pg_host}:{pg_port}/{pg_db}"


async def _configure_connection(conn: psycopg.AsyncConnection) -> None:
    """
    Apply Layer 2 session guards to a newly-opened connection.
    Every connection opens with:
      - SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY
      - statement_timeout
      - idle_in_transaction_session_timeout
    Each request also runs inside an explicitly READ ONLY transaction
    that is always rolled back (enforced in get_connection context manager).
    """
    await conn.execute(
        "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY"
    )
    await conn.execute(
        f"SET statement_timeout = {_statement_timeout_ms}"
    )
    await conn.execute(
        f"SET idle_in_transaction_session_timeout = {_session_idle_timeout_ms}"
    )
    await conn.commit()
    logger.debug("Session guards applied to new connection.")


async def init_pool() -> None:
    """Initialize the async connection pool. Called at MCP startup."""
    global _pool
    dsn = _build_dsn()
    _pool = AsyncConnectionPool(
        conninfo=dsn,
        min_size=cfg.mcp.pool_min_size,
        max_size=cfg.mcp.pool_max_size,
        configure=_configure_connection,
        open=True,
    )
    logger.info("MCP database pool initialized (statesync_reader).")


async def close_pool() -> None:
    """Close the connection pool. Called at MCP shutdown."""
    global _pool
    if _pool:
        await _pool.close()
        logger.info("MCP database pool closed.")
    _pool = None


async def _bind_tenant(conn: psycopg.AsyncConnection) -> None:
    """Scope this transaction to the session's tenant for row-level security.

    Migration 021 puts RLS on every table in session_state and workspace_docs,
    with policies on current_setting('statesync.tenant_id'). set_config(...,
    true) lasts until the transaction ends. Outside a request there is no
    tenant, so the setting is empty and no row matches: fail closed.
    """
    from session import current_tenant_id  # local: session imports config only, but keep db import-light
    await conn.execute("SELECT set_config('statesync.tenant_id', %s, true)", (current_tenant_id() or "",))


@asynccontextmanager
async def get_connection() -> AsyncIterator[psycopg.AsyncConnection]:
    """
    Yield a connection from the pool inside a READ ONLY transaction
    that is always rolled back (Layer 2, A4.5).

    Usage:
        async with get_connection() as conn:
            rows = await conn.execute("SELECT ...").fetchall()
    """
    if _pool is None:
        raise RuntimeError("Database pool not initialized. Call init_pool() first.")

    async with _pool.connection() as conn:
        # Begin an explicit READ ONLY transaction for every request.
        await conn.execute("BEGIN READ ONLY")
        try:
            await _bind_tenant(conn)
            yield conn
        finally:
            # Always rollback — Layer 2 guarantee (A4.5).
            await conn.execute("ROLLBACK")


# ── statesync_history pool (cycle 007) ─────────────────────────────────────────
# Independent of everything above: own pool object, own role, own secret file,
# own DSN. Nothing here is reachable from get_connection() or vice versa.
_history_pool: AsyncConnectionPool | None = None


def _read_pg_history_password() -> str:
    """Read the statesync_history password from its own Docker secret file."""
    secret_file = os.environ.get("PG_HISTORY_PASSWORD_FILE", "/run/secrets/pg_history_password")
    try:
        return open(secret_file).read().strip()
    except OSError:
        pw = os.environ.get("PG_HISTORY_PASSWORD", "")
        if not pw:
            raise RuntimeError(
                "statesync_history password not found. "
                "Set PG_HISTORY_PASSWORD_FILE or PG_HISTORY_PASSWORD."
            )
        return pw


def _build_history_dsn() -> str:
    """Build the postgres DSN for statesync_history (migrations/011)."""
    pg_host = os.environ.get("POSTGRES_HOST", "postgres")
    pg_port = os.environ.get("POSTGRES_PORT", "5432")
    pg_db   = os.environ.get("POSTGRES_DB", "statesync")
    pg_user = "statesync_history"
    pg_pass = _read_pg_history_password()
    return f"postgresql://{pg_user}:{pg_pass}@{pg_host}:{pg_port}/{pg_db}"


async def _configure_history_connection(conn: psycopg.AsyncConnection) -> None:
    """
    Session guards for the history pool. Statement/idle timeouts apply the
    same as the reader pool; READ ONLY does not, and must not -- this
    connection's one job is to write to session_state.conversation_turns.
    """
    await conn.execute(
        f"SET statement_timeout = {_statement_timeout_ms}"
    )
    await conn.execute(
        f"SET idle_in_transaction_session_timeout = {_session_idle_timeout_ms}"
    )
    await conn.commit()
    logger.debug("Session guards applied to new history connection.")


async def init_history_pool() -> None:
    """Initialize the statesync_history pool. Called at MCP startup, alongside init_pool()."""
    global _history_pool
    dsn = _build_history_dsn()
    _history_pool = AsyncConnectionPool(
        conninfo=dsn,
        min_size=1,
        max_size=cfg.mcp.pool_max_size,
        configure=_configure_history_connection,
        open=True,
    )
    logger.info("MCP history database pool initialized (statesync_history).")


async def close_history_pool() -> None:
    """Close the statesync_history pool. Called at MCP shutdown."""
    global _history_pool
    if _history_pool:
        await _history_pool.close()
        logger.info("MCP history database pool closed.")
    _history_pool = None


@asynccontextmanager
async def get_history_connection() -> AsyncIterator[psycopg.AsyncConnection]:
    """
    Yield a connection from the statesync_history pool inside an ordinary
    (non-read-only) transaction that COMMITS on success. This is the only
    write-capable connection path in this codebase -- see this module's
    docstring for why it exists and what constrains it. Never used by any
    agent-facing tool; only mcp/tools/history_tools.py, tile_tools.py
    (user-owned tiles, migration 017) and dashboard_tools.py (user-owned
    dashboards, migration 019) and workspace_tools.py (workspaces, migration
    021) call this.
    """
    if _history_pool is None:
        raise RuntimeError("History database pool not initialized. Call init_history_pool() first.")

    async with _history_pool.connection() as conn:
        try:
            await _bind_tenant(conn)
            yield conn
            await conn.commit()
        except Exception:
            await conn.rollback()
            raise
