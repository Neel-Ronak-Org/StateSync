#!/usr/bin/env python3
"""
scripts/eval_table_selection.py
Offline check of table selection (docs/specs/table_selection.md), with no model calls: for every
FIBEN gold query that runs, does the selection contain every table the gold SQL reads?

The tables are described the way the MCP describes them (pg_attribute for columns, pg_constraint
for foreign keys). Example values come from orchestrator/column_profiles.build_profiles over a
sample_rows-sized sample of each table. The selection is orchestrator/table_selection.select with
the values fixed in config. Needs the local FIBEN database (demo/fiben_db.sh) and psycopg + pglast
(e.g. the research venv).

    python scripts/eval_table_selection.py [--out docs/eval/table_selection_fiben.json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "orchestrator"))
sys.path.insert(0, str(REPO / "research" / "verifier"))
os.environ.setdefault("CONFIG_DIR", str(REPO / "config"))
if "psycopg_pool" not in sys.modules:
    try:
        import psycopg_pool  # noqa: F401
    except ImportError:
        import unittest.mock
        sys.modules["psycopg_pool"] = unittest.mock.MagicMock()

import psycopg  # noqa: E402

from config_loader import cfg  # noqa: E402
from column_profiles import build_profiles  # noqa: E402
from schema_retriever import _format_table  # noqa: E402
import hashlib  # noqa: E402

from table_selection import (build_value_index, select, tables_in_sql, text_columns, value_index_sql,  # noqa: E402
                             values_per_column)

DSN = "host=127.0.0.1 port=5434 dbname=fiben user=statesync_reader password=fiben-reader-pass"  # demo-only, demo/fiben_db.sh
SCHEMA = "fiben"


def describe(conn) -> dict[str, dict]:
    cols = conn.execute("""
        SELECT c.relname, a.attname, pg_catalog.format_type(a.atttypid, a.atttypmod), NOT a.attnotnull,
               col_description(a.attrelid, a.attnum)
        FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND c.relkind = 'r' AND a.attnum > 0 AND NOT a.attisdropped
        ORDER BY c.relname, a.attnum""", (SCHEMA,)).fetchall()
    out: dict[str, dict] = {}
    for table, name, typ, nullable, comment in cols:
        out.setdefault(table, {"columns": [], "foreign_keys": []})["columns"].append(
            {"name": name, "type": typ, "nullable": nullable, "comment": comment})
    fks = conn.execute("""
        SELECT rel.relname, att2.attname, cl.relname, att.attname
        FROM pg_catalog.pg_constraint con
        JOIN pg_catalog.pg_class rel ON rel.oid = con.conrelid
        JOIN pg_catalog.pg_namespace nsp ON nsp.oid = rel.relnamespace
        JOIN pg_catalog.pg_class cl ON cl.oid = con.confrelid
        JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS ck(attnum, ord) ON true
        JOIN LATERAL unnest(con.confkey) WITH ORDINALITY AS cfk(attnum, ord) ON cfk.ord = ck.ord
        JOIN pg_catalog.pg_attribute att2 ON att2.attrelid = con.conrelid AND att2.attnum = ck.attnum
        JOIN pg_catalog.pg_attribute att ON att.attrelid = con.confrelid AND att.attnum = cfk.attnum
        WHERE con.contype = 'f' AND nsp.nspname = %s""", (SCHEMA,)).fetchall()
    for table, col, ref_table, ref_col in fks:
        if table in out:
            out[table]["foreign_keys"].append({"column": col, "references_table": ref_table, "references_column": ref_col})
    return out


def samples(conn, tables: list[str]) -> dict[str, list[dict]]:
    out = {}
    for t in tables:
        cur = conn.execute(f'SELECT * FROM {SCHEMA}."{t}" LIMIT {int(cfg.mcp.sample_rows_hard_cap)}')
        names = [d.name for d in cur.description]
        out[t] = [dict(zip(names, row)) for row in cur.fetchall()]
    return out


def split_of(qid) -> str:
    """docs/specs/table_selection.md "v3 protocol": dev when sha256(qid) ends in an even hex digit."""
    return "dev" if int(hashlib.sha256(str(qid).encode()).hexdigest()[-1], 16) % 2 == 0 else "held"


def value_index(conn, descriptions: dict[str, dict]) -> dict:
    """The v3 value index, built from exactly the SQL the app runs (table_selection.value_index_sql)."""
    sel = cfg.table_selection
    values = {}
    per_column = values_per_column(int(sel.value_index_values_per_column), int(cfg.mcp.max_rows_hard_cap))
    for table, column in text_columns(descriptions, int(sel.value_index_max_columns)):
        sql = value_index_sql(SCHEMA, table, column, int(sel.value_index_scan_rows), per_column)
        values[(table, column)] = [row[0] for row in conn.execute(sql).fetchall()]
    return build_value_index(values, int(sel.value_index_value_chars), int(sel.value_index_max_words))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(REPO / "docs" / "eval" / "table_selection_fiben.json"))
    ap.add_argument("--version", choices=("v2", "v3"), default="v2")
    ap.add_argument("--split", choices=("all", "dev", "held"), default="all")
    args = ap.parse_args()
    from data import DB, load_items  # research/verifier: the runnable FIBEN gold, lower-cased for this port
    items = [it for it in load_items(DB()) if args.split == "all" or split_of(it["qid"]) == args.split]
    conn = psycopg.connect(DSN, autocommit=True)
    descriptions = describe(conn)
    sampled = samples(conn, list(descriptions))
    values = build_profiles({t: d["columns"] for t, d in descriptions.items()}, sampled)
    empty = {t for t, rows in sampled.items() if not rows}  # what the app's sample_rows calls see
    sel = cfg.table_selection
    v3 = args.version == "v3"
    index = value_index(conn, descriptions) if v3 and str(sel.value_index) == "on" else None
    extra = dict(value_index=index, value_tables=int(sel.value_tables),
                 min_value_chars=int(sel.value_min_token_chars)) if v3 else {}
    full_chars = len("\n\n".join(_format_table(f"{SCHEMA}.{t}", d) for t, d in descriptions.items()))
    rows = []
    for it in items:
        gold = set(tables_in_sql(it["gold_sql"]))
        chosen = select(it["question"], descriptions, values, int(sel.top_tables), int(sel.max_tables_selected), int(sel.fk_hops),
                        empty=empty, **extra)
        shown_chars = len("\n\n".join(_format_table(f"{SCHEMA}.{t}", descriptions[t]) for t in chosen))
        rows.append({"qid": it["qid"], "question": it["question"], "gold_tables": sorted(gold), "selected": chosen,
                     "all_found": gold <= set(chosen), "found_share": len(gold & set(chosen)) / len(gold) if gold else 1.0,
                     "shown": len(chosen), "schema_chars": shown_chars})
    n = len(rows)
    summary = {"version": args.version, "split": args.split, "questions": n, "tables_in_schema": len(descriptions), "empty_tables": len(empty), "full_schema_chars": full_chars,
               "all_gold_tables_found": round(sum(r["all_found"] for r in rows) / n, 3),
               "gold_table_share_found": round(sum(r["found_share"] for r in rows) / n, 3),
               "mean_tables_shown": round(sum(r["shown"] for r in rows) / n, 1),
               "mean_schema_chars": round(sum(r["schema_chars"] for r in rows) / n),
               "settings": {"top_tables": int(sel.top_tables), "max_tables_selected": int(sel.max_tables_selected),
                            "fk_hops": int(sel.fk_hops)}}
    Path(args.out).write_text(json.dumps({"summary": summary, "rows": rows}, indent=1))
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
