"""
mcp/tools/sql_templates.py
Turn an approved query into a template: its literal values become typed slots
(docs/V3_PLAN.md Phase 3c, slice 3, migration 022).

WHY THIS EXISTS: a question that differs from an approved one only in its values
("delivered orders since 2018-01-01" vs "since 2019-06-01") does not need the SQL
model; the approved SQL with new values is the answer. Finding those values with
the SQL parser (pglast, not regexes) means a string that merely LOOKS like a
value inside a function call, a CASE arm or the select list is never touched.

WHAT BECOMES A SLOT (and nothing else):
  - a literal on one side of =, <>, <, <=, >, >=, LIKE, ILIKE with a plain column
    on the other side (a `'2018-01-01'::date` cast around the literal is kept);
  - the literals of `column IN (...)` and the bounds of `column BETWEEN a AND b`;
  - the LIMIT count.
Only a single plain SELECT qualifies: a CTE, UNION or sub-select returns None,
because a value inside one is not safely a "same query, other values".

The template text carries $1..$n; the orchestrator fills them (orchestrator/
templates.py) and the result goes through the same SQL gate as any query.

Pure functions: no I/O, unit-tested in tests/test_sql_templates.py.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from config_loader import cfg

try:
    from pglast import ast, parse_sql
    from pglast.enums import A_Expr_Kind, SetOperation
    from pglast.stream import RawStream
    from pglast.visitors import Visitor
except ImportError:  # pragma: no cover - the gate refuses everything without pglast too
    ast = None

_COMPARISONS = frozenset({"=", "<>", "<", "<=", ">", ">=", "~~", "~~*", "!~~", "!~~*"})
_CASTS = frozenset({"date", "timestamp", "timestamptz"})
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_ISO_DATETIME = re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}")
_PARAM_TEXT = re.compile(r"\$\d")


class _Unsupported(Exception):
    """The statement has a construct a template must not contain."""


class _Scan(Visitor):
    def visit_SubLink(self, ancestors, node):  # noqa: N802 -- pglast visitor naming
        raise _Unsupported("sub-select")

    def visit_RangeSubselect(self, ancestors, node):  # noqa: N802
        raise _Unsupported("sub-select")

    def visit_CommonTableExpr(self, ancestors, node):  # noqa: N802
        raise _Unsupported("CTE")

    def visit_ParamRef(self, ancestors, node):  # noqa: N802
        raise _Unsupported("parameter")


def slot_type(const: Any) -> str | None:
    """The slot type of a literal's value (Integer/Float/String node), or None for NULL/booleans."""
    if isinstance(const, ast.Integer):
        return "integer"
    if isinstance(const, ast.Float):
        return "number"
    if isinstance(const, ast.String):
        text = const.sval
        if _ISO_DATE.fullmatch(text):
            try:
                date.fromisoformat(text)
                return "date"
            except ValueError:
                return "text"
        if _ISO_DATETIME.match(text):
            try:
                datetime.fromisoformat(text)
                return "timestamp"
            except ValueError:
                return "text"
        return "text"
    return None


def _example(const: Any) -> str:
    return str(const.ival if isinstance(const, ast.Integer) else const.fval if isinstance(const, ast.Float) else const.sval)


def _column_name(node: Any) -> str | None:
    if not isinstance(node, ast.ColumnRef):
        return None
    parts = [f.sval for f in node.fields if isinstance(f, ast.String)]
    return ".".join(parts) if parts and len(parts) == len(node.fields) else None


def _literal(node: Any) -> Any:
    """The value node (Integer/Float/String) of a slot-able literal, or None.

    A `'2018-01-01'::date` / `date '...'` cast around a string is looked through (the
    cast stays in the template around the parameter); any other cast is not a slot.
    """
    if isinstance(node, ast.A_Const) and not node.isnull and slot_type(node.val) is not None:
        return node.val
    if (isinstance(node, ast.TypeCast) and isinstance(node.arg, ast.A_Const) and not node.arg.isnull
            and node.typeName.names and node.typeName.names[-1].sval in _CASTS
            and isinstance(node.arg.val, ast.String)):
        return node.arg.val
    return None


def _conditions(node: Any):
    """Comparison nodes reachable through AND/OR/NOT and JOIN ... ON only."""
    if isinstance(node, ast.BoolExpr):
        for arg in node.args:
            yield from _conditions(arg)
    elif isinstance(node, ast.A_Expr):
        yield node
    elif isinstance(node, ast.JoinExpr):
        yield from _conditions(node.larg)
        yield from _conditions(node.rarg)
        yield from _conditions(node.quals)


