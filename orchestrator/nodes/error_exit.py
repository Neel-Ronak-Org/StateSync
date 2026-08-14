"""
orchestrator/nodes/error_exit.py
Terminal-exit node for the C2 correction loop (graph.py).

Per the C2 spec's routing diagram, "terminal error" is a distinct arrow
from "success", both leaving the sql node's conditional edge --
distinguishing them as separate graph hops is what makes the loop's
termination reasons visible in the graph (not just in log lines), which is
the whole point of making the correction cycle a real edge instead of
logic hidden inside a node.

This node does no work of its own: sql_agent_node already put everything
synthesizer needs on state (_sql_error scratch key; the classified
category/hint inside it). It exists so "we are not retrying anymore,
whether because the category was terminal or because retry_max was
exhausted" is a real, loggable, inspectable point in the graph.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def error_exit_node(state_dict: dict[str, Any]) -> dict[str, Any]:
    query_id: str = state_dict.get("query_id", "")
    attempt: int = state_dict.get("attempt", 0)
    errors: list[dict] = state_dict.get("errors", [])
    category = errors[-1]["category"] if errors else "unknown"

    logger.info(
        "Correction loop exiting for query_id=%s at attempt=%d category=%s "
        "(no further retry).",
        query_id, attempt, category,
    )
    # No scratch-key handoff needed: synthesizer_node derives "did the last
    # attempt fail, and why" from the typed state.errors/state.attempt
    # fields directly (the same test _route_after_sql uses), not from a
    # scratch key -- a scratch key would not reliably survive the
    # conditional edge between sql_agent_node and this node anyway (see
    # synthesizer_node's docstring/comment for why).
    return {}
