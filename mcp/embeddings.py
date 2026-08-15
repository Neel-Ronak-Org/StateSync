"""
mcp/embeddings.py
Text embedding for AgentSM trajectory memory (A6, BD2 — see
docs/phase_b_amendment.md) and, since Part E1, for document_chunks
retrieval (A4.6/A4.7) and query-time ss_embed support.

Calls the mcp-only Ollama container's /api/embeddings endpoint. This is a
network call from mcp to ollama (both on db_net) — unrelated to A4.7's
ss_embed, which must never make a network call from inside postgres: the
embedding computed here by mcp is what gets bound into ss_embed's session
GUC before query execution, not something ss_embed itself ever fetches.

Model identifier defaults to memory.embedding_model/embedding_dim
(config/statesync.toml, A0.2 — no model literal in source); callers with
their own concern (retrieval.embedding_model/embedding_dim) pass them
explicitly rather than sharing memory's config path, even though both
happen to name the same underlying model today.
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


async def embed_text(
    text: str,
    *,
    model: str | None = None,
    dim: int | None = None,
) -> list[float]:
    """
    Compute the embedding vector for a piece of text via Ollama.

    model/dim default to memory.embedding_model/embedding_dim for existing
    (C1 trajectory) callers; E1 retrieval callers pass
    retrieval.embedding_model/embedding_dim explicitly.

    Raises:
        RuntimeError: if the response is malformed or the vector's dimension
                      does not match the expected dim.
    """
    effective_model = model if model is not None else cfg.memory.embedding_model
    expected_dim = dim if dim is not None else cfg.memory.embedding_dim

    client = _get_client()
    response = await client.post(
        _EMBED_ENDPOINT,
        json={"model": effective_model, "prompt": text},
    )
    response.raise_for_status()
    body = response.json()

    vector = body.get("embedding")
    if not isinstance(vector, list) or not vector:
        raise RuntimeError(
            f"Ollama embeddings response missing 'embedding' array: {body!r}"
        )

    if len(vector) != expected_dim:
        raise RuntimeError(
            f"Embedding dimension mismatch: expected {expected_dim}, got "
            f"{len(vector)}. Check {effective_model!r} matches the "
            f"configured embedding_dim in config/statesync.toml."
        )

    return vector


async def close_embeddings_client() -> None:
    """Called at mcp shutdown."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
