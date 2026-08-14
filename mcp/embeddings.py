"""
mcp/embeddings.py
Query-text embedding for AgentSM trajectory memory (A6, BD2 — see
docs/phase_b_amendment.md).

Calls the mcp-only Ollama container's /api/embeddings endpoint. This is a
network call from mcp to ollama (both on db_net) — unrelated to A4.7's
ss_embed, which must never make a network call from inside postgres.

Model identifier: memory.embedding_model (config/statesync.toml, A0.2 —
no model literal in source). Every returned vector is validated against
memory.embedding_dim before use.
"""

from __future__ import annotations

import logging
import os

import httpx

from config_loader import cfg

logger = logging.getLogger(__name__)

_OLLAMA_URL: str = os.environ.get("OLLAMA_URL", "http://ollama:11434")
_EMBED_ENDPOINT: str = _OLLAMA_URL.rstrip("/") + "/api/embeddings"

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=cfg.memory.embedding_timeout_s)
    return _client


async def embed_text(text: str) -> list[float]:
    """
    Compute the embedding vector for a piece of text via Ollama.

    Raises:
        RuntimeError: if the response is malformed or the vector's dimension
                      does not match memory.embedding_dim.
    """
    client = _get_client()
    response = await client.post(
        _EMBED_ENDPOINT,
        json={"model": cfg.memory.embedding_model, "prompt": text},
    )
    response.raise_for_status()
    body = response.json()

    vector = body.get("embedding")
    if not isinstance(vector, list) or not vector:
        raise RuntimeError(
            f"Ollama embeddings response missing 'embedding' array: {body!r}"
        )

    expected_dim = cfg.memory.embedding_dim
    if len(vector) != expected_dim:
        raise RuntimeError(
            f"Embedding dimension mismatch: expected {expected_dim}, got "
            f"{len(vector)}. Check memory.embedding_model matches "
            f"memory.embedding_dim in config/statesync.toml."
        )

    return vector


async def close_embeddings_client() -> None:
    """Called at mcp shutdown."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
