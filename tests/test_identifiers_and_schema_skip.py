"""
tests/test_identifiers_and_schema_skip.py

WHY THIS EXISTS. The FIBEN trial (docs/eval/external_datasets.md) connected a real 152-table
database with upper-case names and found two failures before any model ran:
- the MCP refused every table name that was not lower-case (^[a-z_][a-z0-9_]{0,62}$);
- one refused describe_table sank the whole question as "service unavailable".
Names may now be mixed case, but still only letters, digits and underscores, so a name can
never carry a quote into SQL. Mixed-case names reach the model quoted. A table the MCP
refuses is skipped, not fatal.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))


def test_the_mcp_identifier_rule_allows_case_and_never_a_quote():
    pytest.importorskip("psycopg")
    sys.path.insert(0, str(REPO_ROOT / "mcp"))
    if "psycopg_pool" not in sys.modules:
        try:
            import psycopg_pool  # noqa: F401
        except ImportError:
            import unittest.mock
            sys.modules["psycopg_pool"] = unittest.mock.MagicMock()
    from tools.schema_tools import IDENTIFIER
    for ok in ("orders", "OrderItems", "LISTEDSECURITY", "_t1", "a" * 63):
        assert IDENTIFIER.match(ok), ok
    for bad in ('Orders"; DROP TABLE x; --', "a-b", "a b", "1abc", "", "a" * 64, 'x"y', "sch.tab", "t;"):
        assert not IDENTIFIER.match(bad), bad


def _retriever():
    sys.path.insert(0, str(REPO_ROOT / "orchestrator"))
    if "psycopg_pool" not in sys.modules:
        try:
            import psycopg_pool  # noqa: F401
        except ImportError:
            import unittest.mock
            sys.modules["psycopg_pool"] = unittest.mock.MagicMock()
    import schema_retriever
    return schema_retriever


def test_mixed_case_names_reach_the_model_quoted_and_plain_ones_unchanged():
    sr = _retriever()
    assert sr.sql_ident("orders") == "orders" and sr.sql_ident("OrderItems") == '"OrderItems"'
    text = sr._format_table("cn_0123456789abcdef.OrderItems", {
        "columns": [{"name": "ItemID", "type": "bigint", "nullable": False}, {"name": "price", "type": "numeric", "nullable": True}],
        "foreign_keys": [{"column": "OrderID", "references_table": "Orders", "references_column": "OrderID"}]})
    assert 'TABLE cn_0123456789abcdef."OrderItems":' in text
    assert '"ItemID" bigint NOT NULL' in text and "price numeric NULL" in text
    assert 'FK: cn_0123456789abcdef."OrderItems"."OrderID" -> "Orders"."OrderID"' in text


def test_a_refused_table_is_skipped_and_only_all_refused_is_an_error(monkeypatch):
    httpx = pytest.importorskip("httpx")
    sr = _retriever()

    def refused():
        req = httpx.Request("POST", "http://mcp/rpc")
        return httpx.HTTPStatusError("422", request=req, response=httpx.Response(422, request=req))

    async def fake_describe(session, tname, schema=None):
        if tname == "bad":
            raise refused()
        return {"columns": [{"name": "id", "type": "int", "nullable": False}], "foreign_keys": []}

    monkeypatch.setattr(sr, "schema_describe_table", fake_describe)
    text, descs = asyncio.run(sr.DirectSchemaRetriever().retrieve(None, ["good", "bad"]))
    assert "TABLE good:" in text and "bad" not in text and len(descs) == 1
    with pytest.raises(RuntimeError):
        asyncio.run(sr.DirectSchemaRetriever().retrieve(None, ["bad"]))
