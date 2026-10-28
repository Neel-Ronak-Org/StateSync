"""
orchestrator/nodes/entry.py
The graph's first step: checks the question is not empty and records the settings in force.

main.py builds the starting state (the question, who asked, the workspace, the query id). This step adds
a snapshot of config/flags.toml, taken once, so every later step reads the same settings even if the file
changes while the question runs, and starts the metrics at zero.
"""

from __future__ import annotations

import uuid
from typing import Any

from config_loader import cfg, snapshot_flags


async def entry_node(state_dict: dict[str, Any]) -> dict[str, Any]:
    raw_query: str = state_dict.get("raw_query", "")
    if not raw_query:
        raise ValueError("Entry node received an empty raw_query.")
    return {
        "tenant_id": state_dict.get("tenant_id") or cfg.tenancy.default_tenant,
        "session_id": state_dict.get("session_id") or str(uuid.uuid4()),
        "raw_query": raw_query,
        "flags_snapshot": snapshot_flags(),
        "metrics": {"tokens_in": 0, "tokens_out": 0, "llm_calls": 0, "trajectory_steps": 0, "latency_ms": 0},
    }
