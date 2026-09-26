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
  sql    — answerable entirely from the relational database. The stored
           columns, so you can recognise a value that simply lives in a table:
             sellers              display_name, category, contracted_sla_hours
             orders               placed_at, order_value, channel (b2b/b2c)
             fulfillments         promised_at, delivered_at, status, attempts
             delivery_events      event_type, occurred_at, note
             disputes             category, raised_at, resolved_at, narrative
             locations            city, state, pincode, digipin, coordinates
             catalog_items        title, category, unit_price
             logistics_providers  display_name, hub location, b2b_enabled
           Any count, sum, average, ranking, or filter over those.
  docs   — answerable entirely from documents: supplier agreements, SLA
           annexures, network policies (returns, disputes, data retention,
           compliance), SOPs (exception handling, damage assessment,
           escalation, cold chain). These are commitments a document makes
           (e.g. a zone's penalty percentage, a policy's resolution window)
           and they exist ONLY in documents, never as a database column.
  hybrid — needs BOTH: a database fact (e.g. which sellers/orders meet some
           condition) combined with a document fact (e.g. what a policy or
           agreement specifies for them). Usually phrased as two connected
           questions, or one question whose answer requires cross-
           referencing database entities against document content.

DISAMBIGUATION -- the words "SLA", "category" and "penalty" occur in BOTH
worlds. Decide by WHERE THE VALUE LIVES, never by which word appears:
  - sellers.contracted_sla_hours is a DATABASE COLUMN. A question asking for
    a seller's own stored SLA number is sql, even though it says "SLA".
  - An agreement's per-zone SLA/penalty TABLE is document content. A question
    asking what a document commits to, or specifies, for a zone is docs.
  - sellers.category and catalog_items.category are DATABASE COLUMNS. Asking
    which category something is in is sql, even though it says "category".
  - A policy's rules about how a category is handled are docs.
Test to apply: could this be answered by reading one value out of a table
row? If yes -> sql. Does it require reading what a written document commits
to? If yes -> docs. Does it need both -> hybrid.

Examples:
  "How many orders did seller X place last month?" -> sql
  "What is seller X's contracted SLA in hours?" -> sql        (stored column)
  "What category is seller X in?" -> sql                      (stored column)
  "How many disputes are still unresolved?" -> sql
  "What is the penalty specified in seller X's supplier agreement for a late delivery?" -> docs
  "What SLA does supplier X's agreement commit to for the Metro NCR zone?" -> docs   (document commitment)
  "What is the ONDC network's returns policy for the electronics category?" -> docs
  "Which sellers breached their contracted SLA last quarter, and what penalty does their agreement specify?" -> hybrid

Rules:
1. Respond with ONLY a JSON object: {"route": "<sql|docs|hybrid>"}
2. No explanation, no prose, no markdown. Only the JSON object.
3. All three routes are fully implemented. Do not default to sql out of
   caution -- classify on what the question actually needs."""


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

    # Regex fallback: the route named EARLIEST in the text wins. It used to be
    # whichever came first while iterating the frozenset, and set order changes
    # with each process's hash seed, so the same unparseable reply could route
    # differently after a restart (audit 007 F-007-7; docs/V3_PLAN.md 0.6).
    # Earliest mention is order-independent, and a model that fails to emit
    # JSON still tends to lead with its answer ("hybrid: needs sql and docs").
    mentions = []
    for candidate in sorted(_VALID_ROUTES):
        match = re.search(rf'\b{candidate}\b', text, re.IGNORECASE)
        if match:
            mentions.append((match.start(), candidate))
    if mentions:
        return min(mentions)[1]

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
