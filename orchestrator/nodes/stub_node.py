"""
orchestrator/nodes/stub_node.py
Stub node for routes not implemented in Phase B (docs, hybrid).

Per B1 scope:
  "Register the routing table with three routes (sql, docs, hybrid),
   where docs and hybrid resolve to a stub node returning an explicit
   'not implemented in Phase 1' marker — a registered stub, not a missing
   route, so the seam exists per A0.5."

The seam is real (registered in the routing table); the implementation is
explicit about its incompleteness. This is what makes A0.5 satisfied:
  "a registry with a registration function that is actually called."
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_NOT_IMPLEMENTED_MESSAGE = (
    "This query requires the '{route}' path, which is not implemented in Phase 1. "
    "Please rephrase as a structured data question, or wait for Phase E1 (document retrieval) "
    "and Phase E2 (hybrid) to be completed."
)


async def stub_node(state_dict: dict[str, Any]) -> dict[str, Any]:
    """
    Stub node for docs and hybrid routes.
    Returns an explicit Phase 1 not-implemented marker as _stub_message.
    The Synthesizer reads _stub_message and passes it through without an LLM call.
    """
    route: str = state_dict.get("route", "unknown")
    query_id: str = state_dict.get("query_id", "")

    logger.info(
        "Stub node invoked for route='%s' query_id=%s (Phase 1 not implemented).",
        route, query_id,
    )

    return {
        "_stub_message": _NOT_IMPLEMENTED_MESSAGE.format(route=route),
    }
