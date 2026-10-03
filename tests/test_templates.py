"""
tests/test_templates.py
Pins the compiled tier's orchestrator side (orchestrator/templates.py and the
template attempt in nodes/sql_agent.py, docs/V3_PLAN.md Phase 3c slice 3).

WHY THIS EXISTS: the values of a template come from a model, so filling must trust
nothing: each value is validated by its slot's type, text is always one quoted
literal (a quote or "; DROP TABLE" in a value cannot escape it), and the result
must parse as exactly one SELECT. In the node, a match must cost ONE decomposer call
and NO sql-role call; a miss or a flag "off" must leave the normal path unchanged.
MCP and the models are faked; no network.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))

pytest.importorskip("pglast")
import pglast  # noqa: E402

import templates as tpl  # noqa: E402
from config_loader import cfg  # noqa: E402
from nodes import sql_agent as sa  # noqa: E402

TEMPLATE = "SELECT count(*) FROM orders WHERE status = $1 AND created >= $2 LIMIT $3"
SLOTS = [
    {"name": "s1", "type": "text", "column": "status", "example": "delivered"},
    {"name": "s2", "type": "date", "column": "created", "example": "2018-01-01"},
    {"name": "s3", "type": "integer", "column": None, "example": "5"},
]
GOOD = {"s1": "shipped", "s2": "2019-06-01", "s3": 10}


def _one_select(sql: str) -> bool:
    stmts = pglast.parse_sql(sql)
    return len(stmts) == 1 and type(stmts[0].stmt).__name__ == "SelectStmt"


# -- fill_template ----------------------------------------------------------

def test_fill_renders_each_type():
    sql = tpl.fill_template(TEMPLATE, SLOTS, GOOD)
    assert sql == "SELECT count(*) FROM orders WHERE status = 'shipped' AND created >= '2019-06-01' LIMIT 10"
    assert _one_select(sql)


def test_number_and_timestamp_slots():
    slots = [{"name": "s1", "type": "number", "column": "n", "example": "2.5"},
             {"name": "s2", "type": "timestamp", "column": "ts", "example": "2018-01-01 10:00:00"}]
    sql = tpl.fill_template("SELECT a FROM t WHERE n > $1 AND ts < $2", slots,
                            {"s1": 3.5, "s2": "2019-06-01T08:30:00"})
    assert sql == "SELECT a FROM t WHERE n > 3.5 AND ts < '2019-06-01 08:30:00'"
    assert tpl.fill_template("SELECT a FROM t WHERE n > $1", slots[:1], {"s1": 4}) == "SELECT a FROM t WHERE n > 4"


@pytest.mark.parametrize("slot_type,bad", [
    ("integer", "5"), ("integer", 5.5), ("integer", True), ("integer", None),
    ("number", "1.5"), ("number", float("nan")), ("number", float("inf")), ("number", False),
    ("date", "2019-13-45"), ("date", "yesterday"), ("date", 20190101), ("date", None),
    ("timestamp", "not a time"), ("timestamp", 5),
    ("text", 5), ("text", None), ("text", "a\x00b"), ("text", "x" * 10_000),
])
def test_bad_values_are_refused(slot_type, bad):
    slots = [{"name": "s1", "type": slot_type, "column": "c", "example": "x"}]
    assert tpl.fill_template("SELECT a FROM t WHERE c = $1", slots, {"s1": bad}) is None


def test_text_at_the_length_limit_is_allowed():
    slots = [{"name": "s1", "type": "text", "column": "c", "example": "x"}]
    ok = "x" * int(cfg.templates.text_slot_max_chars)
    assert tpl.fill_template("SELECT a FROM t WHERE c = $1", slots, {"s1": ok}) is not None
    assert tpl.fill_template("SELECT a FROM t WHERE c = $1", slots, {"s1": ok + "x"}) is None


@pytest.mark.parametrize("attack", [
    "x'; DROP TABLE t; --",
    "'; DELETE FROM orders; SELECT '",
    "it's",
    "a''b",
    "back\\slash'",
    "$1 $2",
])
def test_text_injection_stays_one_quoted_literal(attack):
    slots = [{"name": "s1", "type": "text", "column": "c", "example": "x"},
             {"name": "s2", "type": "integer", "column": "n", "example": "1"}]
    sql = tpl.fill_template("SELECT a FROM t WHERE c = $1 AND n = $2", slots, {"s1": attack, "s2": 7})
    assert sql is not None and _one_select(sql)
    expected = "'" + attack.replace("'", "''") + "'"
    assert sql == f"SELECT a FROM t WHERE c = {expected} AND n = 7"


def test_limit_slot_is_bounded_by_max_rows():
    top = int(cfg.mcp.max_rows_default)
    assert tpl.fill_template(TEMPLATE, SLOTS, {**GOOD, "s3": top}) is not None
    for bad in (0, -1, top + 1):
        assert tpl.fill_template(TEMPLATE, SLOTS, {**GOOD, "s3": bad}) is None


def test_a_missing_value_or_bad_slot_list_is_refused():
    assert tpl.fill_template(TEMPLATE, SLOTS, {"s1": "a", "s2": "2019-01-01"}) is None
    assert tpl.fill_template(TEMPLATE, SLOTS, "nope") is None
    assert tpl.fill_template(TEMPLATE, SLOTS[:2], GOOD) is None  # $3 has no slot
    assert tpl.fill_template("SELECT 1", SLOTS[:1], GOOD) is None  # slot unused


def test_placeholders_are_replaced_by_full_index():
    slots = [{"name": f"s{i}", "type": "integer", "column": "c", "example": "1"} for i in range(1, 11)]
    template = "SELECT a FROM t WHERE " + " AND ".join(f"c{i} = ${i}" for i in range(1, 11))
    sql = tpl.fill_template(template, slots, {f"s{i}": i * 100 for i in range(1, 11)})
    assert "c1 = 100 " in sql and sql.endswith("c10 = 1000")


def test_a_non_select_result_is_refused():
    slots = [{"name": "s1", "type": "text", "column": "c", "example": "x"}]
    assert tpl.fill_template("DELETE FROM t WHERE c = $1", slots, {"s1": "a"}) is None
    assert tpl.fill_template("SELECT 1; SELECT $1", slots, {"s1": "a"}) is None


# -- parse_match ------------------------------------------------------------

CANDS = [{"template_id": "t1", "question": "q", "template_sql": TEMPLATE, "slots": SLOTS, "score": 0.5}]


def test_parse_match_valid_fenced_and_unknown():
    reply = json.dumps({"template_id": "t1", "values": {"s1": "a"}})
    assert tpl.parse_match(reply, CANDS) == (CANDS[0], {"s1": "a"})
    assert tpl.parse_match("```json\n" + reply + "\n```", CANDS) == (CANDS[0], {"s1": "a"})
    assert tpl.parse_match(json.dumps({"template_id": "zzz", "values": {}}), CANDS) is None


@pytest.mark.parametrize("reply", [
    '{"template_id": null, "values": {}}', "not json", "[]", '{"template_id": "t1"}',
    '{"template_id": "t1", "values": []}', '{"template_id": 5, "values": {}}', "",
])
def test_parse_match_null_and_junk(reply):
    assert tpl.parse_match(reply, CANDS) is None


# -- sql_agent_node ---------------------------------------------------------

class _FakeSession:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


DESCS = [{"table": "orders", "columns": [{"name": "status", "type": "text", "nullable": True, "comment": None}]}]


class _FakeRetriever:
    async def retrieve(self, session, table_names, schema=None, **_context):
        return "TABLE orders:\n    status text NULL", DESCS


def _run(flag: str | None, decomposer_reply: str, run_ok: bool = True, candidates=CANDS):
    calls: list[str] = []  # roles, in call order
    mcp_actions: list[str] = []
    ran: list[str] = []

    class _LLM:
        def __init__(self, role):
            self.role = role

        async def complete(self, system, messages, max_tokens=None):
            calls.append(self.role)
            text = decomposer_reply if self.role == "decomposer" else "SELECT status FROM orders"
            return SimpleNamespace(text=text, tokens_in=1, tokens_out=1)

    async def fake_manage(session, principal_id, action, **params):
        mcp_actions.append(action)
        if action == "templates":
            return {"ok": True, "templates": candidates}
        return {"ok": True, "target_schema": "public", "business_context": "", "descriptions": [],
                "definitions": [], "examples": [], "exact": None}

    async def fake_list_tables(session, schema):
        return {"tables": [{"name": "orders"}], "fingerprint": "fp"}

    async def fake_select(session, sql_text, max_rows=None):
        ran.append(sql_text)
        if not run_ok:
            return {"ok": False, "error": {"message": "boom", "sqlstate": "42601"}}
        return {"ok": True, "rows": [{"n": 1}], "columns": ["n"]}

    async def fake_sample(session, table, columns=None, limit=None, schema=None):
        return {"rows": []}

    flags = {"memory.enabled": False}
    if flag is not None:
        flags["pipeline.templates"] = flag
    state = {"tenant_id": "t", "query_id": "q", "session_id": "s", "principal_id": "p", "workspace_id": "w",
             "raw_query": "how many shipped orders since june 2019?", "flags_snapshot": flags,
             "target_schema": "public"}
    with patch.object(sa, "MCPSession", _FakeSession), \
         patch.object(sa, "schema_list_tables", fake_list_tables), \
         patch.object(sa, "workspaces_manage", fake_manage), \
         patch.object(sa, "get_schema_retriever", lambda: _FakeRetriever()), \
         patch.object(sa, "get_agent_llm", lambda role: _LLM(role)), \
         patch.object(sa, "data_run_select", fake_select), \
         patch.object(sa, "data_sample_rows", fake_sample), \
         patch.object(sa, "get_spatial_resolver", lambda: SimpleNamespace(resolve=lambda q: None)):
        update = asyncio.run(sa.sql_agent_node(state))
    return update, calls, mcp_actions, ran


MATCH = json.dumps({"template_id": "t1", "values": GOOD})
FILLED = "SELECT count(*) FROM orders WHERE status = 'shipped' AND created >= '2019-06-01' LIMIT 10"


def test_a_match_costs_one_decomposer_call_and_no_sql_call():
    update, calls, actions, ran = _run("on", MATCH)
    assert calls == ["decomposer"]
    assert "templates" in actions
    assert update["candidate_sql"] == FILLED and update["executed_sql"] == FILLED
    assert update["compiled_template"] is True and update["verified_example"] is False
    assert update["metrics"]["llm_calls"] == 1
    assert ran == [FILLED]  # executed once, through the normal gate


@pytest.mark.parametrize("reply", ['{"template_id": null, "values": {}}', "junk",
                                   json.dumps({"template_id": "t1", "values": {**GOOD, "s3": 10_000_000}})])
def test_no_usable_match_falls_back_to_the_normal_path(reply):
    update, calls, _actions, _ran = _run("on", reply)
    assert calls[0] == "decomposer" and calls.count("decomposer") == 1
    assert "sql" in calls
    assert update["compiled_template"] is False
    assert "errors" not in update  # the template try is not a failed attempt


def test_an_execution_error_falls_back_without_recording_it_for_the_template():
    update, calls, _a, ran = _run("on", MATCH, run_ok=False)
    assert calls.count("decomposer") == 1 and "sql" in calls
    assert update["compiled_template"] is False
    assert ran[0] == FILLED and ran[1] == "SELECT status FROM orders"
    assert all(e["offending_sql"] != FILLED for e in update["errors"])


def test_no_candidates_means_no_model_call_for_the_template():
    _u, calls, actions, _r = _run("on", MATCH, candidates=[])
    assert "decomposer" not in calls and "templates" in actions


@pytest.mark.parametrize("flag", ["off", None])
def test_flag_off_never_asks_for_templates(flag):
    update, calls, actions, _ran = _run(flag, MATCH)
    assert "templates" not in actions
    assert "decomposer" not in calls
    assert update["compiled_template"] is False
