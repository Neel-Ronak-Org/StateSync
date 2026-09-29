"""
orchestrator/sql_explain.py
"How I got this": a plain-English sentence or two describing the SQL behind an answer.

WHY: a business owner cannot check an answer by reading SQL, and asking a model to
"explain the query" would add a call, a cost and a chance of describing something the
query does not do. The explanation is instead read straight off the parse tree of the
statement that actually ran, so it is deterministic and can only say what the SQL says.

Only a single plain SELECT is described in detail. Anything harder (CTEs, set operations,
sub-selects, window functions) falls back to naming the tables, because a confident but
partial description of a complicated query would be worse than a vague true one.
explain_sql never raises: any parse problem returns None and the UI shows nothing.
"""

from __future__ import annotations

import logging
from typing import Any

from config_loader import cfg

logger = logging.getLogger(__name__)

try:
    import pglast
    from pglast import ast
    from pglast.enums import A_Expr_Kind, SetOperation, SortByDir
    from pglast.visitors import Visitor
    _PGLAST_AVAILABLE = True
except ImportError:  # pragma: no cover -- dependency is pinned in pyproject.toml
    _PGLAST_AVAILABLE = False
    Visitor = object  # type: ignore[assignment,misc]

MAX_CHARS = 300          # noqa: literals - display length of one explanation, not a tunable
MAX_LISTED = 3           # noqa: literals - columns/tables named before "and N more"
MAX_IN_VALUES = 5        # noqa: literals - IN-list values named before "and N more"

_CALCULATED = "a calculated value"
_OTHER = "other conditions"
_COMPARE = {
    "=": "is", "<>": "is not", "!=": "is not",
    ">": "is above", ">=": "is at least", "<": "is below", "<=": "is at most",
}
_AGG_PHRASE = {"sum": "added up", "avg": "averaged"}


class _Unsupported(Exception):
    """The statement is outside the shape that is described in detail."""


class _Scan(Visitor):
    """Finds what makes a statement too complicated to describe, and every table it
    reads (CTE names excluded, since they are not tables)."""

    def __init__(self) -> None:
        super().__init__()
        self.complex = False
        self.tables: list[str] = []
        self._ctes: set[str] = set()

    def visit_SubLink(self, ancestors, node):  # noqa: N802 -- pglast visitor naming
        self.complex = True

    def visit_RangeSubselect(self, ancestors, node):  # noqa: N802
        self.complex = True

    def visit_CommonTableExpr(self, ancestors, node):  # noqa: N802
        self.complex = True
        self._ctes.add(node.ctename)

    def visit_WindowDef(self, ancestors, node):  # noqa: N802
        self.complex = True

    def visit_FuncCall(self, ancestors, node):  # noqa: N802
        if node.over is not None:
            self.complex = True

    def visit_RangeVar(self, ancestors, node):  # noqa: N802
        if node.relname not in self.tables:
            self.tables.append(node.relname)

    def real_tables(self) -> list[str]:
        return [t for t in self.tables if t not in self._ctes]


def _humanise(name: str) -> str:
    return name.replace("_", " ")


