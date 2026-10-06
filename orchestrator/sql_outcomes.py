"""
orchestrator/sql_outcomes.py
How the SQL agent's run ends when the database cannot answer (docs/specs/partd_tasks/T2_sql_outcomes_recovery.md;
routing_docs_fixes.md F-S1, F-S2, F-S3). Pure functions only, so they run without the graph or the MCP.

- [pipeline] sql_no_data = "on": the SQL writer may reply NOT_ANSWERABLE: <reason> instead of SQL. The reply
  is never sent to the database; it becomes a terminal no_data_source ErrorRecord.
- With the flag off, Layer 1's "statement reads no table" stays permission_denied, as before F-S2.

T2b (docs/specs/partd_tasks/T2b_confirm_not_answerable.md): a NOT_ANSWERABLE reply is terminal only when the
writer saw the whole schema. After a partial view it is the retriable not_answerable_unconfirmed: the next
attempt is shown every table (cut to [sql] full_schema_max_chars), and a second NOT_ANSWERABLE is final.
"""

from __future__ import annotations

import re

from typing import Any

from classifiers.error_classifier import category_hint, classify_error

NOT_ANSWERABLE_MARKER = "NOT_ANSWERABLE"

# Appended after the prompt template's last rule when [pipeline] sql_no_data is "on".
NOT_ANSWERABLE_RULE = """\
{n}. If the tables described cannot answer the question -- the data it asks about is not in them --
   reply with exactly NOT_ANSWERABLE: <one short reason> instead of SQL. Never write a query that
   returns a constant, or reads an unrelated table, to stand in for data the database does not hold."""

# The ErrorRecord message is the reply's first characters, not all of it.
_MESSAGE_MAX_CHARS = 300  # noqa: literals - display width of a stored message, not a setting

_NO_DATA_SOURCE = "no_data_source"
_UNCONFIRMED = "not_answerable_unconfirmed"
_BLOCK_SEPARATOR = "\n\n"


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


def not_answerable_error(attempt: int, message: str, category: str = _NO_DATA_SOURCE) -> dict[str, Any]:
    """The ErrorRecord (as a dict, like every other one the SQL agent appends) for a NOT_ANSWERABLE reply:
    no_data_source (terminal), or not_answerable_unconfirmed (retriable) after a partial schema view."""
    return {
        "attempt": attempt,
        "category": category,
        "engine_code": None,
        "message": message,
        "offending_sql": None,
        "remediation_hint": category_hint(category),
    }


def view_is_partial(shown_tables: int, dataset_tables: int, description_cut: bool = False) -> bool:
    """Whether the writer saw only part of the schema: fewer tables than the dataset has, or a shown
    table's description cut."""
    return shown_tables < dataset_tables or description_cut


def confirmation_pending(errors: list[dict[str, Any]]) -> bool:
    """True when the last attempt ended in not_answerable_unconfirmed, so this attempt is the one that
    shows every table."""
    return bool(errors) and errors[-1].get("category") == _UNCONFIRMED


def not_answerable_record(
    attempt: int, message: str, prior_errors: list[dict[str, Any]], shown_tables: int, dataset_tables: int,
) -> dict[str, Any]:
    """The ErrorRecord for a NOT_ANSWERABLE reply. A partial view gives the retriable
    not_answerable_unconfirmed, once per question: a question that already has one gets the final
    no_data_source, as does a reply made after the whole schema was shown."""
    asked_before = any(e.get("category") == _UNCONFIRMED for e in prior_errors)
    partial = view_is_partial(shown_tables, dataset_tables)
    return not_answerable_error(attempt, message, _UNCONFIRMED if partial and not asked_before else _NO_DATA_SOURCE)


def cut_schema_view(blocks: list[str], max_chars: int) -> tuple[str, int]:
    """The table descriptions joined with a blank line, whole tables only, at most max_chars characters
    (a first table that alone exceeds it is cut mid-text). Returns (text, tables included)."""
    included: list[str] = []
    total = 0
    for block in blocks:
        added = len(block) + (len(_BLOCK_SEPARATOR) if included else 0)
        if total + added > max_chars:
            if not included:
                return block[:max_chars], 1
            break
        included.append(block)
        total += added
    return _BLOCK_SEPARATOR.join(included), len(included)


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


def not_answerable_rule(prompt: str) -> str:
    """NOT_ANSWERABLE_RULE numbered after the prompt's last numbered rule (the writer's template ends at
    7, the corrector's at 8), so neither prompt has two rules with one number."""
    numbers = [int(m.group(1)) for m in re.finditer(r"(?m)^(\d+)\. ", prompt)]
    return NOT_ANSWERABLE_RULE.format(n=(max(numbers) + 1) if numbers else 1)
