"""
orchestrator/schema_retriever.py
SchemaRetriever seam (A8) — the CSR-RAG slot (Appendix 4). Built here, empty
behind it: only DirectSchemaRetriever ships in C1. A future
HypergraphSchemaRetriever (schema_retriever.provider = "hypergraph") would
plug in here without changing SQL Agent call sites.

Resolver key: schema_retriever.provider, in config/flags.toml (a flag, unlike
memory.provider — see memory_store.py for that distinction).

DirectSchemaRetriever does exactly what the Phase B SQL Agent did inline:
schema.describe_table for every given table, formatted into one context
string. Extracting it behind this interface must not change that behavior —
it is still the path taken on a memory miss or with memory.enabled = false.
"""

from __future__ import annotations

import logging
import re
import time
from functools import lru_cache

import httpx
from typing import Protocol, runtime_checkable

from typing import Any

from column_profiles import get_profiles
from config_loader import cfg, flags
from mcp_client import MCPSession, data_run_select, data_sample_rows, schema_describe_table
from table_selection import build_value_index, select, text_columns, value_index_sql, values_per_column

logger = logging.getLogger(__name__)

_VALID_PROVIDERS = frozenset({"direct", "linked"})

# The schema the agent is pointed at. "public" is A5's application schema and
# the shipped default; "olist" is the real-data corpus (migration 007). Only
# `public` participates in the A5 fingerprint -- see tests/test_schema_scoping.py.
DEFAULT_SCHEMA = "public"


def target_schema() -> str:
    """Which corpus the SQL agent should be shown, from config."""
    return str(getattr(flags.schema_retriever, "target_schema", DEFAULT_SCHEMA))


@runtime_checkable
class SchemaRetriever(Protocol):
    async def retrieve(
        self, session: MCPSession, table_names: list[str],
        schema: str | None = None, **context: Any,
    ) -> tuple[str, list[dict]]:
        """
        `context` (keyword-only, optional): question, attempt, must_include, fingerprint, used by
        the "linked" provider to select tables (docs/specs/table_selection.md); "direct" ignores it.

        `schema` selects the corpus (None = target_schema()); the Protocol
        omitted it while its only implementation required it (audit 007
        F-007-5), so a second implementation written to the Protocol would
        have silently ignored the per-request corpus.

        Returns (formatted schema-context string, raw describe_table
        responses). The raw responses (one dict per table, each with the
        A4.2 shape -- table/columns/foreign_keys/fingerprint) let callers
        pick interrogation targets (C2: nullable columns) without a second
        round of schema.describe_table calls.
        """
        ...


_PLAIN_IDENT = re.compile(r"[a-z_][a-z0-9_]*")


def sql_ident(name: str) -> str:
    """A name as SQL must write it: as is when lower-case, else double-quoted. Postgres folds an
    unquoted name to lower case, so a model shown `OrderItems` would write a query that fails."""
    return name if _PLAIN_IDENT.fullmatch(name) else f'"{name}"'


def _format_table(tname: str, desc: dict) -> str:
    if "." in tname:
        schema_part, table_part = tname.split(".", 1)
        tname = f"{schema_part}.{sql_ident(table_part)}"
    else:
        tname = sql_ident(tname)
    col_lines = []
    for col in desc.get("columns", []):
        nullable = "NULL" if col.get("nullable") else "NOT NULL"
        comment = f"  -- {col['comment']}" if col.get("comment") else ""
        col_lines.append(f"    {sql_ident(col['name'])} {col['type']} {nullable}{comment}")
    fk_lines = []
    for fk in desc.get("foreign_keys", []):
        fk_lines.append(
            f"    FK: {tname}.{sql_ident(fk['column'])} -> {sql_ident(fk['references_table'])}."
            f"{sql_ident(fk['references_column'])}"
        )
    return (
        f"TABLE {tname}:\n" + "\n".join(col_lines) +
        ("\nFOREIGN KEYS:\n" + "\n".join(fk_lines) if fk_lines else "")
    )


class DirectSchemaRetriever:
    """schema.describe_table for every candidate table — the Phase B path."""

    async def retrieve(
        self, session: MCPSession, table_names: list[str],
        schema: str | None = None, **_context: Any,
    ) -> tuple[str, list[dict]]:
        schema_parts: list[str] = []
        descriptions: list[dict] = []
        skipped: list[str] = []
        for tname in table_names:
            try:
                desc = await schema_describe_table(session, tname, schema)
            except httpx.HTTPStatusError as exc:
                # One table the MCP refuses (an odd name, dropped meanwhile) must not sink the
                # question: it used to surface as "service unavailable". Skip it, keep the rest.
                logger.warning("schema_retriever: describe_table(%s) refused (%s); skipped", tname,
                               exc.response.status_code)
                skipped.append(tname)
                continue
            descriptions.append(desc)
            # QUALIFY the name when the corpus is not the default schema.
            # Without this the agent is handed "TABLE orders:" and writes
            # `FROM orders`, which resolves through search_path to PUBLIC --
            # so it queries the 550-row synthetic corpus while being scored
            # against 99,441 real rows. Observed exactly that on the first
            # Olist run before the qualification was added.
            label = tname if not schema or schema == DEFAULT_SCHEMA else f"{schema}.{tname}"
            schema_parts.append(_format_table(label, desc))
        if skipped and not descriptions:
            raise RuntimeError(f"no table could be described ({len(skipped)} refused)")
        return "\n\n".join(schema_parts), descriptions


