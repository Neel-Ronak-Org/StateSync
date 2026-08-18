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
from functools import lru_cache
from typing import Protocol, runtime_checkable

from config_loader import flags
from mcp_client import MCPSession, schema_describe_table

logger = logging.getLogger(__name__)

_VALID_PROVIDERS = frozenset({"direct"})

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
    ) -> tuple[str, list[dict]]:
        """
        Returns (formatted schema-context string, raw describe_table
        responses). The raw responses (one dict per table, each with the
        A4.2 shape -- table/columns/foreign_keys/fingerprint) let callers
        pick interrogation targets (C2: nullable columns) without a second
        round of schema.describe_table calls.
        """
        ...


def _format_table(tname: str, desc: dict) -> str:
    col_lines = []
    for col in desc.get("columns", []):
        nullable = "NULL" if col.get("nullable") else "NOT NULL"
        comment = f"  -- {col['comment']}" if col.get("comment") else ""
        col_lines.append(f"    {col['name']} {col['type']} {nullable}{comment}")
    fk_lines = []
    for fk in desc.get("foreign_keys", []):
        fk_lines.append(
            f"    FK: {tname}.{fk['column']} -> {fk['references_table']}.{fk['references_column']}"
        )
    return (
        f"TABLE {tname}:\n" + "\n".join(col_lines) +
        ("\nFOREIGN KEYS:\n" + "\n".join(fk_lines) if fk_lines else "")
    )


class DirectSchemaRetriever:
    """schema.describe_table for every candidate table — the Phase B path."""

    async def retrieve(
        self, session: MCPSession, table_names: list[str],
        schema: str | None = None,
    ) -> tuple[str, list[dict]]:
        schema_parts: list[str] = []
        descriptions: list[dict] = []
        for tname in table_names:
            desc = await schema_describe_table(session, tname, schema)
            descriptions.append(desc)
            # QUALIFY the name when the corpus is not the default schema.
            # Without this the agent is handed "TABLE orders:" and writes
            # `FROM orders`, which resolves through search_path to PUBLIC --
            # so it queries the 550-row synthetic corpus while being scored
            # against 99,441 real rows. Observed exactly that on the first
            # Olist run before the qualification was added.
            label = tname if not schema or schema == DEFAULT_SCHEMA else f"{schema}.{tname}"
            schema_parts.append(_format_table(label, desc))
        return "\n\n".join(schema_parts), descriptions


@lru_cache(maxsize=1)
def get_schema_retriever() -> "SchemaRetriever":
    provider: str = str(flags.schema_retriever.provider)
    if provider not in _VALID_PROVIDERS:
        raise RuntimeError(
            f"Unknown schema_retriever.provider {provider!r}. Valid values: "
            f"{sorted(_VALID_PROVIDERS)}. Check config/flags.toml [schema_retriever] provider."
        )
    logger.info("SchemaRetriever resolved: provider=%s", provider)
    return DirectSchemaRetriever()
