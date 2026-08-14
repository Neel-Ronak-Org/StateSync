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

# ── Config (loaded from env vars set by docker-compose, not from literals) ────
# The actual values come from config/statesync.toml via the MCP startup.
# We read them from environment variables here because the MCP service uses
# its own copy of the config loader.
_statement_timeout_ms: int = int(os.environ.get("STATEMENT_TIMEOUT_MS", "15000"))
_session_idle_timeout_ms: int = int(os.environ.get("SESSION_IDLE_TIMEOUT_MS", "60000"))
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
            yield conn
        finally:
            # Always rollback — Layer 2 guarantee (A4.5).
            await conn.execute("ROLLBACK")
