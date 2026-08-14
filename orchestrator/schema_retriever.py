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


@runtime_checkable
class SchemaRetriever(Protocol):
    async def retrieve(self, session: MCPSession, table_names: list[str]) -> str:
        """Returns a formatted schema-context string describing every given table."""
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

    async def retrieve(self, session: MCPSession, table_names: list[str]) -> str:
        schema_parts: list[str] = []
        for tname in table_names:
            desc = await schema_describe_table(session, tname)
            schema_parts.append(_format_table(tname, desc))
        return "\n\n".join(schema_parts)


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
