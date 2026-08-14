"""
orchestrator/memory_store.py
TrajectoryStore seam (A8) — AgentSM trajectory memory.

Resolver key: memory.provider, in config/statesync.toml (cfg, a locked
constant — unlike schema_retriever.provider this is NOT a flags.toml entry;
the C1 spec is explicit about that distinction). Shipped implementation:
QdrantTrajectoryStore.

Nodes call get_trajectory_store(), never mcp_client.memory_* directly and
never branch on cfg.memory.provider themselves (A8: no `if provider == "x"`
in agent logic). orchestrator never talks to Qdrant directly — mcp is the
sole Qdrant credential holder (A1); QdrantTrajectoryStore delegates to the
mcp memory.* tools over the existing MCP session.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Protocol, runtime_checkable

from config_loader import cfg
from mcp_client import MCPSession, memory_get_trajectory, memory_put_trajectory

logger = logging.getLogger(__name__)

_VALID_PROVIDERS = frozenset({"qdrant"})


@runtime_checkable
class TrajectoryStore(Protocol):
    """Minimal trajectory store interface. Adapters implement this; nodes call it."""

    async def get_trajectory(
        self,
        session: MCPSession,
        *,
        query_text: str,
        schema_fingerprint: str,
    ) -> dict[str, Any]:
        """Returns {"hit": bool, "rejected_stale": int, "candidates": [...]} (A4.8)."""
        ...

    async def put_trajectory(
        self,
        session: MCPSession,
        *,
        query_text: str,
        schema_fingerprint: str,
        insight: str,
        tables: list[str],
        join_path: list[str],
        dialect_notes: list[str] | None = None,
    ) -> dict[str, Any]:
        """Returns {"trace_id": str} (A4.8)."""
        ...


class QdrantTrajectoryStore:
    """Delegates to the mcp memory.get_trajectory / memory.put_trajectory tools."""

    async def get_trajectory(
        self, session: MCPSession, *, query_text: str, schema_fingerprint: str,
    ) -> dict[str, Any]:
        return await memory_get_trajectory(session, query_text, schema_fingerprint)

    async def put_trajectory(
        self,
        session: MCPSession,
        *,
        query_text: str,
        schema_fingerprint: str,
        insight: str,
        tables: list[str],
        join_path: list[str],
        dialect_notes: list[str] | None = None,
    ) -> dict[str, Any]:
        return await memory_put_trajectory(
            session,
            query_text=query_text,
            schema_fingerprint=schema_fingerprint,
            insight=insight,
            tables=tables,
            join_path=join_path,
            dialect_notes=dialect_notes,
        )


@lru_cache(maxsize=1)
def get_trajectory_store() -> "TrajectoryStore":
    """
    Resolve the configured TrajectoryStore. Unresolvable name is a startup
    failure the first time a node calls this (A8) — not deferred further.
    """
    provider: str = str(cfg.memory.provider)
    if provider not in _VALID_PROVIDERS:
        raise RuntimeError(
            f"Unknown memory.provider {provider!r}. Valid values: "
            f"{sorted(_VALID_PROVIDERS)}. Check config/statesync.toml [memory] provider."
        )
    logger.info("TrajectoryStore resolved: provider=%s", provider)
    return QdrantTrajectoryStore()
