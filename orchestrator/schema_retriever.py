"""
orchestrator/schema_retriever.py
The database schema as the SQL writer reads it: every table with its columns, types, nullability,
comments and foreign keys, one block per table.

Table names are qualified with the dataset ("cn_4509b5c383744bb6.facts"): the MCP maps that name to the
connected database's real schema before running the query, so the model never has to know it.
"""

from __future__ import annotations

import logging
import re

import httpx

from mcp_client import MCPSession, schema_describe_table

logger = logging.getLogger(__name__)

_PLAIN_IDENT = re.compile(r"[a-z_][a-z0-9_]*")


def sql_ident(name: str) -> str:
    """A name as SQL must write it: as is when lower-case, else double-quoted. Postgres folds an
    unquoted name to lower case, so a model shown `OrderItems` would write a query that fails."""
    return name if _PLAIN_IDENT.fullmatch(name) else f'"{name}"'


def format_table_block(tname: str, desc: dict, schema: str | None = None) -> str:
    """One table: TABLE <dataset>.<name>, a line per column, then its foreign keys."""
    label = f"{schema}.{sql_ident(tname)}" if schema else sql_ident(tname)
    col_lines = []
    for col in desc.get("columns", []):
        nullable = "NULL" if col.get("nullable") else "NOT NULL"
        comment = f"  -- {col['comment']}" if col.get("comment") else ""
        col_lines.append(f"    {sql_ident(col['name'])} {col['type']} {nullable}{comment}")
    fk_lines = [
        f"    FK: {label}.{sql_ident(fk['column'])} -> {sql_ident(fk['references_table'])}."
        f"{sql_ident(fk['references_column'])}"
        for fk in desc.get("foreign_keys", [])
    ]
    return f"TABLE {label}:\n" + "\n".join(col_lines) + ("\nFOREIGN KEYS:\n" + "\n".join(fk_lines) if fk_lines else "")


async def describe_schema(session: MCPSession, table_names: list[str], schema: str) -> tuple[str, list[dict]]:
    """(the schema text for the prompt, the raw describe_table results). A table the MCP refuses to
    describe (an odd name, dropped meanwhile) is skipped rather than failing the question; if every
    table is refused, it raises."""
    blocks: list[str] = []
    descriptions: list[dict] = []
    skipped = 0
    for tname in table_names:
        try:
            desc = await schema_describe_table(session, tname, schema)
        except httpx.HTTPStatusError as exc:
            logger.warning("describe_table(%s) refused (%s); skipped", tname, exc.response.status_code)
            skipped += 1
            continue
        descriptions.append(desc)
        blocks.append(format_table_block(tname, desc, schema))
    if skipped and not descriptions:
        raise RuntimeError(f"no table could be described ({skipped} refused)")
    return "\n\n".join(blocks), descriptions