class LinkedSchemaRetriever:
    """Table selection for large schemas (docs/specs/table_selection.md, orchestrator/table_selection.py).

    A schema of at most [table_selection].max_tables_full tables, or a call without a question, is
    shown whole through DirectSchemaRetriever. A larger one is described once per (schema,
    fingerprint), each table checked for rows with a one-row sample, and per question only the
    selected tables are shown, with a note that more exist."""

    def __init__(self) -> None:
        self._direct = DirectSchemaRetriever()
        self._cache: dict[tuple[str, str], tuple[dict[str, dict], set[str]]] = {}
        self._values: dict[tuple[str, str], dict[str, Any] | None] = {}

    async def _value_index(self, session: MCPSession, descriptions: dict[str, dict], schema: str | None,
                           fingerprint: str) -> dict[str, Any] | None:
        """The v3 value index, built once per (schema, fingerprint) through data.run_select (the
        Layer 1 gate, a read-only transaction, the statement timeout). A column whose query fails
        is left out; the index is best-effort and never blocks a question."""
        limits = cfg.table_selection
        if str(limits.value_index) != "on":
            return None
        key = (schema or "", fingerprint)
        if key in self._values and fingerprint:
            return self._values[key]
        values: dict[tuple[str, str], list[str]] = {}
        per_column = values_per_column(int(limits.value_index_values_per_column), int(cfg.mcp.max_rows_hard_cap))
        # Built inside the first question on this schema version, so it gets a time budget: the
        # columns not reached are left out (logged), and the partial index is cached.
        deadline = time.monotonic() + float(limits.value_index_time_budget_s)
        columns = text_columns(descriptions, int(limits.value_index_max_columns))
        for n, (table, column) in enumerate(columns):
            if time.monotonic() > deadline:
                logger.warning("schema_retriever: value index time budget spent after %d of %d columns", n, len(columns))
                break
            sql = value_index_sql(schema or DEFAULT_SCHEMA, table, column, int(limits.value_index_scan_rows), per_column)
            try:
                result = await data_run_select(session, sql, max_rows=per_column)
            except Exception as exc:  # noqa: BLE001 -- best-effort, see the docstring
                logger.warning("schema_retriever: value index skipped %s.%s: %s", table, column, exc)
                continue
            if result.get("ok"):
                values[(table, column)] = [str(r.get("v")) for r in result.get("rows") or [] if r.get("v") is not None]
        index = build_value_index(values, int(limits.value_index_value_chars),
                                  int(limits.value_index_max_words)) if values else None
        self._values[key] = index
        logger.info("schema_retriever: value index for %s: %d columns", schema or DEFAULT_SCHEMA, len(values))
        return index

    async def _catalog(self, session: MCPSession, table_names: list[str], schema: str | None,
                       fingerprint: str) -> tuple[dict[str, dict], set[str]]:
        key = (schema or "", fingerprint)
        if key in self._cache and fingerprint:
            return self._cache[key]
        descriptions: dict[str, dict] = {}
        empty: set[str] = set()
        for tname in table_names:
            try:
                desc = await schema_describe_table(session, tname, schema)
            except httpx.HTTPStatusError as exc:
                logger.warning("schema_retriever: describe_table(%s) refused (%s); skipped", tname,
                               exc.response.status_code)
                continue
            descriptions[tname] = desc
            first = [c["name"] for c in desc.get("columns", [])[:1]]
            try:
                sample = await data_sample_rows(session, tname, columns=first or None, limit=1, schema=schema)
                if not sample.get("rows"):
                    empty.add(tname)
            except httpx.HTTPStatusError:
                pass  # unknown emptiness: treat as non-empty
        self._cache[key] = (descriptions, empty)
        return descriptions, empty

    async def retrieve(
        self, session: MCPSession, table_names: list[str], schema: str | None = None, *,
        question: str | None = None, attempt: int = 0, must_include: list[str] | None = None,
        fingerprint: str = "", **_context: Any,
    ) -> tuple[str, list[dict]]:
        limits = cfg.table_selection
        if not question or len(table_names) <= int(limits.max_tables_full):
            return await self._direct.retrieve(session, table_names, schema)
        descriptions, empty = await self._catalog(session, table_names, schema, fingerprint)
        if not descriptions:
            raise RuntimeError(f"no table could be described ({len(table_names)} refused)")
        values = await get_profiles(session, schema or DEFAULT_SCHEMA, fingerprint, list(descriptions),
                                    list(descriptions.values()))
        index = await self._value_index(session, descriptions, schema, fingerprint)
        chosen = select(question, descriptions, values, int(limits.top_tables), int(limits.max_tables_selected),
                        int(limits.fk_hops) + max(0, attempt), must_include=must_include, empty=empty,
                        value_index=index, value_tables=int(limits.value_tables),
                        min_value_chars=int(limits.value_min_token_chars))
        parts = [f"(Showing {len(chosen)} of {len(descriptions)} tables: the ones most relevant to this question. "
                 "Other tables exist; use only the tables shown.)"]
        for tname in chosen:
            label = tname if not schema or schema == DEFAULT_SCHEMA else f"{schema}.{tname}"
            parts.append(_format_table(label, descriptions[tname]))
        logger.info("schema_retriever: linked selection %d of %d tables (attempt %d): %s",
                    len(chosen), len(descriptions), attempt, ", ".join(chosen))
        return "\n\n".join(parts), [descriptions[t] for t in chosen]


@lru_cache(maxsize=1)
def get_schema_retriever() -> "SchemaRetriever":
    provider: str = str(flags.schema_retriever.provider)
    if provider not in _VALID_PROVIDERS:
        raise RuntimeError(
            f"Unknown schema_retriever.provider {provider!r}. Valid values: "
            f"{sorted(_VALID_PROVIDERS)}. Check config/flags.toml [schema_retriever] provider."
        )
    logger.info("SchemaRetriever resolved: provider=%s", provider)
    return LinkedSchemaRetriever() if provider == "linked" else DirectSchemaRetriever()
