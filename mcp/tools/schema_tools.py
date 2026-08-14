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
from fingerprint import compute_fingerprint

logger = logging.getLogger(__name__)


async def list_tables() -> dict[str, Any]:
    """
    A4.1 schema.list_tables

    Returns:
        {
            "tables": [{"name": str, "row_estimate": int, "comment": str|null}],
            "fingerprint": str
        }

    The fingerprint returned here IS the A5 fingerprint (fingerprint.py's
    compute_fingerprint, the same function mcp startup verifies against
    schema_versions) -- not a separate approximation. This is what the SQL
    Agent passes to memory.get_trajectory/put_trajectory (C1), so D4's
    stale-trace guarantee depends on this being the real formula: a
    table-names-only stand-in would never change on a column rename, which
    is exactly the case C1-V4 exists to test.
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

        fingerprint = await compute_fingerprint(conn)

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

        # Foreign keys.
        #
        # Deliberately NOT information_schema.table_constraints /
        # key_column_usage / constraint_column_usage: per the SQL/PostgreSQL
        # spec, those views only surface constraints for a role that "owns or
        # has some privilege OTHER THAN SELECT" on the table. statesync_reader
        # holds SELECT only (Layer 3, by design) and so silently saw zero
        # foreign keys through that path -- a latent Phase B bug, invisible
        # until something actually asserted on foreign_keys content (C1's
        # fingerprint verification did). pg_constraint/pg_class/pg_attribute
        # are plain system catalogs with no such privilege-tier restriction,
        # same as the columns query above.
        fk_query = """
            SELECT
                att2.attname AS fk_col,
                cl.relname   AS ref_table,
                att.attname  AS ref_col
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
