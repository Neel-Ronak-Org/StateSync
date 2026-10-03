"""
orchestrator/evidence.py
The evidence tier of an answer (docs/V3_PLAN.md Phase 3c, slice 1).

WHY THIS EXISTS: a reader should see how much to trust an answer without
reading SQL. The tier is a pure function of facts the pipeline already
recorded, so it costs no model call and cannot drift from what actually ran:

  verified   an approved example's SQL ran as is
  compiled   an approved example's template filled with the question's values in code
  checked    new SQL that passed the automatic answer checks (checks on, no caveat)
  unchecked  new SQL with a caveat, or with the checks off
  None       no SQL ran (documents-only or failed answers): nothing to label

"compiled" outranks the checks tiers: its SQL is an approved query with values swapped,
not new SQL, but it ranks below "verified" because the values came from a model.
"""

from __future__ import annotations

from typing import Any


def evidence_tier(verified_example: bool, executed_sql: str | None, flags_snapshot: dict[str, Any],
                  caveats: list[str], compiled_template: bool = False) -> str | None:
    """The evidence tier for one finished answer; see the module docstring."""
    if verified_example:
        return "verified"
    if not executed_sql:
        return None
    if compiled_template:
        return "compiled"
    if flags_snapshot.get("pipeline.answer_checks") == "on" and not caveats:
        return "checked"
    return "unchecked"
