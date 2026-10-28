"""
tests/test_card_coverage.py
Pins the front door's coverage line (front_door.card_coverage; Paper B rerun R4).

WHY THIS EXISTS: the line must (a) list every value of a text column with few distinct values and the range
of a date or year column, (b) leave out a column with too many values or a failed query, (c) stay under its
character cap at an item boundary, and (d) leave the card unchanged when the flag is off (every earlier result
was measured that way). MCP is faked; no network.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))

import card_coverage as cc  # noqa: E402
import front_door as fd  # noqa: E402


def _cols(*pairs: tuple[str, str]) -> list[dict]:
    return [{"name": n, "type": t} for n, t in pairs]


def test_column_kind():
    assert cc.column_kind("company", "text") == "values"
    assert cc.column_kind("ticker", "character varying(10)") == "values"
    assert cc.column_kind("period_end", "date") == "range"
    assert cc.column_kind("period_end_year", "integer") == "range"
    assert cc.column_kind("value", "numeric") is None
    assert cc.column_kind("cik", "bigint") is None


def test_sql_quotes_identifiers_and_asks_one_extra_value():
    sql = cc.values_sql("cn_x", "annual metrics", 'we"ird', 40)
    assert '"cn_x"."annual metrics"' in sql and '"we""ird"' in sql and "LIMIT 41" in sql
    assert "min(" in cc.range_sql("s", "t", "d") and "max(" in cc.range_sql("s", "t", "d")


def test_format_cuts_at_item_boundary():
    entries = [("t.a", "x" * 30), ("t.b", "y" * 30)]
    full = cc.format_coverage(entries, 1000)
    assert full.startswith(cc.HEADER) and "t.a:" in full and "t.b:" in full
    cut = cc.format_coverage(entries, len(cc.HEADER) + 40)
    assert "t.a:" in cut and "t.b:" not in cut
    skip = cc.format_coverage([("t.long", "z" * 80), ("t.short", "s")], len(cc.HEADER) + 40)
    assert "t.long" not in skip and "t.short: s;" in skip
    assert cc.format_coverage([], 100) == ""


def test_build_lists_small_columns_skips_large_and_failed():
    async def fake_select(session, sql, max_rows=None):
        if '"company"' in sql:
            return {"ok": True, "rows": [{"v": "3M"}, {"v": "Adobe"}]}
        if '"tag"' in sql:
            return {"ok": True, "rows": [{"v": str(i)} for i in range(41)]}
        if '"unit"' in sql:
            raise RuntimeError("timeout")
        if "min(" in sql:
            return {"ok": True, "rows": [{"lo": "2006-12-01", "hi": "2026-06-30"}]}
        return {"ok": False}

    tables = [("annual_metrics", _cols(("company", "text"), ("tag", "text"), ("unit", "text"),
                                       ("period_end", "date"), ("value", "numeric")))]
    with patch.object(cc, "data_run_select", fake_select):
        line = asyncio.run(cc.build_coverage(SimpleNamespace(), "cn_x", tables, 40, 2500))
    assert "annual_metrics.company: 3M, Adobe;" in line
    assert "annual_metrics.period_end: 2006-12-01 to 2026-06-30;" in line
    assert ".tag:" not in line and ".unit:" not in line and ".value:" not in line


def test_order_is_shortest_first_and_drops_repeats():
    entries = [("a.tag", ["x"] * 0 + [f"T{i}" for i in range(30)]), ("a.period", "2006 to 2026"),
               ("m.company", ["3M", "Adobe"]), ("r.company", ["3M", "Adobe"]), ("a.unit", ["USD"])]
    ordered = cc.order_entries(entries)
    assert [k for k, _ in ordered] == ["a.unit", "m.company", "a.period", "a.tag"]


def _fake_schema(listing, describe):
    async def lt(session, dataset):
        return listing

    async def dt(session, table, dataset):
        return describe[table]
    return lt, dt


def test_the_card_gains_the_coverage_line(monkeypatch):
    listing = {"tables": [{"name": "annual_metrics"}]}
    describe = {"annual_metrics": {"columns": _cols(("company", "text"), ("value", "numeric"))}}
    lt, dt = _fake_schema(listing, describe)

    async def fake_cov(session, schema, tables, max_values, max_chars):
        return "Coverage (every value the rows hold): annual_metrics.company: 3M;"

    fd._CARD_CACHE.clear()
    with patch.object(fd, "schema_list_tables", lt), patch.object(fd, "schema_describe_table", dt), \
            patch.object(fd, "build_coverage", fake_cov):
        card, status = asyncio.run(fd._schema_card(SimpleNamespace(), "t", "cn_x"))
    assert status == "ok"
    assert card == ('Database "cn_x": annual_metrics(company, value)\n'
                    "Coverage (every value the rows hold): annual_metrics.company: 3M;")
    fd._CARD_CACHE.clear()


def test_an_old_card_is_served_at_once_and_rebuilt_in_the_background():
    listing = {"tables": [{"name": "annual_metrics"}]}
    describe = {"annual_metrics": {"columns": _cols(("company", "text"))}}
    lt, dt = _fake_schema(listing, describe)
    lines = iter(["Coverage: annual_metrics.company: 3M;", "Coverage: annual_metrics.company: 3M, TESLA;"])

    async def fake_cov(session, schema, tables, max_values, max_chars):
        return next(lines)

    class FakeSession:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

    clock = [1000.0]

    async def scenario():
        first, s1 = await fd._schema_card(SimpleNamespace(), "t", "cn_x")
        clock[0] += 10
        again, s2 = await fd._schema_card(SimpleNamespace(), "t", "cn_x")
        clock[0] += float(fd.cfg.front_door.card_cache_ttl_s)
        stale, s3 = await fd._schema_card(SimpleNamespace(), "t", "cn_x")   # old card now, rebuild started
        await asyncio.gather(*list(fd._REFRESH_TASKS))
        fresh, s4 = await fd._schema_card(SimpleNamespace(), "t", "cn_x")
        return (first, again, stale, fresh), (s1, s2, s3, s4)

    fd._CARD_CACHE.clear()
    with patch.object(fd, "schema_list_tables", lt), patch.object(fd, "schema_describe_table", dt), \
            patch.object(fd, "build_coverage", fake_cov), patch.object(fd, "MCPSession", FakeSession), \
            patch.object(fd.time, "monotonic", lambda: clock[0]):
        (first, again, stale, fresh), statuses = asyncio.run(scenario())
    fd._CARD_CACHE.clear()
    assert statuses == ("ok", "cached", "cached", "cached")
    assert again == stale == first and "TESLA" not in first
    assert "TESLA" in fresh
    assert not fd._REFRESHING