def _join_list(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _capped(items: list[str]) -> str:
    """'a, b and c', or 'a, b, c and N more' beyond the cap."""
    if len(items) <= MAX_LISTED:
        return _join_list(items)
    return ", ".join(items[:MAX_LISTED]) + f" and {len(items) - MAX_LISTED} more"


def _column(node: Any) -> str | None:
    """Humanised column name for a plain column reference, else None."""
    if not isinstance(node, ast.ColumnRef) or not node.fields:
        return None
    last = node.fields[-1]
    if isinstance(last, ast.String):
        return _humanise(last.sval)
    return None


def _expr(node: Any) -> str:
    return _column(node) or _CALCULATED


def _const(node: Any) -> str | None:
    """Literal text (strings quoted) for a constant, seeing through a cast."""
    if isinstance(node, ast.TypeCast):
        node = node.arg
    if not isinstance(node, ast.A_Const) or node.isnull:
        return None
    val = node.val
    if isinstance(val, ast.String):
        return f"'{val.sval}'"
    if isinstance(val, ast.Integer):
        return str(val.ival)
    if isinstance(val, ast.Float):
        return str(val.fval)
    return None


def _func_name(node: ast.FuncCall) -> str:
    return getattr(node.funcname[-1], "sval", "").lower() if node.funcname else ""


def _aggregate_phrase(call: ast.FuncCall) -> str | None:
    name = _func_name(call)
    if call.agg_star:
        return "counted rows" if name == "count" else None
    args = call.args or ()
    if len(args) != 1:
        return None
    target = _expr(args[0])
    if name == "count":
        return f"counted distinct {target}" if call.agg_distinct else f"counted {target}"
    if name in _AGG_PHRASE:
        return f"{_AGG_PHRASE[name]} {target}"
    if name == "min":
        return f"found the lowest {target}"
    if name == "max":
        return f"found the highest {target}"
    return None


def _select_phrase(targets: tuple) -> str:
    aggs = []
    for t in targets:
        if isinstance(t.val, ast.FuncCall):
            phrase = _aggregate_phrase(t.val)
            if phrase:
                aggs.append(phrase)
    if aggs:
        return _join_list(aggs)
    cols = []
    for t in targets:
        if isinstance(t.val, ast.ColumnRef) and t.val.fields and isinstance(t.val.fields[-1], ast.A_Star):
            return "listed all columns"
        cols.append(_expr(t.val))
    return "listed " + _capped(cols)


def _from_items(items: tuple) -> tuple[list[str], list[str]]:
    """Table names in FROM order, and 'X matching Y' text for each simple ON equality."""
    tables: list[str] = []
    joins: list[str] = []

    def walk(node: Any) -> str | None:
        if isinstance(node, ast.RangeVar):
            tables.append(node.relname)
            return node.alias.aliasname if node.alias is not None else node.relname
        if isinstance(node, ast.JoinExpr):
            walk(node.larg)
            right_alias = walk(node.rarg)
            q = node.quals
            if (isinstance(q, ast.A_Expr) and q.kind == A_Expr_Kind.AEXPR_OP
                    and q.name[-1].sval == "="
                    and _column(q.lexpr) and _column(q.rexpr)):
                left, right = _column(q.lexpr), _column(q.rexpr)
                lq = q.lexpr.fields[-2].sval if len(q.lexpr.fields) > 1 else None
                # "<t2 col> matching <t1 col>": t2 is the table being joined in (right side).
                if lq is not None and lq == right_alias:
                    joins.append(f"{left} matching {right}")
                else:
                    joins.append(f"{right} matching {left}")
            return None
        raise _Unsupported

    for item in items:
        walk(item)
    return tables, joins


def _source_phrase(tables: list[str], joins: list[str]) -> str:
    if len(tables) == 1:
        return f"in {tables[0]}"
    shown = tables[1:MAX_LISTED]
    text = f"in {tables[0]} joined with {_join_list(shown)}"
    if len(tables) > MAX_LISTED:
        text += f" and {len(tables) - MAX_LISTED} more"
    # Join keys ("order id matching order id") are left out: they read as noise
    # to the business reader this is for, and the SQL is in Details.
    del joins
    return text


def _conditions(where: Any) -> tuple[list[str], bool]:
    """Phrases for the top-level AND-ed conditions, and whether any was too complex."""
    if where is None:
        return [], False
    parts = list(where.args) if isinstance(where, ast.BoolExpr) and where.boolop.name == "AND_EXPR" else [where]
    phrases: list[str] = []
    other = False
    for p in parts:
        phrase = _condition(p)
        if phrase is None:
            other = True
        else:
            phrases.append(phrase)
    return phrases, other


def _condition(p: Any) -> str | None:
    if isinstance(p, ast.NullTest):
        col = _column(p.arg)
        if col is None:
            return None
        return f"has no {col}" if p.nulltesttype.name == "IS_NULL" else f"has a {col}"
    if not isinstance(p, ast.A_Expr):
        return None
    col = _column(p.lexpr)
    if col is None:
        return None
    op = p.name[-1].sval if p.name else ""
    if p.kind == A_Expr_Kind.AEXPR_OP:
        value = _const(p.rexpr)
        if value is None or op not in _COMPARE:
            return None
        return f"{col} {_COMPARE[op]} {value}"
    if p.kind == A_Expr_Kind.AEXPR_IN:
        values = [_const(v) for v in p.rexpr] if isinstance(p.rexpr, tuple) else [None]
        if not values or any(v is None for v in values) or op not in ("=", "<>"):
            return None
        shown = ", ".join(v for v in values[:MAX_IN_VALUES] if v is not None)
        if len(values) > MAX_IN_VALUES:
            shown += f" and {len(values) - MAX_IN_VALUES} more"
        return f"{col} is {'one of' if op == '=' else 'not one of'} {shown}"
    if p.kind in (A_Expr_Kind.AEXPR_LIKE, A_Expr_Kind.AEXPR_ILIKE):
        value = _const(p.rexpr)
        if value is None or op not in ("~~", "~~*"):
            return None
        return f"{col} matches {value}"
    if p.kind == A_Expr_Kind.AEXPR_BETWEEN:
        bounds = [_const(v) for v in p.rexpr] if isinstance(p.rexpr, tuple) else []
        if len(bounds) != 2 or None in bounds:  # noqa: literals - BETWEEN has exactly two bounds
            return None
        return f"{col} between {bounds[0]} and {bounds[1]}"
    return None


def _by_position(node: Any, targets: tuple) -> Any:
    """GROUP BY 1 / ORDER BY 2 mean the Nth select-list item."""
    if isinstance(node, ast.A_Const) and isinstance(node.val, ast.Integer):
        i = node.val.ival - 1
        if 0 <= i < len(targets):
            return targets[i].val
    return node


def _clauses(stmt: Any) -> list[str]:
    out: list[str] = []
    if stmt.groupClause:
        out.append("grouped by " + _join_list([_expr(_by_position(g, stmt.targetList)) for g in stmt.groupClause]))
    if stmt.sortClause:
        keys = []
        for s in stmt.sortClause:
            direction = "highest first" if s.sortby_dir == SortByDir.SORTBY_DESC else "lowest first"
            keys.append(f"{_expr(_by_position(s.node, stmt.targetList))}, {direction}")
        out.append("sorted by " + ", then by ".join(keys))
    count = _const(stmt.limitCount) if stmt.limitCount is not None else None
    # The SQL agent adds the default row cap as a safety net; it is not the asker's choice.
    if count is not None and count != str(cfg.mcp.max_rows_default):
        out.append(f"top {count}")
    return out


def _fit(head: str, clauses: list[str]) -> str:
    """Drop trailing clauses (never part of one) until the sentence fits."""
    while clauses and len(head + ", " + ", ".join(clauses)) + 1 > MAX_CHARS:
        clauses = clauses[:-1]
    text = head + "".join(", " + c for c in clauses)
    if len(text) + 1 > MAX_CHARS:
        cut = text[: MAX_CHARS - 1]
        text = cut[: cut.rfind(" ")] if " " in cut else cut
    return text.rstrip(" ,") + "."


def _detailed(stmt: Any) -> str:
    tables, joins = _from_items(stmt.fromClause or ())
    if not tables:
        raise _Unsupported
    head = f"{_select_phrase(stmt.targetList)} {_source_phrase(tables, joins)}"
    phrases, other = _conditions(stmt.whereClause)
    if phrases:
        head += " where " + " and ".join(phrases) + (f" and {_OTHER}" if other else "")
    elif other:
        head += f" with {_OTHER}"
    return _fit(head, _clauses(stmt))


def explain_sql(sql: str | None) -> str | None:
    """Plain-English description of a single SELECT, or None when it cannot be given."""
    if not _PGLAST_AVAILABLE or not sql or not sql.strip():
        return None
    try:
        tree = pglast.parse_sql(sql)
        if len(tree) != 1 or not isinstance(tree[0].stmt, ast.SelectStmt):
            return None
        stmt = tree[0].stmt
        scan = _Scan()
        scan(tree)
        simple = (not scan.complex and stmt.withClause is None
                  and stmt.op == SetOperation.SETOP_NONE)
        if simple:
            try:
                text = _detailed(stmt)
                return text[0].upper() + text[1:]
            except _Unsupported:
                pass
        tables = scan.real_tables()
        if not tables:
            return None
        return _fit(f"Ran a query over {_capped(tables)}", [])
    except Exception as exc:  # pglast.Error and anything unexpected in an odd tree
        logger.debug("Could not explain SQL: %s", exc)
        return None
