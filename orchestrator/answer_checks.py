"""
orchestrator/answer_checks.py
Deterministic checks for SQL that RUNS but can still give a wrong or misleading number
(flag pipeline.answer_checks = "on"; nothing here is called with the flag off).

WHY: the SQL safety gate and the error classifier only see statements that fail. A join
that repeats one side's rows makes SUM/AVG over that side count values more than once and
returns a plausible, confidently wrong figure. No model can be trusted to notice that, so
it is found the mechanical way: count the joined rows, count the distinct keys of the
aggregated table inside the same join, and compare.

COUNT (added 2026-10-02):
  - COUNT(alias.col) without DISTINCT counts rows of alias's table, exactly like SUM, so it
    gets the same probe.
  - COUNT(*) counts JOINED rows. That is right whenever some joined table's rows are unique
    in the join (counting order items through orders is legitimate), so it is flagged only
    when EVERY joined table repeats: then it counts combinations, not rows of any table.
  - Not caught, by design: COUNT(*) that counts the wrong table's rows ("how many customers"
    answered by counting their orders). Which table the question meant is semantic; a guess
    would be a false accusation.

Everything that parses SQL is pure and never raises; the small async runners swallow and
log any failure and skip the check, because a check must never turn a working answer into
a failed one. Non-blocking caveats (truncation, all-NULL result) are pure functions of the
executed rows; they are shown to the user, they do not trigger a retry.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from config_loader import cfg
from mcp_client import MCPSession, data_run_select

logger = logging.getLogger(__name__)

try:
    import pglast
    from pglast.stream import RawStream
    from pglast.visitors import Visitor
    _PGLAST_AVAILABLE = True
except ImportError:  # pragma: no cover -- dependency is pinned in pyproject.toml
    _PGLAST_AVAILABLE = False
    Visitor = object  # type: ignore[assignment,misc]

# Marks where the verified key column goes in a probe's count query. Not valid SQL
# outside an identifier position, so it cannot collide with anything in the original.
KEY_PLACEHOLDER = "<KEY>"

_FANOUT_AGGREGATES = frozenset({"sum", "avg", "count"})
_SIMPLE_IDENT = re.compile(r"[a-z_][a-z0-9_$]*")


@dataclass(frozen=True)
class Probe:
    """One aggregated table to test for repeated rows inside the query's own join."""
    table: str          # schema-qualified when the SQL (or default_schema) says which schema
    alias: str          # the name the SQL uses for it (the table name when unaliased)
    count_sql: str      # COUNT query text with KEY_PLACEHOLDER where the key column goes
    label: str = "SUM/AVG"   # the aggregates over this table's columns, for the message
    star: bool = False       # a COUNT(*) probe: it matters only if EVERY joined table repeats

    def count_sql_for(self, key: str) -> str:
        return self.count_sql.replace(KEY_PLACEHOLDER, _quote_ident(key))


@dataclass(frozen=True)
class Finding:
    table: str
    key: str
    n: int              # joined rows
    d: int              # distinct keys of `table` among them
    message: str


def _quote_ident(name: str) -> str:
    if _SIMPLE_IDENT.fullmatch(name):
        return name
    return '"' + name.replace('"', '""') + '"'


# -- fan-out probes (pure) ---------------------------------------------------

def _leaf_tables(node: Any, default_schema: str | None, out: dict[str, str]) -> bool:
    """Collect alias -> table for every leaf of a FROM item; False if any leaf is not
    a plain table (subquery, function, ...) or an alias repeats."""
    if isinstance(node, pglast.ast.JoinExpr):
        return _leaf_tables(node.larg, default_schema, out) and _leaf_tables(node.rarg, default_schema, out)
    if not isinstance(node, pglast.ast.RangeVar):
        return False
    schema = node.schemaname or default_schema
    table = f"{schema}.{node.relname}" if schema else node.relname
    alias = node.alias.aliasname if node.alias is not None else node.relname
    if alias in out:
        return False
    out[alias] = table
    return True


def _has_join(node: Any) -> bool:
    return isinstance(node, pglast.ast.JoinExpr)


class _Refs(Visitor):
    """Every column reference inside an expression."""

    def __init__(self) -> None:
        super().__init__()
        self.refs: list[Any] = []

    def visit_ColumnRef(self, ancestors, node):  # noqa: N802 -- pglast visitor naming
        self.refs.append(node)


