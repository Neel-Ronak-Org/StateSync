"""
tests/test_answer_checks.py
Pins the deterministic answer checks (flag pipeline.answer_checks).

WHY THIS EXISTS: a join that repeats one side's rows makes SUM/AVG double count and the
statement still "succeeds". The checks must (a) only ever probe the narrow shape they can
reason about, (b) confirm fan-out with counts before accusing, (c) cost one corrective retry
and then a caveat rather than a loop, (d) never run -- not even a query -- with the flag off
or on the fast path. MCP and the model are faked; no network.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))

pytest.importorskip("pglast")
import pglast  # noqa: E402
from pglast.stream import RawStream  # noqa: E402

import answer_checks as ac  # noqa: E402
from nodes import sql_agent as sa  # noqa: E402

FANOUT_SQL = (
    "SELECT c.state, SUM(o.amount) FROM live.orders o "
    "JOIN live.customers c ON c.customer_id = o.customer_id GROUP BY c.state"
)


def _parts(count_sql: str) -> tuple[str, str | None]:
    """(FROM text, WHERE text) of a count query, via the parser rather than string compare."""
    stmt = pglast.parse_sql(count_sql)[0].stmt
    where = RawStream()(stmt.whereClause) if stmt.whereClause is not None else None
    return ", ".join(RawStream()(i) for i in stmt.fromClause), where


# -- fanout_probe -------------------------------------------------------------

@pytest.mark.parametrize("sql", [
    "SELECT SUM(o.amount) FROM live.orders o",
    "SELECT c.state, COUNT(DISTINCT o.order_id) FROM live.orders o JOIN live.customers c ON c.customer_id = o.customer_id GROUP BY c.state",
    "SELECT COUNT(*) FROM live.orders o",
    "WITH x AS (SELECT * FROM live.orders) SELECT SUM(o.amount) FROM x o JOIN live.customers c ON c.customer_id = o.customer_id",
    "SELECT SUM(o.amount) FROM live.orders o JOIN live.customers c ON c.customer_id = o.customer_id "
    "UNION SELECT SUM(o.amount) FROM live.orders o JOIN live.customers c ON c.customer_id = o.customer_id",
    "SELECT SUM(s.amount) FROM (SELECT * FROM live.orders) s JOIN live.customers c ON c.customer_id = s.customer_id",
    "SELECT SUM(amount) FROM live.orders o JOIN live.customers c ON c.customer_id = o.customer_id",
    "not sql at all",
    "",
])
def test_fanout_probe_not_applicable(sql):
    assert ac.fanout_probe(sql) is None


def test_fanout_probe_builds_count_query_with_same_from_and_where():
    sql = FANOUT_SQL.replace("GROUP BY", "WHERE o.amount > 5 GROUP BY")
    probes = ac.fanout_probe(sql)
    assert len(probes) == 1
    p = probes[0]
    assert (p.table, p.alias) == ("live.orders", "o")
    assert ac.KEY_PLACEHOLDER in p.count_sql
    q = p.count_sql_for("order_id")
    stmt = pglast.parse_sql(q)[0].stmt
    assert not stmt.groupClause and not stmt.havingClause and not stmt.sortClause and not stmt.limitCount
    orig = pglast.parse_sql(sql)[0].stmt
    assert _parts(q) == (RawStream()(orig.fromClause[0]), RawStream()(orig.whereClause))
    assert _parts(q)[1] == "o.amount > 5"
    assert "count(DISTINCT o.order_id)" in RawStream()(stmt).lower().replace("count(distinct", "count(DISTINCT")


def test_fanout_probe_without_where_has_none():
    assert _parts(ac.fanout_probe(FANOUT_SQL)[0].count_sql_for("order_id"))[1] is None


def test_fanout_probe_web_traffic_case():
    sql = (
        "SELECT wt.device, AVG(o.amount) FROM live.orders o JOIN live.web_traffic wt "
        "ON wt.channel = o.channel AND o.placed_at >= wt.bucket_start GROUP BY wt.device"
    )
    probes = ac.fanout_probe(sql)
    assert [(p.table, p.alias) for p in probes] == [("live.orders", "o")]


def test_fanout_probe_default_schema_and_multiple_tables():
    sql = "SELECT SUM(o.a), AVG(c.b) FROM orders o JOIN customers c ON c.id = o.cid"
    probes = ac.fanout_probe(sql, "live")
    assert [p.table for p in probes] == ["live.orders", "live.customers"]


# -- COUNT (2026-10-02) ----------------------------------------------------------
# COUNT(col) counts one table's rows like SUM; COUNT(*) counts joined rows and is flagged only
# when every joined table repeats (otherwise it counts the unique table's rows, maybe on purpose).

STAR_SQL = ("SELECT c.state, COUNT(*) FROM live.orders o JOIN live.customers c "
            "ON c.customer_id = o.customer_id GROUP BY c.state")


def test_count_star_gets_one_star_probe_per_joined_table():
    probes = ac.fanout_probe(STAR_SQL)
    assert [(p.table, p.alias, p.star, p.label) for p in probes] == [
        ("live.orders", "o", True, "COUNT(*)"), ("live.customers", "c", True, "COUNT(*)")]


def test_count_of_a_column_is_probed_like_sum():
    sql = "SELECT c.state, COUNT(o.order_id), SUM(o.amount) FROM live.orders o JOIN live.customers c ON c.customer_id = o.customer_id GROUP BY c.state"
    probes = ac.fanout_probe(sql)
    assert [(p.alias, p.label, p.star) for p in probes] == [("o", "SUM/AVG/COUNT", False)]


def _counts(per_alias: dict[str, tuple[int, int]], keyless: set[str] = frozenset()):
    async def fake(session, sql, max_rows=None):
        if "AS is_unique" in sql:
            return {"ok": True, "rows": [{"is_unique": not any(t in sql for t in keyless)}]}
        alias = sql.split("COUNT(")[1].split(".")[0]
        n, d = per_alias[alias]
        return {"ok": True, "rows": [{"n": n, "d": d}]}
    return fake


COLS = {"live.orders": ["order_id"], "live.customers": ["customer_id"]}


def _check(sql, fake):
    with patch.object(ac, "data_run_select", fake):
        ac.clear_cache()
        try:
            return asyncio.run(ac.check_fanout(None, sql, "fp", COLS))
        finally:
            ac.clear_cache()


def test_count_star_is_fine_when_one_table_is_unique_in_the_join():
    assert _check(STAR_SQL, _counts({"o": (1000, 1000), "c": (1000, 400)})) is None


def test_count_star_is_flagged_when_every_table_repeats():
    found = _check(STAR_SQL, _counts({"o": (1500, 1000), "c": (1500, 400)}))
    assert found is not None and found.message.startswith("COUNT(*) over this join counts 1,500 combinations")
    assert "live.customers: 400 distinct customer_id" in found.message


def test_count_star_is_not_flagged_without_a_key_for_every_table():
    assert _check(STAR_SQL, _counts({"o": (1500, 1000), "c": (1500, 400)}, keyless={"customers"})) is None


def test_count_of_a_repeated_table_says_count():
    sql = "SELECT c.state, COUNT(o.order_id) FROM live.orders o JOIN live.customers c ON c.customer_id = o.customer_id GROUP BY c.state"
    found = _check(sql, _counts({"o": (1204, 1000)}))
    assert found.message.endswith("so COUNT over its columns count some values more than once.")


# -- find_single_column_key ---------------------------------------------------

def _fake_select(unique_columns: set[str], log: list):
    async def fake(session, sql, max_rows=None):
        log.append(sql)
        return {"ok": True, "rows": [{"is_unique": any(f"DISTINCT {c})" in sql for c in unique_columns)}]}
    return fake


def test_find_key_picks_first_unique_candidate_and_caches():
    ac.clear_cache()
    log: list = []
    with patch.object(ac, "data_run_select", _fake_select({"order_id"}, log)):
        async def go():
            first = await ac.find_single_column_key(None, "live.orders", ["amount", "customer_id", "order_id"], "fp")
            again = await ac.find_single_column_key(None, "live.orders", ["amount", "customer_id", "order_id"], "fp")
            return first, again
        first, again = asyncio.run(go())
    assert first == again == "order_id"
    assert len(log) == 2          # customer_id (not unique), order_id; "amount" is never probed
    assert "amount" not in " ".join(log)
    ac.clear_cache()


def test_find_key_none_when_nothing_unique_and_cached_per_fingerprint():
    ac.clear_cache()
    log: list = []
    with patch.object(ac, "data_run_select", _fake_select(set(), log)):
        async def go():
            a = await ac.find_single_column_key(None, "live.orders", ["customer_id"], "fp1")
            b = await ac.find_single_column_key(None, "live.orders", ["customer_id"], "fp1")
            c = await ac.find_single_column_key(None, "live.orders", ["customer_id"], "fp2")
            return a, b, c
        assert asyncio.run(go()) == (None, None, None)
    assert len(log) == 2
    ac.clear_cache()


def test_find_key_swallows_errors():
    ac.clear_cache()

    async def boom(*a, **k):
        raise RuntimeError("transport")
    with patch.object(ac, "data_run_select", boom):
        assert asyncio.run(ac.find_single_column_key(None, "live.orders", ["order_id"], "fp")) is None
    ac.clear_cache()


# -- caveat helpers -----------------------------------------------------------

def test_truncation_caveat():
    assert ac.truncation_caveat(1000, 1000) == "Showing the first 1,000 rows; the full result is larger."
    assert ac.truncation_caveat(999, 1000) is None


def test_null_aggregate_caveat():
    assert ac.null_aggregate_caveat([{"total": None}]) == "No matching data: the result is empty."
    assert ac.null_aggregate_caveat([{"a": None, "b": None}, {"a": None, "b": None}]) is not None
    assert ac.null_aggregate_caveat([{"total": 0}]) is None
    assert ac.null_aggregate_caveat([{"a": None}, {"a": 3}]) is None
    assert ac.null_aggregate_caveat([]) is None


# -- sql_agent_node -----------------------------------------------------------

DESCS = [{"table": "orders", "columns": [
    {"name": "order_id", "type": "integer"}, {"name": "customer_id", "type": "integer"},
    {"name": "amount", "type": "numeric"}]}]


class _FakeSession:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeLLM:
    def __init__(self, sql):
        self._sql = sql

    async def complete(self, system, messages, max_tokens=None):
        return SimpleNamespace(text=self._sql, tokens_in=1, tokens_out=1)


class _FakeRetriever:
    async def retrieve(self, session, table_names, schema=None, **_context):
        return "TABLE orders", DESCS


def _run(state: dict, *, n: int, d: int, rows=None, ws_exact: str | None = None):
    """Runs the node; returns (update, main_sqls, check_sqls)."""
    main: list[str] = []
    checks: list[str] = []
    result_rows = rows if rows is not None else [{"state": "MH", "sum": 10}]

    async def fake_select(session, sql, max_rows=None):
        if "AS is_unique" in sql:
            checks.append(sql)
            return {"ok": True, "rows": [{"is_unique": True}]}
        if "AS n," in sql:
            checks.append(sql)
            return {"ok": True, "rows": [{"n": n, "d": d}]}
        main.append(sql)
        return {"ok": True, "rows": result_rows, "columns": list(result_rows[0])}

    async def fake_tables(session, schema):
        return {"tables": [{"name": "orders"}], "fingerprint": "fp"}

    async def fake_sample(session, table, columns=None, limit=None, schema=None):
        return {"rows": []}

    async def fake_ws(session, **kw):
        return {"ok": True, "exact": {"sql": ws_exact}} if ws_exact else {"ok": True}

    with patch.object(sa, "MCPSession", _FakeSession), \
         patch.object(sa, "schema_list_tables", fake_tables), \
         patch.object(sa, "workspaces_manage", fake_ws), \
         patch.object(sa, "get_schema_retriever", lambda: _FakeRetriever()), \
         patch.object(sa, "get_agent_llm", lambda role: _FakeLLM(FANOUT_SQL)), \
         patch.object(sa, "data_run_select", fake_select), \
         patch.object(sa, "data_sample_rows", fake_sample), \
         patch.object(ac, "data_run_select", fake_select), \
         patch.object(sa, "get_spatial_resolver", lambda: SimpleNamespace(resolve=lambda q: None)):
        ac.clear_cache()
        try:
            update = asyncio.run(sa.sql_agent_node(state))
        finally:
            ac.clear_cache()
    return update, main, checks


def _state(flag: str | None, **extra) -> dict:
    flags = {"memory.enabled": False}
    if flag is not None:
        flags["pipeline.answer_checks"] = flag
    return {"tenant_id": "t", "query_id": "q", "session_id": "s", "raw_query": "revenue by state",
            "flags_snapshot": flags, "target_schema": "live", **extra}


def _fanout_error() -> dict:
    return {"attempt": 0, "category": "join_cardinality", "engine_code": None, "message": "m",
            "offending_sql": FANOUT_SQL, "remediation_hint": "h"}


def test_flag_on_fanout_first_attempt_is_a_retryable_failure():
    update, main, checks = _run(_state("on"), n=1204, d=1000)
    assert "executed_sql" not in update
    assert update["sql_rows"] == [] and update["sql_columns"] == []
    assert "caveats" not in update
    err = update["errors"][0]
    assert err["category"] == "join_cardinality" and err["engine_code"] is None
    assert err["attempt"] == 0 and err["offending_sql"] == FANOUT_SQL
    assert err["message"] == (
        "Joining repeats rows of live.orders (1,204 joined rows for 1,000 distinct order_id), "
        "so SUM/AVG over its columns count some values more than once."
    )
    assert "each row of live.orders counts once" in err["remediation_hint"]
    assert len(checks) == 2   # key uniqueness + count


def test_flag_on_fanout_after_retry_budget_succeeds_with_caveat():
    update, _, _ = _run(_state("on", errors=[_fanout_error()]), n=1204, d=1000)
    assert update["executed_sql"] == FANOUT_SQL
    assert update["sql_rows"]
    assert "errors" not in update
    assert update["caveats"] == ["These figures may count some rows more than once (join fan-out)."]


def test_flag_on_no_fanout_no_caveat():
    update, _, checks = _run(_state("on"), n=1000, d=1000)
    assert update["executed_sql"] == FANOUT_SQL
    assert "errors" not in update and "caveats" not in update
    assert len(checks) == 2


def test_flag_on_null_and_truncation_caveats_reach_update():
    update, _, _ = _run(_state("on"), n=1000, d=1000, rows=[{"total": None}])
    assert update["caveats"] == ["No matching data: the result is empty."]
    limit = sa.cfg.mcp.max_rows_default
    update, _, _ = _run(_state("on"), n=1000, d=1000, rows=[{"total": 1}] * limit)
    assert update["caveats"] == [f"Showing the first {limit:,} rows; the full result is larger."]


@pytest.mark.parametrize("flag", ["off", None])
def test_flag_off_issues_no_check_queries(flag):
    update, main, checks = _run(_state(flag), n=1204, d=1000, rows=[{"total": None}])
    assert checks == []
    assert len(main) == 1
    assert update["executed_sql"] == FANOUT_SQL
    assert "caveats" not in update and "errors" not in update


def test_fast_path_is_not_checked():
    state = _state("on", workspace_id="w", principal_id="p")
    update, main, checks = _run(state, n=1204, d=1000, rows=[{"total": None}], ws_exact=FANOUT_SQL)
    assert update["verified_example"] is True
    assert checks == []
    assert update["executed_sql"] == FANOUT_SQL
    assert "caveats" not in update and "errors" not in update


def test_debug_candidate_sql_is_not_checked():
    update, _, checks = _run(_state("on", debug_candidate_sql=FANOUT_SQL), n=1204, d=1000)
    assert checks == []
    assert update["executed_sql"] == FANOUT_SQL


def test_count_query_ignores_outer_join_null_rows():
    """A LEFT JOIN pads missing rows with NULLs; counting the key (not *) keeps them out."""
    probes = ac.fanout_probe(
        "SELECT c.state, SUM(o.amount) FROM live.customers c LEFT JOIN live.orders o "
        "ON o.customer_id = c.customer_id GROUP BY c.state"
    )
    assert probes and "COUNT(*)" not in probes[0].count_sql
    assert f"COUNT(o.{ac.KEY_PLACEHOLDER})" in probes[0].count_sql.replace('"', "")
