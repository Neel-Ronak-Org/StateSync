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

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import connections
from db import get_connection
from fingerprint import compute_fingerprint

logger = logging.getLogger(__name__)

# A5's application schema. Everything defaults to it, so every existing caller
# is byte-for-byte unaffected by the `schema` parameter added below.
DEFAULT_SCHEMA = "public"

# Schemas a caller may discover. An allowlist rather than free-form input:
# these names are interpolated into catalog queries as PARAMETERS (never
# formatted in), but an allowlist also stops discovery being pointed at
# pg_catalog or information_schema, which is reconnaissance, not analysis.
#
# WHY olist IS DISCOVERABLE BUT NOT PART OF A5. mcp/fingerprint.py hardcodes
# table_schema='public' in all three of its queries, so the A5 fingerprint is
# STRUCTURALLY incapable of seeing another schema -- listing olist cannot move
# it, and D4's validate-on-read is unaffected. That is asserted by a test, not
# assumed: tests/test_schema_scoping.py.
#
# Trajectory memory over a non-A5 schema is NOT meaningful (the memory key is
# the A5 fingerprint, which does not describe olist), so runs against olist
# must keep memory.enabled=false. Stated here because nothing enforces it.
# `live` (migration 020, Phase 2b): the simulator-fed demo data. Its sibling
# `live_sim` holds the simulator's future plans and is deliberately absent.
ALLOWED_SCHEMAS = frozenset({"public", "olist", "live"})


def _validate_schema(schema: str) -> None:
    """Refuse an unknown schema loudly. An unresolvable name is an error, not a
    silent fallback to public -- the same rule A8 applies to scorer names.
    A connected database's dataset name (cn_...) is resolved by `source`."""
    if schema not in ALLOWED_SCHEMAS and not connections.is_token(schema):
        raise ValueError(
            f"Schema {schema!r} is not discoverable. Allowed: "
            f"{sorted(ALLOWED_SCHEMAS)}. Add it to ALLOWED_SCHEMAS deliberately; "
            f"note that only 'public' participates in the A5 fingerprint."
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
    _validate_schema(schema)
    if connections.is_token(schema):
        # The owner's database: every readable table or view in the chosen
        # schema. Its fingerprint hashes that schema's columns (the A5
        # fingerprint describes our public schema only).
        async with source(schema) as (conn, real_schema):
            rows = await (await conn.execute(_EXTERNAL_TABLES, (real_schema,))).fetchall()
            columns = await (await conn.execute(_EXTERNAL_FINGERPRINT, (real_schema,))).fetchall()
        tables = [{"name": r[0], "row_estimate": int(r[1]), "comment": r[2]} for r in rows]
        fingerprint = hashlib.sha256(json.dumps(columns, separators=(",", ":")).encode()).hexdigest()
        return {"tables": tables, "fingerprint": fingerprint, "schema": schema}
    async with get_connection() as conn:
        # Table list with row estimates from pg_stat_user_tables.
        #
        # Extension-owned tables are excluded. PostGIS installs spatial_ref_sys
        # into `public` alongside the application's own tables, so a plain
        # nspname='public' filter surfaces it as if it were part of the A5
        # schema -- and the SchemaRetriever then spends a describe_table call on
        # it for every miss-path query, on both the control and treatment arms.
        # The filter is on extension OWNERSHIP (pg_depend deptype 'e'), not on
        # the name 'spatial_ref_sys': anything an installed extension creates in
        # public (PostGIS, pgvector) is by definition not part of the
        # application schema, and denylisting one name would silently let the
        # next one through.
        #
        # This deliberately does NOT touch fingerprint.py. The A5 fingerprint is
        # computed by a separate query there, so it is unchanged by this edit --
        # see docs/spec-gaps.md GAP-6 for the separate, pre-existing question of
        # whether spatial_ref_sys belongs in the fingerprint at all. Changing
        # that would invalidate every stored trajectory (D4), which is a
        # decision, not a cleanup.
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
        # `schema` is echoed so a caller can never mistake an olist listing for
        # the A5 one. The fingerprint is ALWAYS A5's (public), by construction.
        return {"tables": tables, "fingerprint": fingerprint, "schema": schema}


async def describe_table(table: str, schema: str = DEFAULT_SCHEMA) -> dict[str, Any]:
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