def _one_alias_ref(expr: Any) -> Any | None:
    """A representative column of `expr` when every column it reads is alias.column of the SAME
    alias (AVG(EXTRACT(YEAR FROM d.dob)), SUM(o.qty * o.price)); None when it reads none or
    several tables. Added after the evidence-tiers study: on its fan-out set the robustness model
    averaged EXTRACT(YEAR FROM ...) through a repeating join, which the plain-column rule missed."""
    scan = _Refs()
    scan(expr)
    aliases = {tuple(getattr(f, "sval", None) for f in ref.fields)[0] for ref in scan.refs if len(ref.fields) == 2}
    if not scan.refs or len(aliases) != 1 or any(len(ref.fields) != 2 for ref in scan.refs):
        return None
    return scan.refs[0]


class _Scan(Visitor):
    """Finds sum/avg/count calls over a column (or an expression of one table's columns),
    COUNT(*), and anything that makes the statement too complicated to reason about
    (sub-selects)."""

    def __init__(self) -> None:
        super().__init__()
        self.aggregates: list[tuple[str, Any]] = []   # (function name, ColumnRef)
        self.count_star = False
        self.complex = False

    def visit_SubLink(self, ancestors, node):  # noqa: N802 -- pglast visitor naming
        self.complex = True

    def visit_FuncCall(self, ancestors, node):  # noqa: N802
        name = getattr(node.funcname[-1], "sval", "").lower() if node.funcname else ""
        if name not in _FANOUT_AGGREGATES or node.agg_distinct or node.over is not None:
            return
        if name == "count" and node.agg_star:
            self.count_star = True
            return
        args = node.args or ()
        if len(args) == 1 and isinstance(args[0], pglast.ast.ColumnRef):
            self.aggregates.append((name, args[0]))
        elif len(args) == 1:
            ref = _one_alias_ref(args[0])
            if ref is not None:
                self.aggregates.append((name, ref))


def fanout_probe(sql: str, default_schema: str | None = None) -> list[Probe] | None:
    """Probes for a single SELECT with a JOIN and SUM/AVG/COUNT over a column of some joined
    table, or COUNT(*) (one star probe per joined table); None whenever the statement is
    outside that shape (conservative on purpose: a wrong "fan-out" verdict costs a needless
    retry, a missed one costs nothing new)."""
    if not _PGLAST_AVAILABLE or not sql or not sql.strip():
        return None
    try:
        stmts = pglast.parse_sql(sql)
        if len(stmts) != 1:
            return None
        stmt = stmts[0].stmt
        if not isinstance(stmt, pglast.ast.SelectStmt) or stmt.op != pglast.enums.SetOperation.SETOP_NONE:
            return None
        if stmt.withClause is not None or not stmt.fromClause or stmt.intoClause is not None:
            return None
        if not any(_has_join(item) for item in stmt.fromClause):
            return None
        tables: dict[str, str] = {}
        for item in stmt.fromClause:
            if not _leaf_tables(item, default_schema, tables):
                return None

        scan = _Scan()
        scan(stmt)
        if scan.complex:
            return None

        functions: dict[str, set[str]] = {}
        for name, ref in scan.aggregates:
            fields = [getattr(f, "sval", None) for f in ref.fields]
            # Only alias.column: an unqualified column in a join cannot be assigned to one
            # table without the schema, and guessing would be a false accusation.
            if len(fields) == 2 and all(fields) and fields[0] in tables:
                functions.setdefault(fields[0], set()).add(name)
        aliases = list(functions)
        if not aliases and not scan.count_star:
            return None

        from_sql = ", ".join(RawStream()(item) for item in stmt.fromClause)
        where_sql = f" WHERE {RawStream()(stmt.whereClause)}" if stmt.whereClause is not None else ""
        def count_sql(alias: str) -> str:
            # COUNT(key), not COUNT(*): rows NULL-extended by an outer join
            # are not rows of this table, so they must not look like repeats.
            return (f"SELECT COUNT({_quote_ident(alias)}.{KEY_PLACEHOLDER}) AS n, "
                    f"COUNT(DISTINCT {_quote_ident(alias)}.{KEY_PLACEHOLDER}) AS d "
                    f"FROM {from_sql}{where_sql}")

        probes = [Probe(table=tables[alias], alias=alias, count_sql=count_sql(alias),
                        label=_label(functions[alias])) for alias in aliases]
        if scan.count_star:
            probes += [Probe(table=table, alias=alias, count_sql=count_sql(alias), label="COUNT(*)", star=True)
                       for alias, table in tables.items()]
        return probes
    except Exception as exc:
        logger.warning("answer_checks: fanout_probe skipped: %s", exc)
        return None


def _label(functions: set[str]) -> str:
    """'SUM/AVG' (unchanged wording), 'COUNT', or 'SUM/AVG/COUNT' for the message."""
    parts = (["SUM/AVG"] if functions & {"sum", "avg"} else []) + (["COUNT"] if "count" in functions else [])
    return "/".join(parts)


