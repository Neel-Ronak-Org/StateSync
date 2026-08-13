"""
mcp/fingerprint.py
Schema fingerprint computation per the A5 specification.

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
A mismatch between the two is a startup failure (A5 spec requirement).
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

import psycopg

logger = logging.getLogger(__name__)

# Tables excluded from fingerprint computation (A5 spec)
_EXCLUDED_TABLES = frozenset({"schema_versions"})


async def compute_fingerprint(conn: psycopg.AsyncConnection) -> str:
    """
    Compute the schema fingerprint using the A5 formula.
    Must be called with a superuser or migrator connection (not statesync_reader)
    because it queries information_schema which reader can access but we need
    explicit schema access to pg_constraint.

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

        # Foreign keys via pg_constraint
        fk_query = """
            SELECT
                kcu.column_name AS fk_column,
                ccu.table_name  AS ref_table,
                ccu.column_name AS ref_column
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
              AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage ccu
              ON ccu.constraint_name = tc.constraint_name
              AND ccu.table_schema = tc.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_schema = 'public'
              AND tc.table_name = %s
            ORDER BY kcu.column_name
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
    migrator_conn: psycopg.AsyncConnection,
) -> str:
    """
    Compute the current fingerprint and compare against schema_versions.
    Raises RuntimeError on mismatch (startup failure per A5).

    Returns the verified fingerprint.
    """
    current_fp = await compute_fingerprint(migrator_conn)

    cur = await migrator_conn.execute(
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
