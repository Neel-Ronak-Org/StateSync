"""
mcp/tools/schema_tools.py
MCP schema tools: schema.list_tables (A4.1) and schema.describe_table (A4.2).

These tools are SELECT-only reads from information_schema and pg_constraint.
Both run through the standard get_connection() context manager (Layer 2).
Layer 3 (statesync_reader role) is always in effect.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from db import get_connection

logger = logging.getLogger(__name__)


async def list_tables() -> dict[str, Any]:
    """
    A4.1 schema.list_tables

    Returns:
        {
            "tables": [{"name": str, "row_estimate": int, "comment": str|null}],
            "fingerprint": str
        }

    The fingerprint returned here is the live schema fingerprint (same formula
    as fingerprint.py but computed fresh for schema caching in C1).
    """
    async with get_connection() as conn:
        # Table list with row estimates from pg_stat_user_tables
        query = """
            SELECT
                c.relname                          AS name,
                GREATEST(c.reltuples::bigint, 0)   AS row_estimate,
                obj_description(c.oid, 'pg_class') AS comment
            FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
              AND c.relkind = 'r'
              AND c.relname != 'schema_versions'
            ORDER BY c.relname
        """
        cur = await conn.execute(query)
        rows = await cur.fetchall()

        tables = [
            {
                "name": row[0],
                "row_estimate": int(row[1]),
                "comment": row[2],
            }
            for row in rows
        ]

        # Compute a lightweight fingerprint from table names + types for this response.
        # The full A5 fingerprint is computed at startup; this is for cache keying
        # in C1. For Phase B it mirrors the startup value.
        fp_input = json.dumps(
            sorted([t["name"] for t in tables]),
            separators=(",", ":"),
        )
        fingerprint = hashlib.sha256(fp_input.encode()).hexdigest()

        logger.debug("schema.list_tables: %d tables, fingerprint=%s", len(tables), fingerprint)
        return {"tables": tables, "fingerprint": fingerprint}


async def describe_table(table: str) -> dict[str, Any]:
    """
    A4.2 schema.describe_table

    Returns:
        {
            "table": str,
            "columns": [{"name", "type", "nullable": bool, "comment": str|null}],
            "foreign_keys": [{"column", "references_table", "references_column"}],
            "fingerprint": str
        }
    """
    # Input validation: table name must match the pattern in A4.2
    import re
    if not re.match(r'^[a-z_][a-z0-9_]{0,62}$', table):
        raise ValueError(f"Invalid table name: {table!r}")

    async with get_connection() as conn:
        # Columns
        cols_query = """
            SELECT
                a.attname                                   AS name,
                pg_catalog.format_type(a.atttypid, a.atttypmod) AS type,
                NOT a.attnotnull                            AS nullable,
                col_description(a.attrelid, a.attnum)      AS comment
            FROM pg_catalog.pg_attribute a
            JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
              AND c.relname = %s
              AND a.attnum > 0
              AND NOT a.attisdropped
            ORDER BY a.attnum
        """
        cur = await conn.execute(cols_query, (table,))
        col_rows = await cur.fetchall()

        if not col_rows:
            raise ValueError(f"Table not found: {table!r}")

        columns = [
            {
                "name":     row[0],
                "type":     row[1],
                "nullable": bool(row[2]),
                "comment":  row[3],
            }
            for row in col_rows
        ]

        # Foreign keys
        fk_query = """
            SELECT
                kcu.column_name         AS fk_col,
                ccu.table_name          AS ref_table,
                ccu.column_name         AS ref_col
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema    = kcu.table_schema
            JOIN information_schema.constraint_column_usage ccu
              ON ccu.constraint_name = tc.constraint_name
             AND ccu.table_schema   = tc.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_schema    = 'public'
              AND tc.table_name      = %s
            ORDER BY kcu.column_name
        """
        cur = await conn.execute(fk_query, (table,))
        fk_rows = await cur.fetchall()

        foreign_keys = [
            {
                "column":             row[0],
                "references_table":   row[1],
                "references_column":  row[2],
            }
            for row in fk_rows
        ]

        fp_input = json.dumps(
            {"table": table, "columns": [c["name"] for c in columns]},
            separators=(",", ":"),
        )
        fingerprint = hashlib.sha256(fp_input.encode()).hexdigest()

        logger.debug(
            "schema.describe_table: table=%s cols=%d fks=%d",
            table, len(columns), len(foreign_keys),
        )
        return {
            "table":        table,
            "columns":      columns,
            "foreign_keys": foreign_keys,
            "fingerprint":  fingerprint,
        }
