"""
research/verifier/data.py
Items, split, mutations and execution labels for the setup-time verifier study (README.md).

Everything that decides a label lives here, so every model is scored against the same truth:
a candidate is correct when its result multiset equals the gold result multiset.
"""

from __future__ import annotations

import copy
import hashlib
import json
import random
import re
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

import psycopg
from pglast import ast, parse_sql
from pglast.stream import RawStream

REPO = Path(__file__).resolve().parents[2]
FIBEN_QUERIES = REPO / "data" / "external" / "fiben" / "FIBEN_Queries.json"
OUT = Path(__file__).resolve().parent / "out"
DSN = "host=127.0.0.1 port=5434 dbname=fiben user=statesync_reader password=fiben-reader-pass"  # demo-only, demo/fiben_db.sh
TIMEOUT_MS = 20000
SEED = 20261002
TEST_SHARE = 0.3
RESULT_ROWS_SHOWN = 3
CELL_CHARS = 40


# ── Running SQL and comparing results ─────────────────────────────────────────

class DB:
    def __init__(self) -> None:
        self.conn = psycopg.connect(DSN, autocommit=True)
        self.conn.execute(f"SET statement_timeout = {TIMEOUT_MS}")

    def run(self, sql: str) -> tuple[list[str], list[tuple]] | None:
        """(columns, rows), or None if it fails or times out."""
        try:
            cur = self.conn.execute(sql)
            return [d.name for d in cur.description or []], cur.fetchall()
        except psycopg.Error:
            return None


def _cell(v: Any) -> str:
    if isinstance(v, (int, float, Decimal)) and not isinstance(v, bool):
        return f"{float(v):.4g}"
    return str(v).strip().lower()


def result_key(rows: list[tuple]) -> list[tuple]:
    """The comparison form of a result: cells normalised, row and column order ignored."""
    return sorted(tuple(sorted(_cell(c) for c in row)) for row in rows)


def column_keys(columns: list[str], rows: list[tuple]) -> list[list[str]]:
    """Each column as its sorted multiset of normalised cells."""
    return [sorted(_cell(row[i]) for row in rows) for i in range(len(columns))]


def relaxed_correct(gold_columns: list[str], gold_cols: list[list[str]], columns: list[str], rows: list[tuple]) -> bool:
    """FIBEN's gold SQL often projects extra identifying columns (ticker, legal name) the question
    did not ask for, so an exact multiset match marks a right answer with fewer columns wrong.
    Relaxed rule (decided 2026-10-02 before any model was scored; README "Deviations"): same row
    count; every candidate column equals a distinct gold column as a value multiset; and every
    gold aggregate column (FIBEN names them AggResultN) is matched -- or, with no aggregate, at
    least one column is."""
    if not columns or (gold_cols and len(rows) != len(gold_cols[0])):
        return False
    cand = column_keys(columns, rows)
    free = list(range(len(gold_cols)))
    matched = []
    for col in cand:
        hit = next((g for g in free if gold_cols[g] == col), None)
        if hit is None:
            return False
        free.remove(hit)
        matched.append(hit)
    aggs = [i for i, name in enumerate(gold_columns) if name.lower().startswith("aggresult")]
    return all(a in matched for a in aggs) if aggs else bool(matched)


def summarise(columns: list[str], rows: list[tuple]) -> str:
    """What the verifier sees of a result: row count, columns, the first rows (truncated cells)."""
    shown = [" | ".join(str(c)[:CELL_CHARS] for c in row) for row in rows[:RESULT_ROWS_SHOWN]]
    return f"{len(rows)} rows; columns: {', '.join(columns)}" + ("; first rows: " + " / ".join(shown) if shown else "")


# ── FIBEN items and the split ─────────────────────────────────────────────────

def gold_sql(sql: str) -> str:
    """FIBEN's Db2 gold SQL for the lower-case Postgres port (mechanical; demo/fiben_db.sh)."""
    sql = re.sub(r'"([A-Za-z_][A-Za-z0-9_]*)"', lambda m: '"' + m[1].lower() + '"', sql.strip().rstrip(";"))
    return re.sub(r"\bFIBEN\.", "fiben.", sql)


def _group_key(sql: str) -> str:
    return hashlib.sha1(" ".join(sql.lower().split()).encode()).hexdigest()


def load_items(db: DB) -> list[dict[str, Any]]:
    """FIBEN questions whose gold SQL runs and returns at least one row, with their gold result."""
    items = []
    for q in json.loads(FIBEN_QUERIES.read_text()):
        sql = gold_sql(q["SQL"])
        out = db.run(sql)
        if out is None or not out[1]:
            continue
        items.append({"qid": q["uniqueQueryID"], "question": q["question"].strip(), "gold_sql": sql,
                      "group": _group_key(sql), "query_type": q["queryType"].lower(),
                      "gold_columns": out[0], "gold_key": result_key(out[1]), "gold_rows": len(out[1]),
                      "gold_cols": column_keys(out[0], out[1])})
    return items


