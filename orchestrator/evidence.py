"""
orchestrator/evidence.py
The evidence label shown on an answer: how much to trust it without reading the SQL.

  verified    the SQL is an approved example the owner gave a thumbs-up to, run as is
  unchecked   new SQL the model wrote for this question (the web app shows "New query")
  None        no SQL ran (a documents-only or failed answer): nothing to label

A pure function of what the pipeline recorded: no model call, so it cannot disagree with what ran.
A new query that needed a correction also carries a caution in its caveats (nodes/sql_agent.py).
"""

from __future__ import annotations


def evidence_tier(verified_example: bool, executed_sql: str | None) -> str | None:
    if verified_example:
        return "verified"
    return "unchecked" if executed_sql else None
