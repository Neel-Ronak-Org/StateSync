"""
orchestrator/nodes/error_exit.py
The point where the SQL retry loop gives up (graph.py).

It does no work: the failed attempts are already in state.errors. It exists so that "no more retries"
is a visible step in the graph and in the logs, and so the next step can choose between the answer
writer, the documents part of a two-source question, and route recovery.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def error_exit_node(state_dict: dict[str, Any]) -> dict[str, Any]:
    errors: list[dict] = state_dict.get("errors", [])
    logger.info("SQL retries over for query_id=%s at attempt=%d category=%s",
                state_dict.get("query_id", ""), state_dict.get("attempt", 0),
                errors[-1]["category"] if errors else "unknown")
    return {}
