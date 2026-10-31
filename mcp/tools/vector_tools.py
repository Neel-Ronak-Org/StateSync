"""
mcp/tools/vector_tools.py
vector.semantic_search: the passages of a workspace's documents closest in meaning to a text.

How it works:
  1. The text is embedded with mxbai-embed-large (embeddings.py, through Ollama): 1,024 numbers.
  2. Postgres (pgvector) ranks the passages of the workspace's READY documents by cosine similarity
     to it: score = 1 - (embedding <=> query), so 1.0 is identical and 0 unrelated.
  3. The best top_k come back, each with its document title, page and section, for citations.

The search is an exact scan: retrieval.exact_vector_search switches index scans off for the transaction,
so every passage is compared. At the demo's 44,922 passages that takes about half a second; an approximate
index (HNSW) was measured on an earlier document set to miss the best passage.

Which passages are searchable is decided by the session, never by the caller's arguments: the workspace
comes from the X-Workspace-ID header (the orchestrator checked it is the caller's own), and row-level
security plus the tenant predicate keep other tenants' documents out of reach. Without a workspace there
is nothing to search. `document_ids` narrows the search to some of the workspace's documents (round 2 of
the two-round search in orchestrator/nodes/documents.py).

The embedding and the SQL are internal code, not model-written SQL, so they do not go through the
data.run_select gate; the query vector is always a bound parameter.
"""

from __future__ import annotations

import logging
from typing import Any

from config_loader import cfg
from db import get_connection
from embeddings import embed_text
from session import get_tenant_id, get_workspace_id

logger = logging.getLogger(__name__)


def _format_vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


async def _use_exact_search(conn: Any) -> None:
    """Rank every passage by true cosine distance in this transaction (SET LOCAL ends with it, and
    get_connection() always rolls back, so it never reaches the next use of the pooled connection)."""
    if cfg.retrieval.exact_vector_search:
        await conn.execute("SET LOCAL enable_indexscan = off")


async def semantic_search(query_text: str, top_k: int | None = None,
                          document_ids: list[str] | None = None) -> dict[str, Any]:
    top_k_max = int(cfg.retrieval.semantic_top_k_max)
    if top_k is not None and not (1 <= top_k <= top_k_max):
        raise ValueError(f"top_k must be between 1 and {top_k_max}, got {top_k!r}")
    effective_top_k = top_k if top_k is not None else int(cfg.retrieval.semantic_top_k)
    workspace_id = get_workspace_id()
    if not workspace_id:
        return {"matches": []}
    tenant_id = get_tenant_id()
    vector = await embed_text(query_text, model=cfg.retrieval.embedding_model, dim=cfg.retrieval.embedding_dim)
    params: dict[str, Any] = {"tenant_id": tenant_id, "workspace_id": workspace_id,
                              "qvec": _format_vector_literal(vector), "top_k": effective_top_k}
    where = ["wf.workspace_id = %(workspace_id)s", "c.tenant_id = %(tenant_id)s", "d.status = 'ready'"]
    if document_ids:
        where.append("c.document_id = ANY(%(document_ids)s)")
        params["document_ids"] = document_ids
    async with get_connection() as conn:
        await _use_exact_search(conn)
        cur = await conn.execute(f"""
            SELECT c.chunk_id, c.document_id, c.content_md, c.page,
                   1 - (c.embedding <=> %(qvec)s::vector) AS score, d.title, c.section, c.kind
            FROM workspace_docs.chunks c
            JOIN workspace_docs.documents d ON d.document_id = c.document_id
            JOIN workspace_docs.workspace_folders wf ON wf.folder_id = d.folder_id
            WHERE {' AND '.join(where)}
            ORDER BY c.embedding <=> %(qvec)s::vector
            LIMIT %(top_k)s
        """, params)
        rows = await cur.fetchall()
        names = [d.name for d in cur.description] if cur.description else []
    matches = [dict(zip(names, row)) for row in rows]
    logger.info("vector.semantic_search: tenant=%s workspace=%s top_k=%d documents=%s matches=%d",
                tenant_id, workspace_id, effective_top_k, len(document_ids) if document_ids else "all", len(matches))
    return {"matches": matches}
