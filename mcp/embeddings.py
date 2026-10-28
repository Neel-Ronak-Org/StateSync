"""
mcp/embeddings.py
Text embeddings, from the Ollama container (mxbai-embed-large: 1,024 numbers per text).

Used twice: when a document is uploaded (every passage, in batches) and when a question is searched (the
question's text). Both must use the same model, or the cosine scores mean nothing; the model and its size
are [retrieval] embedding_model and embedding_dim in config/statesync.toml, and a vector of the wrong size
is an error, not a silent mismatch. Ollama is reachable only from the MCP (both on db_net).
"""

from __future__ import annotations

import logging
import os

import httpx

from config_loader import cfg

logger = logging.getLogger(__name__)

_OLLAMA_URL: str = os.environ.get("OLLAMA_URL", "http://ollama:11434")
_EMBED_ENDPOINT: str = _OLLAMA_URL.rstrip("/") + "/api/embeddings"

_BATCH_ENDPOINT: str = _OLLAMA_URL.rstrip("/") + "/api/embed"

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=cfg.retrieval.embedding_timeout_s)
    return _client


async def embed_text(
    text: str,
    *,
    model: str | None = None,
    dim: int | None = None,
) -> list[float]:
    """
    Compute the embedding vector for a piece of text via Ollama.

    model/dim default to [retrieval] embedding_model/embedding_dim.

    Raises:
        RuntimeError: if the response is malformed or the vector's dimension
                      does not match the expected dim.
    """
    effective_model = model if model is not None else cfg.retrieval.embedding_model
    expected_dim = dim if dim is not None else cfg.retrieval.embedding_dim

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


async def embed_texts(texts: list[str], *, model: str, dim: int) -> list[list[float]]:
    """
    Embed several texts in ONE Ollama request (/api/embed takes a list).

    The document library embeds a batch of chunks per call; one request per
    chunk would spend most of its time on round trips.

    Raises:
        RuntimeError: if the number of vectors differs from the number of
                      texts, or any vector's length is not `dim`.
    """
    response = await _get_client().post(_BATCH_ENDPOINT, json={"model": model, "input": texts})
    response.raise_for_status()
    vectors = response.json().get("embeddings")
    if not isinstance(vectors, list) or len(vectors) != len(texts):
        raise RuntimeError(
            f"Ollama returned {len(vectors) if isinstance(vectors, list) else 'no'} embeddings "
            f"for {len(texts)} texts."
        )
    for vector in vectors:
        if not isinstance(vector, list) or len(vector) != dim:
            raise RuntimeError(
                f"Embedding dimension mismatch: expected {dim}, got "
                f"{len(vector) if isinstance(vector, list) else 'none'}. "
                f"Check {model!r} matches retrieval.embedding_dim in config/statesync.toml."
            )
    return vectors


async def close_embeddings_client() -> None:
    """Called at mcp shutdown."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