def _find(select: Any) -> list[dict[str, Any]]:
    """Slot sites in source order (FROM/JOIN, WHERE, HAVING, LIMIT): {"val", "column", "set"}.

    pglast nodes carry no source offsets, so the order is the traversal order, which is
    the order the deparsed text reads in ($1 is the first value in the text).
    """
    sites: list[dict[str, Any]] = []

    def add(node: Any, column: str | None, setter) -> None:
        val = _literal(node)
        if val is None:
            return
        sites.append({"val": val, "column": column, "set": setter})

    roots = [*(select.fromClause or ()), select.whereClause, select.havingClause]
    for root in roots:
        for cond in _conditions(root):
            name = cond.name[-1].sval if cond.name else None
            kind = cond.kind
            if kind in (A_Expr_Kind.AEXPR_OP, A_Expr_Kind.AEXPR_LIKE, A_Expr_Kind.AEXPR_ILIKE) and name in _COMPARISONS:
                for lit_attr, col_attr in (("rexpr", "lexpr"), ("lexpr", "rexpr")):
                    column = _column_name(getattr(cond, col_attr))
                    if column is None:
                        continue
                    node = getattr(cond, lit_attr)
                    add(node, column, _replacer(cond, lit_attr, node))
            elif kind == A_Expr_Kind.AEXPR_IN and _column_name(cond.lexpr) and isinstance(cond.rexpr, tuple):
                column = _column_name(cond.lexpr)
                for i, item in enumerate(cond.rexpr):
                    add(item, column, _list_replacer(cond, i, item))
            elif (kind in (A_Expr_Kind.AEXPR_BETWEEN, A_Expr_Kind.AEXPR_NOT_BETWEEN,
                           A_Expr_Kind.AEXPR_BETWEEN_SYM, A_Expr_Kind.AEXPR_NOT_BETWEEN_SYM)
                  and _column_name(cond.lexpr) and isinstance(cond.rexpr, tuple)):
                column = _column_name(cond.lexpr)
                for i, item in enumerate(cond.rexpr):
                    add(item, column, _list_replacer(cond, i, item))

    limit = select.limitCount
    if isinstance(limit, ast.A_Const) and not limit.isnull and isinstance(limit.val, ast.Integer):
        sites.append({"val": limit.val, "column": None,
                      "set": lambda param, s=select: setattr(s, "limitCount", param)})
    return sites


def _replacer(cond: Any, attr: str, node: Any):
    """Setter that puts a parameter where the literal was, keeping a date/timestamp cast."""
    def set_param(param: Any) -> None:
        if isinstance(node, ast.TypeCast):
            node.arg = param
        else:
            setattr(cond, attr, param)
    return set_param


def _list_replacer(cond: Any, index: int, node: Any):
    def set_param(param: Any) -> None:
        if isinstance(node, ast.TypeCast):
            node.arg = param
        else:
            items = list(cond.rexpr)
            items[index] = param
            cond.rexpr = tuple(items)
    return set_param


def derive_template(sql: str) -> tuple[str, list[dict[str, Any]]] | None:
    """(template_sql, slots) for an approved query, or None when it has no usable slots.

    None also covers every parse problem and anything that is not a single plain
    SELECT: a template is an optimisation, so "cannot" is always a safe answer.
    """
    if ast is None or not isinstance(sql, str) or _PARAM_TEXT.search(sql):
        # A literal "$1" in the text would be indistinguishable from a placeholder when filling.
        return None
    try:
        stmts = parse_sql(sql)
        if len(stmts) != 1 or not isinstance(stmts[0].stmt, ast.SelectStmt):
            return None
        select = stmts[0].stmt
        if (select.op != SetOperation.SETOP_NONE or select.withClause is not None
                or select.intoClause is not None):
            return None
        _Scan()(select)
        sites = _find(select)
        if not sites or len(sites) > int(cfg.templates.max_slots):
            return None
        slots: list[dict[str, Any]] = []
        for n, site in enumerate(sites, start=1):
            kind = "integer" if site["column"] is None else slot_type(site["val"])
            site["set"](ast.ParamRef(number=n, location=-1))
            slots.append({"name": f"s{n}", "type": kind, "column": site["column"], "example": _example(site["val"])})
        return RawStream()(stmts[0]), slots
    except Exception:  # noqa: BLE001 -- any parse or shape problem means "no template"
        return None
