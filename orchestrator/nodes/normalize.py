"""
orchestrator/nodes/normalize.py
Normalization gate node -- Phase D. Wired in graph.py between entry and
supervisor (Callout 1: the Supervisor never sees raw_query, only
normalized_query -- structural, not conventional, because this node always
runs first and always sets normalized_query before supervisor_node can run).

normalization.enabled (flags_snapshot, captured once at entry per A2) is a
D13 control path: disabled means no LanguageNormalizer call at all -- a true
control condition, not "call it and discard the result" -- matching how
memory.enabled gates memory.get_trajectory in sql_agent.py.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from language_gate import needs_language_model
from language_normalizer import get_language_normalizer

logger = logging.getLogger(__name__)


async def normalize_node(state_dict: dict[str, Any]) -> dict[str, Any]:
    raw_query: str = state_dict.get("raw_query", "")
    query_id: str = state_dict.get("query_id", "")
    flags_snapshot: dict[str, Any] = state_dict.get("flags_snapshot", {})
    normalization_enabled: bool = bool(flags_snapshot.get("normalization.enabled", True))

    start_ms = int(time.monotonic() * 1000)

    if not normalization_enabled:
        logger.info(
            "Normalize gate DISABLED (control path) for query_id=%s -- "
            "raw_query passed straight to Supervisor, no LanguageNormalizer call.",
            query_id,
        )
        return {
            "detected_language": "en",
            "normalized_query": raw_query,
            "preserved_entities": {},
            "metrics": {
                "tokens_in": 0, "tokens_out": 0, "llm_calls": 0,
                "trajectory_steps": 0, "latency_ms": 0,
            },
        }

    if (
        flags_snapshot.get("pipeline.language_gate") == "script"
        and not needs_language_model(raw_query)
    ):
        logger.info(
            "Normalize gate: query_id=%s language_gate=script found no Devanagari or "
            "Hinglish markers -- skipped the LanguageNormalizer call.",
            query_id,
        )
        return {
            "detected_language": "en",
            "normalized_query": raw_query,
            "preserved_entities": {},
            "metrics": {
                "tokens_in": 0, "tokens_out": 0, "llm_calls": 0,
                "trajectory_steps": 0, "latency_ms": 0,
            },
        }

    normalizer = get_language_normalizer()
    result = await normalizer.normalize(raw_query)
    elapsed_ms = int(time.monotonic() * 1000) - start_ms

    logger.info(
        "Normalize gate: query_id=%s detected_language=%s rewritten=%s "
        "preserved_entities=%d latency_ms=%d",
        query_id, result["detected_language"],
        result["normalized_query"] != raw_query,
        len(result["preserved_entities"]), elapsed_ms,
    )

    return {
        "detected_language": result["detected_language"],
        "normalized_query": result["normalized_query"],
        "preserved_entities": result["preserved_entities"],
        "metrics": {
            "tokens_in": result["tokens_in"],
            "tokens_out": result["tokens_out"],
            "llm_calls": 1,
            "trajectory_steps": 1,
            "latency_ms": elapsed_ms,
        },
    }
