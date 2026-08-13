"""
orchestrator/nodes/entry.py
Entry node — first node in the graph.

Responsibilities:
- Generates query_id (uuid4).
- Sets tenant_id from config (A0.6; always config default, never from input).
- Generates session_id.
- Captures flags_snapshot once (A2: flags_snapshot is captured at query entry
  and never re-read mid-query).
- Returns the initial state fields.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from config_loader import cfg, snapshot_flags


async def entry_node(state_dict: dict[str, Any]) -> dict[str, Any]:
    """
    Entry node. Receives the raw query from the API layer and produces
    the initial state update.

    Input keys expected in state_dict:
        raw_query (str): The user's query.
        session_id (str, optional): If provided by the caller, used; otherwise generated.

    Returns a state update dict with identity fields and flags_snapshot.
    """
    raw_query: str = state_dict.get("raw_query", "")
    if not raw_query:
        raise ValueError("Entry node received an empty raw_query.")

    session_id: str = state_dict.get("session_id") or str(uuid.uuid4())

    return {
        "query_id": str(uuid.uuid4()),
        "tenant_id": cfg.tenancy.default_tenant,    # A0.6: always config default
        "session_id": session_id,
        "raw_query": raw_query,
        "flags_snapshot": snapshot_flags(),          # A2: captured once, never re-read
        "metrics": {                                  # start the latency clock
            "tokens_in": 0,
            "tokens_out": 0,
            "llm_calls": 0,
            "trajectory_steps": 0,
            "latency_ms": 0,
        },
    }