# -- key discovery and the fan-out check (async) -------------------------------

_KEY_CACHE: dict[tuple[str, str], str | None] = {}


def clear_cache() -> None:
    _KEY_CACHE.clear()


def _first_value(result: dict[str, Any]) -> Any:
    rows = result.get("rows") or []
    if not (result.get("ok") and rows):
        return None
    return next(iter(rows[0].values()), None)


def _is_key_candidate(column: str) -> bool:
    name = column.lower()
    return name == "id" or any(name.endswith(s.lower()) for s in cfg.answer_checks.key_column_suffixes)


async def find_single_column_key(
    session: MCPSession, schema_table: str, columns: list[str], fingerprint: str = "",
) -> str | None:
    """First key-looking column of the table that is provably unique. Cached per
    (table, schema fingerprint): uniqueness only changes when the data does, and the
    scan is a full-table read we do not want to repeat for every question."""
    cache_key = (schema_table, fingerprint)
    if cache_key in _KEY_CACHE:
        return _KEY_CACHE[cache_key]
    try:
        found: str | None = None
        for column in columns:
            if not _is_key_candidate(column):
                continue
            col = _quote_ident(column)
            result = await data_run_select(
                session,
                f"SELECT COUNT(*) = COUNT(DISTINCT {col}) AS is_unique FROM {schema_table}",
                max_rows=1,
            )
            if _first_value(result) in (True, "t", "true"):
                found = column
                break
    except Exception as exc:
        logger.warning("answer_checks: key search skipped for %s: %s", schema_table, exc)
        return None
    _KEY_CACHE[cache_key] = found
    return found


async def check_fanout(
    session: MCPSession,
    sql: str,
    fingerprint: str,
    table_columns: dict[str, list[str]],
    default_schema: str | None = None,
) -> Finding | None:
    """A Finding when the joined rows outnumber the distinct keys of an aggregated table, or,
    for COUNT(*), when EVERY joined table repeats. table_columns maps a table (schema-qualified
    or bare) to its column names."""
    try:
        probes = fanout_probe(sql, default_schema) or []
        for probe in (p for p in probes if not p.star):
            measured = await _measure(session, probe, table_columns, fingerprint)
            if measured is not None and measured[1] > measured[2]:
                key, n, d = measured
                return Finding(
                    table=probe.table, key=key, n=n, d=d,
                    message=(
                        f"Joining repeats rows of {probe.table} ({n:,} joined rows for {d:,} "
                        f"distinct {key}), so {probe.label} over its columns count some values more than once."
                    ),
                )
        stars = [p for p in probes if p.star]
        if stars:
            measured_all = [(p, await _measure(session, p, table_columns, fingerprint)) for p in stars]
            # Every table needs a proven key and must repeat; one unique table means COUNT(*)
            # counts that table's rows, which may be exactly what was asked.
            if all(m is not None and m[1] > m[2] for _, m in measured_all):
                first, (key, n, d) = measured_all[0]
                details = "; ".join(f"{p.table}: {m[2]:,} distinct {m[0]}" for p, m in measured_all)
                return Finding(
                    table=first.table, key=key, n=n, d=d,
                    message=(
                        f"COUNT(*) over this join counts {n:,} combinations of rows, and every joined table "
                        f"repeats ({details}), so it is not a count of any one table's rows."
                    ),
                )
    except Exception as exc:
        logger.warning("answer_checks: check_fanout skipped: %s", exc)
    return None


async def _measure(session: MCPSession, probe: Probe, table_columns: dict[str, list[str]],
                   fingerprint: str) -> tuple[str, int, int] | None:
    """(key, joined rows, distinct keys) for one probe; None without a provable key or a result."""
    columns = table_columns.get(probe.table) or table_columns.get(probe.table.split(".")[-1]) or []
    key = await find_single_column_key(session, probe.table, columns, fingerprint)
    if key is None:
        return None
    result = await data_run_select(session, probe.count_sql_for(key), max_rows=1)
    rows = result.get("rows") or []
    if not (result.get("ok") and rows):
        return None
    return key, int(rows[0]["n"]), int(rows[0]["d"])


# -- non-blocking caveats (pure) -----------------------------------------------

def truncation_caveat(row_count: int, max_rows: int) -> str | None:
    if row_count == max_rows:
        return f"Showing the first {max_rows:,} rows; the full result is larger."
    return None


def null_aggregate_caveat(rows: list[dict]) -> str | None:
    if rows and all(v is None for row in rows for v in row.values()):
        return "No matching data: the result is empty."
    return None
