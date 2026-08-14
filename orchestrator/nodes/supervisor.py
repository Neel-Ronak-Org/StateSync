"""
orchestrator/nodes/supervisor.py
Supervisor node — classifies the user query into a route via one LLM call.

Uses get_agent_llm("supervisor") from the provider seam — never constructs
an SDK client directly. Provider and model are resolved from flags.toml.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from config_loader import cfg
from llm.provider import get_agent_llm

logger = logging.getLogger(__name__)

_VALID_ROUTES = frozenset({"sql", "docs", "hybrid"})

_SYSTEM_PROMPT = """\
You are a query router for an ONDC analytics system. Classify the user query into exactly one route.

Routes:
  sql    — structured data questions answerable via SQL over the relational database
           (orders, fulfillments, sellers, locations, disputes, etc.)
  docs   — questions requiring unstructured document retrieval or free-text search
  hybrid — questions requiring both SQL data and document context

Rules:
1. Respond with ONLY a JSON object: {"route": "<sql|docs|hybrid>"}
2. No explanation, no prose, no markdown. Only the JSON object.
3. When in doubt between sql and hybrid, choose sql.
4. docs and hybrid are Phase 1 stubs — choose sql unless the query explicitly
   requires document content."""


def _extract_route(text: str) -> str:
    """
    Parse the LLM response to extract the route string.
    Tries JSON parse first; falls back to regex; defaults to 'sql'.
    """
    # Clean markdown fences if present
    cleaned = re.sub(r"```(?:json)?|```", "", text).strip()
    try:
        parsed = json.loads(cleaned)
        route = str(parsed.get("route", "sql")).lower()
        if route in _VALID_ROUTES:
            return route
    except (json.JSONDecodeError, AttributeError):
        pass

    # Regex fallback
    for candidate in _VALID_ROUTES:
        if re.search(rf'\b{candidate}\b', text, re.IGNORECASE):
            return candidate

    logger.warning("Supervisor could not parse route from %r — defaulting to 'sql'.", text[:80])  # noqa: literals — log truncation width
    return "sql"


async def supervisor_node(state_dict: dict[str, Any]) -> dict[str, Any]:
    """
    Supervisor node: classifies the query into a route via one LLM call.
    Returns a state update dict with 'route' and metrics contribution.

    Reads normalized_query only -- never raw_query (Phase D Callout 1: this
    is structural, not conventional). normalize_node runs unconditionally
    before this node via a direct, non-conditional edge and always sets
    normalized_query (even normalization.enabled=false sets it to raw_query
    verbatim, per D1), so it is never None by the time this node runs.
    """
    query_id: str = state_dict.get("query_id", "")
    normalized_query = state_dict.get("normalized_query")
    assert normalized_query is not None, (
        "Supervisor reached with normalized_query unset -- normalize_node "
        "must run before supervisor_node on every path (D1 Callout 1)."
    )
    raw_query: str = normalized_query

    logger.info("Supervisor routing query_id=%s", query_id)

    llm = get_agent_llm("supervisor")
    start_ms = int(time.monotonic() * 1000)

    try:
        result = await llm.complete(
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": raw_query}],
            max_tokens=cfg.sql.max_tokens_supervisor,
        )
        elapsed_ms = int(time.monotonic() * 1000) - start_ms

        route = _extract_route(result.text)
        logger.info(
            "Supervisor: query_id=%s route=%s tokens_in=%d tokens_out=%d latency_ms=%d",
            query_id, route, result.tokens_in, result.tokens_out, elapsed_ms,
        )

        return {
            "route": route,
            "metrics": {
                "tokens_in":        result.tokens_in,
                "tokens_out":       result.tokens_out,
                "llm_calls":        1,
                "trajectory_steps": 1,
                "latency_ms":       elapsed_ms,
            },
        }

    except Exception as exc:
        elapsed_ms = int(time.monotonic() * 1000) - start_ms
        logger.error(
            "Supervisor failed for query_id=%s: %s — defaulting to 'sql'.",
            query_id, exc,
        )
        return {
            "route": "sql",
            "metrics": {
                "tokens_in":        0,
                "tokens_out":       0,
                "llm_calls":        1,
                "trajectory_steps": 1,
                "latency_ms":       elapsed_ms,
            },
        }
