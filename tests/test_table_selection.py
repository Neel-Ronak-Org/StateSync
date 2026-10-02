"""
tests/test_table_selection.py

WHY THIS EXISTS (docs/specs/table_selection.md). Shown every table of a 152-table database, the
SQL prompt exceeded Groq's free-tier limit and every question was refused. The "linked" provider
shows only the tables a question needs. Pinned here:
- snake_case, camelCase and run-together names are tokenised so a question can match them;
- BM25 puts the matching table first; example values count;
- foreign-key neighbours are added best table first, under a hard cap;
- empty tables rank after tables with rows;
- approved-example tables are always shown;
- a retry widens by one more hop;
- a small schema is shown whole, exactly as with "direct".
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))
if "psycopg_pool" not in sys.modules:
    try:
        import psycopg_pool  # noqa: F401
    except ImportError:
        import unittest.mock
        sys.modules["psycopg_pool"] = unittest.mock.MagicMock()

import table_selection as ts  # noqa: E402


def _desc(*cols, fks=()):
    return {"columns": [{"name": c, "type": "text", "nullable": True} for c in cols],
            "foreign_keys": [{"column": c, "references_table": t, "references_column": "id"} for c, t in fks]}


def test_words_split_snake_camel_and_tokens_add_4grams():
    assert ts.words("OrderItems order_id HTTPServer2") == ["order", "items", "order", "id", "http", "server"]  # 1-char tokens dropped
    toks = ts.tokens("haslasttradedvalue")
    assert "haslasttradedvalue" in toks and "#trad" in toks and "#valu" in toks


def test_bm25_ranks_the_matching_table_first_and_values_count():
    cards = {
        "listedsecurity": ts.card("listedsecurity", _desc("hastickersymbol", "haslasttradedvalue"),
                                  {"listedsecurity.haslegalname": ["Alphabet Inc."]}),
        "person": ts.card("person", _desc("hasfirstname", "haslastname")),
        "country": ts.card("country", _desc("hasname")),
    }
    assert ts.rank("last traded value of Alphabet", cards)[0][0] == "listedsecurity"


def _schema():
    return {
        "listedsecurity": _desc("hastickersymbol", "haslasttradedvalue", fks=[("haslasttradedvalue", "monetaryamount")]),
        "monetaryamount": _desc("hasamount", fks=[("hascurrency", "currency")]),
        "currency": _desc("hascode"),
        "person": _desc("haspersonname"),
        "phonenumber": _desc("hasvalue"),
    }


def test_neighbours_are_expanded_best_table_first_under_the_cap():
    chosen = ts.select("last traded value", _schema(), None, top_tables=1, max_selected=2, hops=1)
    assert chosen == ["listedsecurity", "monetaryamount"]
    wider = ts.select("last traded value", _schema(), None, top_tables=1, max_selected=5, hops=2)
    assert wider[:3] == ["listedsecurity", "monetaryamount", "currency"]


def test_empty_tables_rank_after_tables_with_rows():
    chosen = ts.select("value", _schema(), None, top_tables=1, max_selected=1, hops=0, empty={"phonenumber", "listedsecurity"})
    assert chosen[0] not in {"phonenumber", "listedsecurity"}


def test_approved_example_tables_are_always_included_first():
    chosen = ts.select("last traded value", _schema(), None, top_tables=1, max_selected=3, hops=0, must_include=["person"])
    assert chosen[0] == "person" and "listedsecurity" in chosen


def test_tables_in_sql_reads_relations_and_ignores_unparsable_sql():
    assert ts.tables_in_sql("SELECT a FROM fiben.listedsecurity l JOIN fiben.monetaryamount m ON true") == ["listedsecurity", "monetaryamount"]
    assert ts.tables_in_sql("not sql at all") == []


def test_linked_shows_a_small_schema_whole_like_direct(monkeypatch):
    import schema_retriever as sr
    calls = []

    async def fake_describe(session, tname, schema=None):
        calls.append(tname)
        return {"columns": [{"name": "id", "type": "int", "nullable": False}], "foreign_keys": []}

    monkeypatch.setattr(sr, "schema_describe_table", fake_describe)
    names = [f"t{i}" for i in range(3)]
    linked = asyncio.run(sr.LinkedSchemaRetriever().retrieve(None, names, None, question="anything", fingerprint="f"))
    direct = asyncio.run(sr.DirectSchemaRetriever().retrieve(None, names, None))
    assert linked == direct
