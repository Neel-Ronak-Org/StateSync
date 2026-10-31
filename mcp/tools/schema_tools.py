"""
mcp/tools/schema_tools.py
schema.list_tables and schema.describe_table: what tables, columns and foreign keys a database has.

Read-only catalog queries (pg_class, pg_attribute, pg_constraint) inside a read-only transaction that is
rolled back, as the read-only login. For a workspace's connected database (a dataset named cn_<16 hex>)
they run on the owner's database through mcp/connections.py; for one of StateSync's own sample schemas, on
StateSync's Postgres.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import connections
from db import get_connection
from fingerprint import compute_fingerprint

logger = logging.getLogger(__name__)

# A table or column name the MCP accepts. Upper case is allowed (an owner's database may have "OrderItems");
# only letters, digits and underscores, so a name can never carry a quote: where a name is spliced into SQL
# it is double-quoted, which keeps its case and cannot be broken out of.
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

DEFAULT_SCHEMA = "public"

# StateSync's own sample schemas, readable for older conversations and pins; an allowlist, so discovery can
# never be pointed at pg_catalog or information_schema. New questions are asked only in workspaces, which sit
# on connected databases.
ALLOWED_SCHEMAS = frozenset({"public", "olist", "live"})


def _validate_schema(schema: str) -> None:
    """Refuse an unknown schema loudly, never fall back to another one. A connected database's dataset name
    (cn_...) is resolved by `source`."""
    if schema not in ALLOWED_SCHEMAS and not connections.is_token(schema):
        raise ValueError(
            f"Schema {schema!r} is not discoverable. Allowed: "
            f"{sorted(ALLOWED_SCHEMAS)} or a connected database."
        )


@asynccontextmanager
async def source(schema: str) -> AsyncIterator[tuple[Any, str]]:
    """(connection, schema to query) for a dataset: our own database for a built-in
    schema, the owner's database (mcp/connections.py) for a cn_ dataset. Both are
    READ ONLY transactions that are rolled back."""
    _validate_schema(schema)
    if connections.is_token(schema):
        try:
            async with connections.external(schema) as (conn, real_schema):
                yield conn, real_schema
        except connections.Refused as exc:
            raise ValueError(str(exc)) from None
    else:
        async with get_connection() as conn:
            yield conn, schema


_EXTERNAL_TABLES = """
    SELECT c.relname, GREATEST(c.reltuples::bigint, 0), obj_description(c.oid, 'pg_class')
    FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = %s AND c.relkind IN ('r', 'p', 'v', 'm')
      AND pg_catalog.has_table_privilege(c.oid, 'SELECT')
    ORDER BY c.relname
"""

_EXTERNAL_FINGERPRINT = """
    SELECT c.relname, a.attname, pg_catalog.format_type(a.atttypid, a.atttypmod)
    FROM pg_catalog.pg_attribute a
    JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = %s AND c.relkind IN ('r', 'p', 'v', 'm') AND a.attnum > 0 AND NOT a.attisdropped
    ORDER BY 1, 2
"""


async def list_tables(schema: str = DEFAULT_SCHEMA) -> dict[str, Any]:
    """schema.list_tables: {"tables": [{"name", "row_estimate", "comment"}], "fingerprint", "schema"}.
    The fingerprint is a hash of the schema's columns: it changes when a column is added, dropped or renamed."""
    _validate_schema(schema)
    if connections.is_token(schema):
        # The owner's database: every table or view the read-only login may read, in the chosen schema.
        async with source(schema) as (conn, real_schema):
            rows = await (await conn.execute(_EXTERNAL_TABLES, (real_schema,))).fetchall()
            columns = await (await conn.execute(_EXTERNAL_FINGERPRINT, (real_schema,))).fetchall()
        tables = [{"name": r[0], "row_estimate": int(r[1]), "comment": r[2]} for r in rows]
        fingerprint = hashlib.sha256(json.dumps(columns, separators=(",", ":")).encode()).hexdigest()
        return {"tables": tables, "fingerprint": fingerprint, "schema": schema}
    async with get_connection() as conn:
        # StateSync's own schema: its tables, without the ones an installed extension owns (PostGIS puts
        # spatial_ref_sys in public; it is not data anyone asks about).
        query = """
            SELECT
                c.relname                          AS name,
                GREATEST(c.reltuples::bigint, 0)   AS row_estimate,
                obj_description(c.oid, 'pg_class') AS comment
            FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = %s
              AND c.relkind = 'r'
              AND c.relname != 'schema_versions'
              AND NOT EXISTS (
                  SELECT 1
                  FROM pg_catalog.pg_depend d
                  JOIN pg_catalog.pg_extension e ON e.oid = d.refobjid
                  WHERE d.classid = 'pg_class'::regclass
                    AND d.objid   = c.oid
                    AND d.deptype = 'e'
              )
            ORDER BY c.relname
        """
        cur = await conn.execute(query, (schema,))
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
        # `schema` is echoed so a caller always knows which listing it got.
        return {"tables": tables, "fingerprint": fingerprint, "schema": schema}


async def describe_table(table: str, schema: str = DEFAULT_SCHEMA) -> dict[str, Any]:
    """
    schema.describe_table

    Returns:
        {
            "table": str,
            "columns": [{"name", "type", "nullable": bool, "comment": str|null}],
            "foreign_keys": [{"column", "references_table", "references_column"}],
            "fingerprint": str
        }
    """
    # The name must match IDENTIFIER (above).
    if not IDENTIFIER.match(table):
        raise ValueError(f"Invalid table name: {table!r}")
    async with source(schema) as (conn, schema):
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
            WHERE n.nspname = %s
              AND c.relname = %s
              AND a.attnum > 0
              AND NOT a.attisdropped
            ORDER BY a.attnum
        """
        cur = await conn.execute(cols_query, (schema, table))
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

        # Foreign keys, from pg_constraint. Not from information_schema: its constraint views show nothing
        # to a login that may only SELECT, which is exactly the login this runs as.
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
              AND nsp.nspname = %s
              AND rel.relname = %s
            ORDER BY att2.attname
        """
        cur = await conn.execute(fk_query, (schema, table))
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
