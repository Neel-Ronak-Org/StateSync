"""
mcp/fingerprint.py
A fingerprint (a hash) of StateSync's own database schema, checked when the MCP starts.

fingerprint = sha256(
    canonical_json(
        sorted([
            { table, sorted([{ column, data_type, is_nullable }]),
              sorted([{ fk_column, ref_table, ref_column }]) }
            for each table in the application schema
        ])
    )
)

Excludes: indexes, comments, row counts, sequence values, schema_versions itself.
Includes: table names, column names, column types, nullability, foreign keys.

Computed:
  1. By the migration job (scripts/migrate.sh) before inserting into schema_versions.
  2. By this module at MCP startup.
A mismatch between the two is a startup failure.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

import psycopg

logger = logging.getLogger(__name__)

# Tables excluded from fingerprint computation
_EXCLUDED_TABLES = frozenset({"schema_versions"})


async def compute_fingerprint(conn: psycopg.AsyncConnection) -> str:
    """
    Compute the schema fingerprint.

    Called with the ordinary statesync_reader pool connection (db.get_connection()).
    The migration login must never enter a long-running service, and
    mcp is one, so this must not need a migrator connection — and it doesn't:
    information_schema and pg_constraint-derived views are visible to any role
    with SELECT on the underlying tables, which statesync_reader holds on every
    application table (Layer 3). mcp/tools/schema_tools.py already queries the
    same catalogs through this same reader pool on every live request.

    Returns:
        64-character lowercase hex SHA-256 string.
    """
    # Get all application tables (exclude system tables and excluded set)
    tables_query = """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_type = 'BASE TABLE'
        ORDER BY table_name
    """
    cur = await conn.execute(tables_query)
    all_tables = [row[0] for row in await cur.fetchall()]
    app_tables = [t for t in all_tables if t not in _EXCLUDED_TABLES]

    schema_rows: list[dict[str, Any]] = []

    for table in sorted(app_tables):
        # Columns
        cols_query = """
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = %s
            ORDER BY column_name
        """
        cur = await conn.execute(cols_query, (table,))
        columns = [
            {
                "column": row[0],
                "data_type": row[1],
                "is_nullable": row[2] == "YES",
            }
            for row in await cur.fetchall()
        ]

        # Foreign keys via pg_constraint -- NOT information_schema (see the
        # matching comment in mcp/tools/schema_tools.py's fk_query: those
        # views hide constraints from SELECT-only roles, which is exactly
        # what statesync_reader is by design). pg_constraint has no such
        # restriction, so this produces the same result under reader or
        # migrator -- required for the fingerprint to be a pure function of
        # schema shape, not of which role computed it.
        fk_query = """
            SELECT
                att2.attname AS fk_column,
                cl.relname   AS ref_table,
                att.attname  AS ref_column
            FROM pg_catalog.pg_constraint con
            JOIN pg_catalog.pg_class rel ON rel.oid = con.conrelid
            JOIN pg_catalog.pg_namespace nsp ON nsp.oid = rel.relnamespace
            JOIN pg_catalog.pg_class cl ON cl.oid = con.confrelid
            JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS ck(attnum, ord) ON true
            JOIN LATERAL unnest(con.confkey) WITH ORDINALITY AS cfk(attnum, ord) ON cfk.ord = ck.ord
            JOIN pg_catalog.pg_attribute att2 ON att2.attrelid = con.conrelid AND att2.attnum = ck.attnum
            JOIN pg_catalog.pg_attribute att ON att.attrelid = con.confrelid AND att.attnum = cfk.attnum
            WHERE con.contype = 'f'
              AND nsp.nspname = 'public'
              AND rel.relname = %s
            ORDER BY att2.attname
        """
        cur = await conn.execute(fk_query, (table,))
        foreign_keys = [
            {
                "fk_column": row[0],
                "ref_table": row[1],
                "ref_column": row[2],
            }
            for row in await cur.fetchall()
        ]

        schema_rows.append({
            "table": table,
            "columns": sorted(columns, key=lambda c: c["column"]),
            "foreign_keys": sorted(foreign_keys, key=lambda f: f["fk_column"]),
        })

    schema_rows_sorted = sorted(schema_rows, key=lambda r: r["table"])
    canonical = json.dumps(schema_rows_sorted, sort_keys=True, separators=(",", ":"))
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    logger.debug("Computed schema fingerprint: %s", fingerprint)
    return fingerprint


async def verify_fingerprint_at_startup(
    conn: psycopg.AsyncConnection,
) -> str:
    """
    Compute the current fingerprint and compare against schema_versions.
    Raises RuntimeError on a mismatch: the MCP refuses to start on a database its migrations do not describe.

    Called by mcp/main.py's lifespan with a connection from the ordinary
    statesync_reader pool (db.get_connection()) — see compute_fingerprint's
    docstring for why a migrator connection is neither needed nor permitted.

    Returns the verified fingerprint.
    """
    current_fp = await compute_fingerprint(conn)

    cur = await conn.execute(
        "SELECT fingerprint FROM schema_versions WHERE is_current = TRUE LIMIT 1"
    )
    row = await cur.fetchone()

    if row is None:
        raise RuntimeError(
            "MCP startup failure: schema_versions table has no current fingerprint row. "
            "Run migrations first (scripts/migrate.sh)."
        )

    stored_fp: str = row[0].strip()
    if current_fp != stored_fp:
        raise RuntimeError(
            f"MCP startup failure: schema fingerprint mismatch. "
            f"Stored: {stored_fp!r}. Computed: {current_fp!r}. "
            "This means the schema changed without a migration, or the migration "
            "fingerprint was not updated. Investigate before proceeding."
        )

    logger.info("Schema fingerprint verified at startup: %s", current_fp)
    return current_fp
