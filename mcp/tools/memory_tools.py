"""
mcp/tools/memory_tools.py
MCP memory tools: memory.get_trajectory and memory.put_trajectory (A4.8, C1).

AgentSM trajectory store — Qdrant-backed (A6). Reads always filter on
tenant_id AND schema_fingerprint inside the Qdrant query itself (A0.6, D4),
never in application code after retrieval.

put_trajectory stores abstracted insight, never the generated SQL string and
never result rows (A4.8). The server-side regex below is the backstop; the
node-side prompt that produces `insight` is not the guarantee.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from qdrant_client import models as qmodels

from config_loader import cfg
from embeddings import embed_text
from qdrant_store import get_qdrant_client
from session import get_tenant_id

logger = logging.getLogger(__name__)

_COLLECTION = cfg.memory.collection

# A4.8: server-side rejection — insight must never be a SQL statement.
_SQL_REJECT_RE = re.compile(r"(?i)^\s*(select|with)\b")


def _tenant_filter(tenant_id: str) -> qmodels.FieldCondition:
    return qmodels.FieldCondition(
        key="tenant_id", match=qmodels.MatchValue(value=tenant_id)
    )


def _fingerprint_filter(schema_fingerprint: str) -> qmodels.FieldCondition:
    return qmodels.FieldCondition(
        key="schema_fingerprint", match=qmodels.MatchValue(value=schema_fingerprint)
    )


# ── A4.8 memory.get_trajectory ──────────────────────────────────────────────
async def get_trajectory(
    query_text: str,
    schema_fingerprint: str,
    top_k: int | None = None,
) -> dict[str, Any]:
    """
    Vector search over agentsm_trajectories, filtered on tenant_id AND
    schema_fingerprint inside the Qdrant query (A6). rejected_stale is the
    count of matches that clear the same similarity bar for this tenant but
    fall out once the fingerprint filter is applied (D4 — makes staleness
    observable rather than silent).
    """
    top_k_max = cfg.memory.top_k_max
    if top_k is not None and not (1 <= top_k <= top_k_max):
        raise ValueError(f"top_k must be between 1 and {top_k_max}, got {top_k!r}")

    tenant_id = get_tenant_id()
    effective_top_k = top_k if top_k is not None else cfg.memory.top_k
    threshold = cfg.memory.similarity_threshold

    client = get_qdrant_client()
    vector = await embed_text(query_text)

    filtered_hits = await client.search(
        collection_name=_COLLECTION,
        query_vector=vector,
        query_filter=qmodels.Filter(
            must=[_tenant_filter(tenant_id), _fingerprint_filter(schema_fingerprint)]
        ),
        limit=effective_top_k,
        score_threshold=threshold,
        with_payload=True,
    )

    tenant_only_hits = await client.search(
        collection_name=_COLLECTION,
        query_vector=vector,
        query_filter=qmodels.Filter(must=[_tenant_filter(tenant_id)]),
        limit=effective_top_k,
        score_threshold=threshold,
        with_payload=False,
    )

    filtered_ids = {hit.id for hit in filtered_hits}
    rejected_stale = sum(1 for hit in tenant_only_hits if hit.id not in filtered_ids)

    candidates = [
        {
            "trace_id": str(hit.id),
            "similarity": hit.score,
            "insight": hit.payload.get("insight", ""),
            "tables": hit.payload.get("tables", []),
            "join_path": hit.payload.get("join_path", []),
            "dialect_notes": hit.payload.get("dialect_notes", []),
        }
        for hit in filtered_hits
    ]

    # hit_count increments on use (C1 Notes) — instrumentation for a future
    # eviction policy, bumped only on the candidate actually surfaced as best.
    if filtered_hits:
        best = filtered_hits[0]
        await client.set_payload(
            collection_name=_COLLECTION,
            payload={"hit_count": int(best.payload.get("hit_count", 0)) + 1},
            points=[best.id],
        )

    logger.info(
        "memory.get_trajectory: tenant=%s hit=%s candidates=%d rejected_stale=%d",
        tenant_id, bool(candidates), len(candidates), rejected_stale,
    )

    return {
        "hit": len(candidates) > 0,
        "rejected_stale": rejected_stale,
        "candidates": candidates,
    }


# ── A4.8 memory.put_trajectory ──────────────────────────────────────────────
async def put_trajectory(
    query_text: str,
    schema_fingerprint: str,
    insight: str,
    tables: list[str],
    join_path: list[str],
    dialect_notes: list[str] | None = None,
) -> dict[str, Any]:
    """
    Write a trajectory. Stores abstracted insight only — never SQL, never
    result rows (A4.8). Rejects server-side if insight looks like a SQL
    statement; this is the backstop, not the guarantee (C1 Notes).
    """
    if len(insight) > cfg.memory.insight_max_chars:
        raise ValueError(
            f"insight exceeds memory.insight_max_chars "
            f"({cfg.memory.insight_max_chars} chars): got {len(insight)}."
        )
    if _SQL_REJECT_RE.match(insight):
        raise ValueError(
            "insight must describe navigation strategy, not SQL — rejected: "
            "starts with SELECT or WITH (A4.8 server-side backstop)."
        )
    if not tables:
        raise ValueError("tables must be a non-empty list.")
    # join_path may legitimately be empty: a single-table query has no join
    # to describe. A4.8 requires the field present, not non-empty.

    tenant_id = get_tenant_id()
    vector = await embed_text(query_text)
    trace_id = str(uuid.uuid4())

    client = get_qdrant_client()
    await client.upsert(
        collection_name=_COLLECTION,
        points=[
            qmodels.PointStruct(
                id=trace_id,
                vector=vector,
                payload={
                    "tenant_id": tenant_id,
                    "schema_fingerprint": schema_fingerprint,
                    "query_text": query_text,
                    "insight": insight,
                    "tables": tables,
                    "join_path": join_path,
                    "dialect_notes": dialect_notes or [],
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "hit_count": 0,
                },
            )
        ],
    )

    logger.info(
        "memory.put_trajectory: trace_id=%s tenant=%s tables=%s",
        trace_id, tenant_id, tables,
    )
    return {"trace_id": trace_id}
