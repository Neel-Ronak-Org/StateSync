"""
mcp/qdrant_store.py
Qdrant client and agentsm_trajectories collection lifecycle (A6, C1).

mcp is the sole Qdrant credential holder (A1) — orchestrator never connects
to Qdrant directly. The collection is created idempotently at mcp startup
if it does not already exist: cosine distance, memory.embedding_dim dims.
"""

from __future__ import annotations

import logging
import os

from qdrant_client import AsyncQdrantClient
from qdrant_client import models as qmodels

from config_loader import cfg

logger = logging.getLogger(__name__)

_QDRANT_URL: str = os.environ.get("QDRANT_URL", "http://qdrant:6333")
_client: AsyncQdrantClient | None = None


def get_qdrant_client() -> AsyncQdrantClient:
    if _client is None:
        raise RuntimeError("Qdrant client not initialized. Call init_qdrant() first.")
    return _client


async def init_qdrant() -> None:
    """
    Initialize the Qdrant client and ensure the agentsm_trajectories
    collection exists with the A6 schema. Idempotent — safe on every
    mcp startup (does not recreate or wipe an existing collection).
    """
    global _client
    _client = AsyncQdrantClient(url=_QDRANT_URL)

    collection = cfg.memory.collection
    existing = await _client.get_collections()
    existing_names = {c.name for c in existing.collections}

    if collection not in existing_names:
        await _client.create_collection(
            collection_name=collection,
            vectors_config=qmodels.VectorParams(
                size=cfg.memory.embedding_dim,
                distance=qmodels.Distance.COSINE,
            ),
        )
        logger.info(
            "Qdrant collection '%s' created (cosine, dim=%d).",
            collection, cfg.memory.embedding_dim,
        )
    else:
        logger.info("Qdrant collection '%s' already exists.", collection)


async def close_qdrant() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None
