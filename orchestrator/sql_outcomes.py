"""
orchestrator/sql_outcomes.py
How the SQL agent's run ends when the database cannot answer (docs/specs/partd_tasks/T2_sql_outcomes_recovery.md;
routing_docs_fixes.md F-S1, F-S2, F-S3). Pure functions only, so they run without the graph or the MCP.

- [pipeline] sql_no_data = "on": the SQL writer may reply NOT_ANSWERABLE: <reason> instead of SQL. The reply
  is never sent to the database; it becomes a terminal no_data_source ErrorRecord.
- With the flag off, Layer 1's "statement reads no table" stays permission_denied, as before F-S2.

T2b (docs/specs/partd_tasks/T2b_confirm_not_answerable.md), T2c (T2c_always_confirm.md): the first
NOT_ANSWERABLE reply of a question is the retriable not_answerable_unconfirmed, whatever the writer saw. The
next attempt is shown every table (cut to [sql] full_schema_max_chars) and the columns whose names resemble
words in the question or the reply ([sql] near_match_max lines); a second NOT_ANSWERABLE is final.
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

NEAR_MATCH_HEADER = "Columns whose names resemble words in the question or your reply:"
_WORD_RE = re.compile(r"\w{4,}")
_WORD_SUBSTRING_MIN = 5  # noqa: literals - T2c spec: a normalised word and column name both this long also match by containment


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


def confirmation_pending(errors: list[dict[str, Any]]) -> bool:
    """True when the last attempt ended in not_answerable_unconfirmed, so this attempt is the one that
    shows every table."""
    return bool(errors) and errors[-1].get("category") == _UNCONFIRMED


def not_answerable_record(attempt: int, message: str, prior_errors: list[dict[str, Any]]) -> dict[str, Any]:
    """The ErrorRecord for a NOT_ANSWERABLE reply. The first one of a question is the retriable
    not_answerable_unconfirmed, whatever the writer saw; a question that already has one gets the final
    no_data_source."""
    asked_before = any(e.get("category") == _UNCONFIRMED for e in prior_errors)
    return not_answerable_error(attempt, message, _NO_DATA_SOURCE if asked_before else _UNCONFIRMED)


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


def _normalise(name: str) -> str:
    return name.lower().replace("_", "")


def _near_words(question: str, reason: str) -> list[str]:
    """The distinct words (runs of letters, digits and underscores, 4 characters or more) of the question and
    the NOT_ANSWERABLE reply, normalised (lowercase, no underscores), in order of appearance. The reply's
    marker is not a word of the reply."""
    reason = re.sub(rf"(?i)^\s*{NOT_ANSWERABLE_MARKER}\s*:?", "", reason or "")
    words: dict[str, None] = {}
    for raw in _WORD_RE.findall(f"{question or ''} {reason}"):
        norm = _normalise(raw)
        if norm:
            words[norm] = None
    return list(words)


def near_match_columns(question: str, reason: str, descriptions: list[dict[str, Any]], max_lines: int) -> list[str]:
    """`table.column` for each column whose name resembles a word of the question or the reply. A word and a
    column name are normalised (lowercase, no underscores); they match when equal, or, when both are at
    least 5 characters, when one is inside the other (so a short column such as `id` or `name` matches only
    by equality and cannot fill the list). Every table of
    `descriptions` is searched, in the order given, then column order; each match is listed once, at most
    max_lines."""
    words = _near_words(question, reason)
    found: dict[str, None] = {}
    for desc in descriptions:
        table = str(desc.get("table"))
        for col in desc.get("columns", []):
            name = col.get("name") if isinstance(col, dict) else None
            norm = _normalise(str(name)) if name else ""
            if not norm:
                continue
            if any(w == norm or (min(len(w), len(norm)) >= _WORD_SUBSTRING_MIN and (w in norm or norm in w)) for w in words):
                found[f"{table}.{name}"] = None
    return list(found)[:max(0, int(max_lines))]


def near_match_block(question: str, reason: str, descriptions: list[dict[str, Any]], max_lines: int) -> str:
    """The confirming attempt's block of near-matching columns, one `table.column` per line under
    NEAR_MATCH_HEADER; empty when nothing matches."""
    columns = near_match_columns(question, reason, descriptions, max_lines)
    return "\n".join([NEAR_MATCH_HEADER, *columns]) if columns else ""


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
