"""
orchestrator/templates.py
Answer a question from a template of an approved query (docs/V3_PLAN.md Phase 3c,
slice 3): the model only PICKS the template and reads the values out of the
question; the SQL is assembled here, in code.

WHY THIS EXISTS: when a question differs from an approved one only in its values,
the approved SQL with new values is the answer, and no SQL-model call is needed.
The risk is that the values come from a model, so nothing the model returns is
trusted:

  - every value is validated by its slot's type (an integer is an int, a date
    parses, a text value is bounded and has no NUL byte) and rendered by us, never
    pasted; a text value becomes a standard SQL string literal with quotes doubled;
  - the filled statement must parse as exactly ONE SELECT, or the template is dropped;
  - it then goes through the MCP data.run_select gate like any other SQL. That gate
    is authoritative; the checks here are defence in depth.

Any failure returns None and the caller falls back to the normal pipeline.
Pure functions: unit-tested in tests/test_templates.py.
"""

from __future__ import annotations

import json
import math
import re
from datetime import date, datetime
from typing import Any

from config_loader import cfg

try:
    import pglast
    from pglast import ast
except ImportError:  # pragma: no cover
    pglast = None

_PLACEHOLDER = re.compile(r"\$(\d+)")

MATCHER_PROMPT = """You match a user's question to a saved query template.

You get the user's question and up to a few candidate templates. Each candidate has the
question it was saved from and its slots (the values that can change: name, type, column
and the example value used in the saved question).

Choose a template ONLY if the user's question asks exactly the same thing as that
template's question, with different values for the slots. If the question asks for
something different (another measure, another grouping, another table, an extra
condition), answer null. Never invent columns or slots, and never change what is asked.

Reply with JSON only, no prose:
{"template_id": "<id of the chosen template>" or null, "values": {"<slot name>": <value>, ...}}

Give a value for EVERY slot of the chosen template, in the slot's type: integer and number
slots as JSON numbers, date slots as "YYYY-MM-DD", timestamp slots as "YYYY-MM-DD HH:MM:SS",
text slots as strings spelled as the example value is spelled. If the question does not
change a slot's value, repeat that slot's example value."""


def format_candidates(question: str, candidates: list[dict[str, Any]]) -> str:
    """The matcher's user message: the question and each candidate's question and slots."""
    blocks = []
    for cand in candidates:
        slots = "\n".join(
            f"  - {s.get('name')} ({s.get('type')}, column: {s.get('column') or 'LIMIT'}, "
            f"example: {s.get('example')!r})"
            for s in cand.get("slots", []))
        blocks.append(f"Template {cand.get('template_id')}\nSaved question: {cand.get('question')}\nSlots:\n{slots}")
    return f"Question: {question}\n\nCandidates:\n\n" + "\n\n".join(blocks)


def parse_match(reply: str, candidates: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """(candidate, values) from the matcher's JSON reply, or None (no match or malformed)."""
    if not isinstance(reply, str):
        return None
    text = reply.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    template_id = data.get("template_id")
    values = data.get("values")
    if not isinstance(template_id, str) or not isinstance(values, dict):
        return None
    chosen = next((c for c in candidates if c.get("template_id") == template_id), None)
    return (chosen, values) if chosen is not None else None


def _render(slot: dict[str, Any], value: Any) -> str | None:
    """One value as an SQL literal, by the slot's type, or None if it is not acceptable."""
    kind = slot.get("type")
    if kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        if slot.get("column") is None and not 1 <= value <= int(cfg.mcp.max_rows_default):
            return None  # the only column-less slot is the LIMIT count
        return str(value)
    if kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return None
        return repr(value)
    if kind in ("date", "timestamp"):
        if not isinstance(value, str):
            return None
        try:
            if kind == "date":
                return f"'{date.fromisoformat(value).isoformat()}'"
            return f"'{datetime.fromisoformat(value).isoformat(sep=' ')}'"
        except ValueError:
            return None
    if kind == "text":
        if not isinstance(value, str) or len(value) > int(cfg.templates.text_slot_max_chars) or "\x00" in value:
            return None
        return "'" + value.replace("'", "''") + "'"
    return None


def fill_template(template_sql: str, slots: list[dict[str, Any]], values: dict[str, Any]) -> str | None:
    """The template with its slots filled from `values` (keyed by slot name), or None.

    Substitution is ONE pass over the template, so a value that itself contains "$1"
    is never re-substituted, and "$10" is never read as "$1" followed by "0".
    """
    if pglast is None or not isinstance(template_sql, str) or not isinstance(slots, list) or not isinstance(values, dict):
        return None
    rendered: dict[int, str] = {}
    for index, slot in enumerate(slots, start=1):
        if not isinstance(slot, dict) or slot.get("name") not in values:
            return None
        literal = _render(slot, values[slot["name"]])
        if literal is None:
            return None
        rendered[index] = literal
    used = {int(n) for n in _PLACEHOLDER.findall(template_sql)}
    if used != set(rendered):
        return None
    sql = _PLACEHOLDER.sub(lambda m: rendered[int(m.group(1))], template_sql)
    try:
        stmts = pglast.parse_sql(sql)
    except Exception:  # noqa: BLE001 -- any parse failure means "no template"
        return None
    if len(stmts) != 1 or not isinstance(stmts[0].stmt, ast.SelectStmt):
        return None
    return sql
