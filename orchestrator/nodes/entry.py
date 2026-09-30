"""
orchestrator/nodes/entry.py
Entry node — first node in the graph.

Responsibilities:
- Generates query_id (uuid4).
- Keeps the authenticated principal's tenant_id (set by main.py); the config
  default only when none was supplied (A0.6, from the single-tenant phase).
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
        # query_id is generated once by main.py before the graph runs, not
        # here -- A2 marks it immutable from entry onward, and main.py's
        # initial StateSyncState construction is that entry point. Passing
        # it through (not regenerating it) is what makes the immutable-field
        # assertion in state.merge() a real check rather than a guaranteed
        # first-node failure.
        # The tenant of the authenticated principal, set by main.py when the
        # state is built (tenant_id is immutable from there on). The config
        # default only stands in when no caller supplied one (A0.6's original
        # single-tenant rule); overwriting a principal's tenant broke every
        # question from an account outside the default tenant.
        "tenant_id": state_dict.get("tenant_id") or cfg.tenancy.default_tenant,
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
