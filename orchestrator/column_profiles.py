"""
orchestrator/column_profiles.py
Cached example values per text column, for the one-call SQL step.

WHY THIS EXISTS: the shipped SQL step (pipeline.sql_draft_mode = "llm") makes a
throwaway model call whose only job is to name the tables worth sampling, then
samples them live. The "profiles" variant replaces both with example values
sampled once per schema fingerprint and reused by every later question. The
model still sees the exact stored spellings (the point of interrogation) but
attempt 0 costs a single generation call.

Profiles are schema-wide rather than question-targeted, so they are bounded
hard: a few values per column, each truncated, the whole block cut to
pipeline.profile_max_chars. Identifier-like columns (ids, hashes) are skipped
because their values are noise the model would only copy.

The cache key is (schema, fingerprint): the fingerprint moves whenever the
schema does (A5), so a stale profile can never outlive the structure it was
sampled from. Data drift within an unchanged schema is accepted for this
experiment; a per-key lock makes concurrent first questions sample once.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from config_loader import cfg
from mcp_client import data_sample_rows, schema_describe_table, MCPSession

logger = logging.getLogger(__name__)

# describe_table reports pg_catalog.format_type(), e.g. "character varying(50)";
# the length suffix is stripped before matching.
_TEXT_TYPES = frozenset({"text", "character varying", "varchar", "character", "char", "citext"})

# Identifier heuristic: a column whose sampled values are all distinct AND each
# either long or hex/uuid-shaped is an id/hash column, not a vocabulary.
_IDENTIFIER_MIN_CHARS = 20  # noqa: literals - length at which a distinct value reads as an opaque id
_HEX_OR_UUID = re.compile(r"^[0-9a-fA-F]{8,}(-[0-9a-fA-F]{4,})*$")  # noqa: literals - hex/uuid shape

_HEADER = "Example values (from a sample of rows; use these exact spellings):"

_cache: dict[tuple[str, str], dict[str, list[str]]] = {}
_locks: dict[tuple[str, str], asyncio.Lock] = {}


def clear_cache() -> None:
    """Drop every cached profile (tests, and any operator-driven refresh)."""
    _cache.clear()
    _locks.clear()


def _is_text_type(type_name: str) -> bool:
    return type_name.split("(")[0].strip().lower() in _TEXT_TYPES


def _text_columns(columns: list[dict]) -> list[str]:
    return [c["name"] for c in columns if _is_text_type(str(c.get("type", "")))]


def _is_identifier_like(values: list[str]) -> bool:
    if not values or len(set(values)) != len(values):
        return False
    return all(len(v) >= _IDENTIFIER_MIN_CHARS or _HEX_OR_UUID.match(v) for v in values)


def build_profiles(
    table_columns: dict[str, list[dict]],
    samples: dict[str, list[dict]],
) -> dict[str, list[str]]:
    """
    Map "table.column" -> up to profile_values_per_column distinct non-empty
    string values (first-seen order, each cut to profile_value_max_chars) for
    text-like columns only. Identifier-like columns and columns with no usable
    value are omitted.
    """
    per_column = int(cfg.pipeline.profile_values_per_column)
    max_chars = int(cfg.pipeline.profile_value_max_chars)
    profiles: dict[str, list[str]] = {}
    for table, columns in table_columns.items():
        rows = samples.get(table, [])
        for col in _text_columns(columns):
            seen = [
                row[col] for row in rows
                if isinstance(row.get(col), str) and row[col].strip()
            ]
            if not seen or _is_identifier_like(seen):
                continue
            distinct = list(dict.fromkeys(seen))[:per_column]
            profiles[f"{table}.{col}"] = [v[:max_chars] for v in distinct]
    return profiles


def format_profiles(profiles: dict[str, list[str]], max_chars: int) -> str:
    """Render the prompt block, cut at a line boundary to max_chars; "" if empty."""
    if not profiles:
        return ""
    lines = [_HEADER]
    total = len(_HEADER)
    for key, values in profiles.items():
        line = f"  {key}: " + ", ".join(f"'{v}'" for v in values)
        if total + 1 + len(line) > max_chars:
            break
        lines.append(line)
        total += 1 + len(line)
    if len(lines) == 1:
        return ""
    return "\n".join(lines)


async def get_profiles(
    session: MCPSession,
    schema: str,
    fingerprint: str,
    table_names: list[str],
    table_descriptions: list[dict] | None = None,
) -> dict[str, list[str]]:
    """
    Profiles for every table, sampled once per (schema, fingerprint). Pass the
    schema retriever's `table_descriptions` to avoid re-describing tables;
    otherwise each table is described here.
    """
    key = (schema, fingerprint)
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        if key in _cache:
            return _cache[key]

        described = {d["table"]: d for d in (table_descriptions or [])}
        table_columns: dict[str, list[dict]] = {}
        samples: dict[str, list[dict]] = {}
        for table in table_names:
            desc: Any = described.get(table) or await schema_describe_table(session, table, schema)
            columns = desc.get("columns", [])
            text_cols = _text_columns(columns)
            if not text_cols:
                continue
            table_columns[table] = columns
            result = await data_sample_rows(
                session, table, columns=text_cols,
                limit=int(cfg.pipeline.profile_sample_rows), schema=schema,
            )
            samples[table] = result.get("rows", [])

        profiles = build_profiles(table_columns, samples)
        _cache[key] = profiles
        logger.info(
            "Column profiles built for schema=%s fingerprint=%.12s: %d columns over %d tables",
            schema, fingerprint, len(profiles), len(table_columns),
        )
        return profiles