def split(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """70/30 by gold-SQL group, so paraphrases never straddle the split. Fixed seed."""
    groups = sorted({it["group"] for it in items})
    random.Random(SEED).shuffle(groups)
    test_groups = set(groups[: round(len(groups) * TEST_SHARE)])
    return {"train": [it for it in items if it["group"] not in test_groups],
            "test": [it for it in items if it["group"] in test_groups]}


# ── Mutations ─────────────────────────────────────────────────────────────────

_OP_SWAPS = {">": [">=", "<"], "<": ["<=", ">"], ">=": [">", "<="], "<=": ["<", ">="], "=": ["<>"], "<>": ["="]}
_AGG_SWAPS = {"sum": ["avg", "max"], "avg": ["sum", "min"], "max": ["min", "avg"], "min": ["max", "avg"], "count": ["count"]}


def _nodes(node: Any, path: tuple = ()) -> Iterator[tuple[tuple, Any]]:
    """Every AST node with its path of (attribute, index) steps."""
    if isinstance(node, ast.Node):
        yield path, node
        for name in node:
            value = getattr(node, name)
            if isinstance(value, tuple):
                for i, child in enumerate(value):
                    yield from _nodes(child, path + ((name, i),))
            else:
                yield from _nodes(value, path + ((name, None),))


def _at(root: Any, path: tuple) -> Any:
    node = root
    for name, i in path:
        node = getattr(node, name)
        if i is not None:
            node = node[i]
    return node


def _variants(node: Any) -> Iterator[tuple[str, Any]]:
    """(operator name, a mutated copy of this node) for every mutation that applies to it."""
    if isinstance(node, ast.A_Expr) and node.kind == 0 and node.name and len(node.name) == 1:
        op = node.name[0].sval
        for new in _OP_SWAPS.get(op, []):
            m = copy.deepcopy(node)
            m.name = (ast.String(sval=new),)
            yield f"op {op}->{new}", m
    if isinstance(node, ast.A_Const) and isinstance(node.val, ast.Integer):
        v = node.val.ival
        for new in {v + 1, v - 1, v * 10} - {v}:
            m = copy.deepcopy(node)
            m.val = ast.Integer(ival=new)
            yield f"const {v}->{new}", m
    if isinstance(node, ast.FuncCall) and node.funcname and len(node.funcname) == 1:
        name = node.funcname[-1].sval.lower()
        for new in _AGG_SWAPS.get(name, []):
            m = copy.deepcopy(node)
            if name == "count":
                if node.agg_star:
                    continue
                m.agg_distinct = not node.agg_distinct
                yield f"count distinct {'off' if node.agg_distinct else 'on'}", m
            else:
                m.funcname = (ast.String(sval=new),)
                yield f"agg {name}->{new}", m
    if isinstance(node, ast.BoolExpr) and node.boolop == 0 and len(node.args) >= 2:  # AND
        for i in range(len(node.args)):
            m = copy.deepcopy(node)
            m.args = node.args[:i] + node.args[i + 1:]
            yield f"drop condition {i}", m
    if isinstance(node, ast.SortBy):
        m = copy.deepcopy(node)
        m.sortby_dir = 1 if node.sortby_dir == 2 else 2  # DESC <-> ASC
        yield "flip sort", m
    if isinstance(node, ast.JoinExpr) and node.jointype == 0:
        m = copy.deepcopy(node)
        m.jointype = 1  # INNER -> LEFT
        yield "inner->left join", m


def _replace(root: Any, path: tuple, new: Any) -> Any:
    out = copy.deepcopy(root)
    parent_path, (name, i) = path[:-1], path[-1]
    parent = _at(out, parent_path)
    if i is None:
        setattr(parent, name, new)
    else:
        seq = list(getattr(parent, name))
        seq[i] = new
        setattr(parent, name, tuple(seq))
    return out


def mutations(sql: str) -> list[tuple[str, str]]:
    """(operator, mutated SQL) for every single-site mutation of sql, plus a DISTINCT toggle."""
    root = parse_sql(sql)[0].stmt
    out: dict[str, str] = {}
    for path, node in list(_nodes(root)):
        if not path:
            continue
        for name, new in _variants(node):
            try:
                text = RawStream()(_replace(root, path, new))
            except Exception:  # noqa: BLE001 -- a mutation the deparser cannot print is skipped
                continue
            out.setdefault(text, name)
    if isinstance(root, ast.SelectStmt):
        m = copy.deepcopy(root)
        m.distinctClause = None if root.distinctClause else (None,)
        try:
            out.setdefault(RawStream()(m), "toggle distinct")
        except Exception:  # noqa: BLE001
            pass
    return [(op, text) for text, op in out.items()]


def label(db: DB, item: dict[str, Any], sql: str) -> dict[str, Any] | None:
    """A labelled candidate, or None when it does not run."""
    out = db.run(sql)
    if out is None:
        return None
    columns, rows = out
    strict = result_key(rows) == item["gold_key"]
    relaxed = strict or relaxed_correct(item["gold_columns"], item["gold_cols"], columns, rows)
    return {"qid": item["qid"], "question": item["question"], "sql": sql, "result": summarise(columns, rows),
            "correct": relaxed, "correct_strict": strict, "rows": len(rows)}


def build(max_mutants_per_item: int = 12) -> None:
    """out/items.json, out/train.jsonl (gold + mutants of train questions), out/test_mut.jsonl."""
    OUT.mkdir(exist_ok=True)
    db = DB()
    items = load_items(db)
    parts = split(items)
    (OUT / "items.json").write_text(json.dumps(parts, indent=1, default=str))
    rng = random.Random(SEED)
    for part, name in (("train", "train"), ("test", "test_mut")):
        rows = []
        for it in parts[part]:
            gold = label(db, it, it["gold_sql"])
            if gold:
                rows.append({**gold, "source": "gold", "operator": None})
            muts = mutations(it["gold_sql"])
            rng.shuffle(muts)
            for op, sql in muts[:max_mutants_per_item]:
                lab = label(db, it, sql)
                if lab:
                    rows.append({**lab, "source": "mutant", "operator": op})
        with open(OUT / f"{name}.jsonl", "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        n_ok = sum(r["correct"] for r in rows)
        print(f"{name}: {len(parts[part])} questions, {len(rows)} candidates, {n_ok} correct / {len(rows) - n_ok} wrong")


if __name__ == "__main__":
    build()
