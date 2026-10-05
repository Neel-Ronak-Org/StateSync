"""
orchestrator/sql_outcomes.py
How the SQL agent's run ends when the database cannot answer (docs/specs/partd_tasks/T2_sql_outcomes_recovery.md;
routing_docs_fixes.md F-S1, F-S2, F-S3). Pure functions only, so they run without the graph or the MCP.

- [pipeline] sql_no_data = "on": the SQL writer may reply NOT_ANSWERABLE: <reason> instead of SQL. The reply
  is never sent to the database; it becomes a terminal no_data_source ErrorRecord.
- With the flag off, Layer 1's "statement reads no table" stays permission_denied, as before F-S2.
"""

from __future__ import annotations

from typing import Any

from classifiers.error_classifier import category_hint, classify_error

NOT_ANSWERABLE_MARKER = "NOT_ANSWERABLE"

# Appended after the prompt template's last rule when [pipeline] sql_no_data is "on".
NOT_ANSWERABLE_RULE = """\
8. If the tables described cannot answer the question -- the data it asks about is not in them --
   reply with exactly NOT_ANSWERABLE: <one short reason> instead of SQL. Never write a query that
   returns a constant, or reads an unrelated table, to stand in for data the database does not hold."""

# The ErrorRecord message is the reply's first characters, not all of it.
_MESSAGE_MAX_CHARS = 300  # noqa: literals - display width of a stored message, not a setting

_NO_DATA_SOURCE = "no_data_source"


def sql_no_data_on(flags_snapshot: dict[str, Any]) -> bool:
    return flags_snapshot.get("pipeline.sql_no_data", "off") == "on"


def parse_not_answerable(text: str | None) -> str | None:
    """The model's NOT_ANSWERABLE reply, or None when the reply is SQL. After whitespace and Markdown
    code fences are stripped, a reply that starts with NOT_ANSWERABLE (any case) is one; the result is its
    first 300 characters, which become the ErrorRecord's message."""
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = "\n".join(line for line in cleaned.split("\n") if not line.startswith("```")).strip()
    if not cleaned.upper().startswith(NOT_ANSWERABLE_MARKER):
        return None
    return cleaned[:_MESSAGE_MAX_CHARS]


def not_answerable_error(attempt: int, message: str) -> dict[str, Any]:
    """The ErrorRecord (as a dict, like every other one the SQL agent appends) for a NOT_ANSWERABLE reply."""
    return {
        "attempt": attempt,
        "category": _NO_DATA_SOURCE,
        "engine_code": None,
        "message": message,
        "offending_sql": None,
        "remediation_hint": category_hint(_NO_DATA_SOURCE),
    }


def classify_engine_failure(
    sqlstate: str | None, message: str, flags_snapshot: dict[str, Any],
) -> tuple[str, str]:
    """classify_error for a failed statement. With sql_no_data on, as classified. Off, the category added
    by F-S2 is not shown to the pipeline: Layer 1's "reads no table" is permission_denied with the generic
    Layer 1 hint, as before."""
    category, hint = classify_error(sqlstate=sqlstate, message=message, empty_result=False)
    if category == _NO_DATA_SOURCE and not sql_no_data_on(flags_snapshot):
        return classify_error(sqlstate=sqlstate, message=message, empty_result=False, use_overrides=False)
    return category, hint
