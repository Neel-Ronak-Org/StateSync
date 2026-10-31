"""
orchestrator/card_coverage.py
What the rows of a database cover, as one line of the router's database card (front_door.py).

WHY THIS EXISTS: the card lists tables and columns, so a router cannot tell a question about an entity the
database holds from one about an entity it does not; both look like database questions (in Paper B's
routing test, every model-based router sent all ten questions about absent companies to SQL). The coverage line lists, for
each table in the card, every value of each text column with few distinct values (company names, tickers,
metric names, units) and the range of each date, timestamp or year column. It is built with read-only
queries through data.run_select (the safety gate, a read-only transaction, the statement time limit), with no
model call, and cached with the card. A column whose query fails or has too many values is left out.
"""

from __future__ import annotations

import logging
from typing import Any

from mcp_client import MCPSession, data_run_select

logger = logging.getLogger(__name__)

_TEXT_TYPES = frozenset({"text", "character varying", "varchar", "character", "char", "citext"})
_DATE_TYPES = frozenset({"date", "timestamp without time zone", "timestamp with time zone", "timestamp", "timestamptz"})
_INT_TYPES = frozenset({"integer", "bigint", "smallint", "int", "int4", "int8", "int2"})
HEADER = "Coverage (every value the rows hold):"


def _base(type_name: str) -> str:
    return type_name.split("(")[0].strip().lower()


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def column_kind(name: str, type_name: str) -> str | None:
    """"values" for a text column, "range" for a date, timestamp or year-like integer column, else None."""
    base = _base(type_name)
    if base in _TEXT_TYPES:
        return "values"
    if base in _DATE_TYPES or (base in _INT_TYPES and "year" in name.lower()):
        return "range"
    return None


def values_sql(schema: str, table: str, column: str, max_values: int) -> str:
    """Distinct values, one more than the limit so a column with too many can be told apart."""
    return (f"SELECT {_q(column)}::text AS v FROM {_q(schema)}.{_q(table)} WHERE {_q(column)} IS NOT NULL "
            f"GROUP BY 1 ORDER BY 1 LIMIT {int(max_values) + 1}")


def range_sql(schema: str, table: str, column: str) -> str:
    return f"SELECT min({_q(column)})::text AS lo, max({_q(column)})::text AS hi FROM {_q(schema)}.{_q(table)}"


def order_entries(entries: list[tuple[str, list[str] | str]]) -> list[tuple[str, str]]:
    """Shortest item first (rendered length), so the most items, and the short telling ones (ranges, units,
    entity names) survive the cap ahead of long code lists; an item whose text repeats one already kept (the
    same column in another table or view) is dropped."""
    rendered = [(k, v if isinstance(v, str) else ", ".join(v)) for k, v in entries]
    seen: set[str] = set()
    kept: list[tuple[str, str]] = []
    for k, text in sorted(rendered, key=lambda kv: (len(kv[1]), kv[0])):
        if text in seen:
            continue
        seen.add(text)
        kept.append((k, text))
    return kept


def format_coverage(entries: list[tuple[str, str]], max_chars: int) -> str:
    """One block: the header, then "table.column: ..." items in order, each kept only if it still fits within
    max_chars (an item never cut in the middle); "" when there is nothing to say."""
    if not entries:
        return ""
    out = HEADER
    for key, text in entries:
        item = f" {key}: {text};"
        if len(out) + len(item) > max_chars:
            continue                        # too long for what is left; a shorter item after it may still fit
        out += item
    return "" if out == HEADER else out


async def build_coverage(session: MCPSession, schema: str, tables: list[tuple[str, list[dict[str, Any]]]],
                         max_values: int, max_chars: int) -> str:
    """The coverage line for the card's tables ([(table, [{"name", "type"}, ...]), ...])."""
    entries: list[tuple[str, list[str] | str]] = []
    for table, columns in tables:
        for col in columns:
            name, kind = str(col.get("name")), column_kind(str(col.get("name")), str(col.get("type", "")))
            if kind is None:
                continue
            sql = values_sql(schema, table, name, max_values) if kind == "values" else range_sql(schema, table, name)
            try:
                result = await data_run_select(session, sql, max_rows=max_values + 1)
            except Exception as exc:  # noqa: BLE001 -- best-effort: a missing item only loses evidence
                logger.warning("card coverage: %s.%s skipped: %s", table, name, exc)
                continue
            rows = result.get("rows") or [] if result.get("ok") else []
            if kind == "values":
                vals = [str(r.get("v")) for r in rows if r.get("v") is not None]
                if vals and len(vals) <= max_values:
                    entries.append((f"{table}.{name}", vals))
            elif rows and rows[0].get("lo") is not None:
                entries.append((f"{table}.{name}", f"{rows[0]['lo']} to {rows[0]['hi']}"))
    return format_coverage(order_entries(entries), max_chars)
