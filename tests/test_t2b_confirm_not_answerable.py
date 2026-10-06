"""
tests/test_t2b_confirm_not_answerable.py
Pins T2b (docs/specs/partd_tasks/T2b_confirm_not_answerable.md): with [pipeline] sql_no_data = "on", a
NOT_ANSWERABLE reply is final only when the writer saw the whole schema.

WHY THIS EXISTS. Gate G1 found 2 of 150 answerable BIRD questions answered NOT_ANSWERABLE ("missing
edhrecRank column") from the partial schema table selection showed. NOT_ANSWERABLE was terminal, so the
correction loop that would have shown the full table never ran. After a partial view the reply is now the
retriable not_answerable_unconfirmed; the next attempt is shown every table (cut to
[sql] full_schema_max_chars) with a fixed hint, and a second NOT_ANSWERABLE is final. With the flag off
nothing moves (tests/test_t2_sql_outcomes.py pins that).
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
if "psycopg_pool" not in sys.modules:
    try:
        import psycopg_pool  # noqa: F401
    except ImportError:
        import unittest.mock
        sys.modules["psycopg_pool"] = unittest.mock.MagicMock()

import config_loader  # noqa: E402
import sql_outcomes  # noqa: E402
from classifiers.error_classifier import TERMINAL_CATEGORIES, category_hint, classify_error  # noqa: E402
from nodes import sql_agent as sa  # noqa: E402
from state import ErrorRecord, StateSyncState, merge  # noqa: E402

ON = {"pipeline.sql_no_data": "on"}
UNCONFIRMED = "not_answerable_unconfirmed"
HINT = ("You replied NOT_ANSWERABLE after seeing only part of the schema. Every table is described below: "
        "write the SQL if any table holds what the question asks; reply NOT_ANSWERABLE again only if none does.")
REPLY = "NOT_ANSWERABLE: no edhrecRank column in the tables"


def _desc(table: str, *columns: str) -> dict:
    return {"table": table, "columns": [{"name": c, "type": "text", "nullable": True} for c in columns],
            "foreign_keys": []}


DESCS = {"orders": _desc("orders", "status"), "cards": _desc("cards", "name", "edhrecRank"),
         "sets": _desc("sets", "code")}


class _FakeSession:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _CfgWithCap:
    """cfg with [sql] full_schema_max_chars replaced (cfg itself is read-only)."""

    def __init__(self, cap: int):
        self._cap = cap

    def __getattr__(self, name):
        real = getattr(config_loader.cfg, name)
        if name != "sql":
            return real
        cap = self._cap
        return SimpleNamespace(**{k: getattr(real, k) for k in ("max_tokens_sql", "retry_max")},
                               full_schema_max_chars=cap)


def _run(state: dict, replies: list[str], *, shown: list[str], dataset: list[str], cap: int | None = None):
    """Run the SQL node once. The retriever returns the `shown` tables; the dataset has the `dataset` ones.
    Returns (update, user messages sent to the model, tables schema_describe_table was asked for)."""
    sent: list[str] = []
    described: list[str] = []
    queue = list(replies)

    class _Recorder:
        async def complete(self, system, messages, max_tokens=None):
            sent.append(messages[0]["content"])
            return SimpleNamespace(text=queue.pop(0) if len(queue) > 1 else queue[0], tokens_in=1, tokens_out=1)

    class _Retriever:
        async def retrieve(self, session, table_names, schema=None, **_context):
            return "TABLE shown", [DESCS[t] for t in shown]

    async def fake_list_tables(session, schema):
        return {"tables": [{"name": t} for t in dataset], "fingerprint": "fp"}

    async def fake_describe(session, table, schema=None):
        described.append(table)
        return DESCS[table]

    async def fake_select(session, sql_text, max_rows=None):
        return {"ok": True, "rows": [{"status": "x"}], "columns": ["status"]}

    async def fake_sample(session, table, columns=None, limit=None, schema=None):
        return {"rows": [{"status": "shipped"}]}

    patches = [
        patch.object(sa, "data_sample_rows", fake_sample),
        patch.object(sa, "MCPSession", _FakeSession), patch.object(sa, "schema_list_tables", fake_list_tables),
        patch.object(sa, "schema_describe_table", fake_describe),
        patch.object(sa, "get_schema_retriever", lambda: _Retriever()),
        patch.object(sa, "get_agent_llm", lambda role: _Recorder()), patch.object(sa, "data_run_select", fake_select),
        patch.object(sa, "get_spatial_resolver", lambda: SimpleNamespace(resolve=lambda q: None)),
    ]
    if cap is not None:
        patches.append(patch.object(sa, "cfg", _CfgWithCap(cap)))
    for p in patches:
        p.start()
    try:
        update = asyncio.run(sa.sql_agent_node(state))
    finally:
        for p in reversed(patches):
            p.stop()
    return update, sent, described


def _state(flags: dict | None = None, **extra) -> dict:
    return {"tenant_id": "t", "query_id": "q", "session_id": "s", "raw_query": "which card has the best edhrecRank?",
            "flags_snapshot": {"memory.enabled": False, **(flags or {})}, "target_schema": "public", **extra}


def _unconfirmed(attempt: int = 0) -> dict:
    return sql_outcomes.not_answerable_error(attempt, REPLY, UNCONFIRMED)


# ── the taxonomy and the pure functions ─────────────────────────────────────
def test_the_new_category_is_in_the_overrides_with_the_exact_hint_and_is_not_terminal():
    overrides = json.loads((REPO_ROOT / "config" / "error_taxonomy_overrides.json").read_text())["rules"]
    [rule] = [r for r in overrides if r["category"] == UNCONFIRMED]
    assert rule["hint"] == HINT
    assert category_hint(UNCONFIRMED) == HINT
    assert UNCONFIRMED not in TERMINAL_CATEGORIES
    base = json.loads((REPO_ROOT / "config" / "error_taxonomy.json").read_text())["rules"]
    assert UNCONFIRMED not in {r["category"] for r in base}          # old images read that file live
    ErrorRecord(**_unconfirmed())                                    # the ErrorCategory literal has it
    # nothing an engine says can land on it
    assert classify_error(sqlstate=None, message="NOT_ANSWERABLE: x")[0] != UNCONFIRMED


def test_the_cap_ships_at_12000():
    assert int(config_loader.cfg.sql.full_schema_max_chars) == 12000


@pytest.mark.parametrize("shown, total, cut, partial", [
    (2, 3, False, True), (3, 3, False, False), (0, 3, False, True),      # 0 shown: a memory hit shows no table
    (3, 3, True, True), (0, 0, False, False),
])
def test_view_is_partial(shown, total, cut, partial):
    assert sql_outcomes.view_is_partial(shown, total, cut) is partial


def test_record_by_view_and_by_history():
    partial = sql_outcomes.not_answerable_record(0, REPLY, [], 1, 3)
    assert partial["category"] == UNCONFIRMED and partial["remediation_hint"] == HINT
    assert partial["engine_code"] is None and partial["offending_sql"] is None and partial["message"] == REPLY
    full = sql_outcomes.not_answerable_record(0, REPLY, [], 3, 3)
    assert full == sql_outcomes.not_answerable_error(0, REPLY) and full["category"] == "no_data_source"
    # asked once already: final, even if the view is still partial (the confirmation was cut or a table refused)
    again = sql_outcomes.not_answerable_record(1, REPLY, [_unconfirmed()], 1, 3)
    assert again["category"] == "no_data_source" and again["attempt"] == 1


def test_confirmation_pending_is_only_the_attempt_right_after_an_unconfirmed_one():
    assert sql_outcomes.confirmation_pending([_unconfirmed()])
    assert not sql_outcomes.confirmation_pending([])
    assert not sql_outcomes.confirmation_pending([_unconfirmed(), {"category": "syntax"}])
    assert not sql_outcomes.confirmation_pending([sql_outcomes.not_answerable_error(0, REPLY)])


def test_cut_schema_view_keeps_whole_tables_and_never_exceeds_the_cap():
    blocks = ["a" * 40, "b" * 40, "c" * 40]
    assert sql_outcomes.cut_schema_view(blocks, 1000) == ("\n\n".join(blocks), 3)
    text, n = sql_outcomes.cut_schema_view(blocks, 100)
    assert n == 2 and text == blocks[0] + "\n\n" + blocks[1] and len(text) <= 100
    text, n = sql_outcomes.cut_schema_view(blocks, 81)               # two tables need 82
    assert n == 1 and text == blocks[0]
    assert sql_outcomes.cut_schema_view(blocks, 10) == ("a" * 10, 1)    # one table longer than the cap
    assert sql_outcomes.cut_schema_view([], 10) == ("", 0)


# ── the SQL node ────────────────────────────────────────────────────────────
def test_partial_view_is_a_retriable_unconfirmed_outcome():
    update, _, _ = _run(_state(ON), [REPLY], shown=["orders"], dataset=["orders", "cards", "sets"])
    [err] = update["errors"]
    assert err["category"] == UNCONFIRMED and err["attempt"] == 0 and err["remediation_hint"] == HINT
    assert err["message"] == REPLY and err["engine_code"] is None and err["offending_sql"] is None
    assert update["candidate_sql"] is None and "executed_sql" not in update
    # retriable: the graph loops back to the SQL agent
    graph = pytest.importorskip("graph")
    state = StateSyncState(query_id="q", tenant_id="t", principal_id="p", session_id="s", target_schema="public",
                           raw_query="x", route="sql")
    state = merge(state, {"attempt": 0, "errors": [err]}, node_name="sql_agent")
    assert graph._route_after_sql(state) == "retry"


def test_full_view_is_the_final_no_data_source():
    update, _, _ = _run(_state(ON), [REPLY], shown=["orders", "cards", "sets"], dataset=["orders", "cards", "sets"])
    [err] = update["errors"]
    assert err["category"] == "no_data_source" and err["remediation_hint"] == category_hint("no_data_source")
    assert err["category"] in TERMINAL_CATEGORIES


def test_memory_hit_shows_no_table_so_the_view_is_partial(monkeypatch):
    # attempt 0 on a memory hit builds its context from the stored insight: no schema description at all.
    class _Store:
        async def get_trajectory(self, session, query_text, schema_fingerprint):
            return {"hit": True, "candidates": [{"trace_id": "t1", "insight": "use orders", "tables": ["orders"]}]}

    monkeypatch.setattr(sa, "get_trajectory_store", lambda: _Store())
    update, _, _ = _run(_state({**ON, "memory.enabled": True}), [REPLY], shown=[], dataset=["orders", "cards"])
    assert update["errors"][0]["category"] == UNCONFIRMED


def test_next_attempt_context_holds_every_table_and_the_fixed_hint():
    prior = _unconfirmed()
    update, messages, described = _run(_state(ON, errors=[prior], attempt=0), ["SELECT 1"],
                                       shown=["orders"], dataset=["orders", "cards", "sets"])
    [message] = messages
    assert described == ["cards", "sets"]                    # only the tables the selection left out
    for text in ("TABLE orders:", "TABLE cards:", "edhrecRank", "TABLE sets:"):
        assert text in message
    assert message.index("TABLE orders:") < message.index("TABLE cards:")       # the selected tables come first
    assert HINT in message and "cut at" not in message
    assert "errors" not in update and update["attempt"] == 1


def test_next_attempt_context_is_cut_at_the_cap():
    from schema_retriever import format_table_block
    blocks = [format_table_block(t, DESCS[t]) for t in ("orders", "cards", "sets")]
    cap = len(blocks[0]) + len("\n\n") + len(blocks[1])                 # room for exactly two tables
    update, messages, _ = _run(_state(ON, errors=[_unconfirmed()]), ["SELECT 1"], shown=["orders"],
                               dataset=["orders", "cards", "sets"], cap=cap)
    [message] = messages
    assert "TABLE cards:" in message and "TABLE sets:" not in message
    assert f"cut at {cap} characters: 2 of 3 tables" in message


def test_the_other_attempts_keep_their_context_without_the_flag_or_the_prior_record():
    # the same record with the flag off, or a different last error: the retriever's selection only
    for flags, prior in (({}, _unconfirmed()), (ON, {**_unconfirmed(), "category": "unknown_column"})):
        _, messages, described = _run(_state(flags, errors=[prior]), ["SELECT 1"],
                                      shown=["orders"], dataset=["orders", "cards", "sets"])
        assert described == [] and "TABLE cards:" not in messages[0] and "TABLE shown" in messages[0]


def test_second_not_answerable_is_final_and_the_trace_keeps_both_records():
    update, _, _ = _run(_state(ON, errors=[_unconfirmed()], attempt=0), [REPLY],
                        shown=["orders"], dataset=["orders", "cards", "sets"])
    [second] = update["errors"]
    assert second["category"] == "no_data_source" and second["attempt"] == 1
    assert second["category"] in TERMINAL_CATEGORIES
    state = StateSyncState(query_id="q", tenant_id="t", principal_id="p", session_id="s", target_schema="public",
                           raw_query="x", route="sql", errors=[ErrorRecord(**_unconfirmed())], attempt=0)
    state = merge(state, update, node_name="sql_agent")
    assert [e.category for e in state.errors] == [UNCONFIRMED, "no_data_source"]
    graph = pytest.importorskip("graph")
    assert graph._route_after_sql(state) == "terminal"


def test_confirmation_costs_one_attempt_of_the_retry_budget():
    graph = pytest.importorskip("graph")
    cap = int(config_loader.cfg.sql.retry_max)
    state = StateSyncState(query_id="q", tenant_id="t", principal_id="p", session_id="s", target_schema="public",
                           raw_query="x", route="sql",
                           errors=[ErrorRecord(**_unconfirmed(a)) for a in range(cap + 1)], attempt=cap)
    assert graph._route_after_sql(state) == "terminal"               # no attempt left: no loop past retry_max


def test_flag_off_never_produces_the_new_category():
    update, _, _ = _run(_state(), [REPLY], shown=["orders"], dataset=["orders", "cards", "sets"])
    assert "errors" not in update and update["candidate_sql"] == REPLY   # sent as SQL, as before
