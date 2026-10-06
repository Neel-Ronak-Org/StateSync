"""
tests/test_t2b_confirm_not_answerable.py
Pins T2b (docs/specs/partd_tasks/T2b_confirm_not_answerable.md) and T2c (T2c_always_confirm.md): with
[pipeline] sql_no_data = "on", the first NOT_ANSWERABLE reply of a question is never final.

WHY THIS EXISTS. Gate G1 found 2 of 150 answerable BIRD questions answered NOT_ANSWERABLE ("missing
edhrecRank column") from the partial schema table selection showed. NOT_ANSWERABLE was terminal, so the
correction loop that would have shown the full table never ran. After a partial view the reply is now the
retriable not_answerable_unconfirmed; the next attempt is shown every table (cut to
[sql] full_schema_max_chars) with a fixed hint, and a second NOT_ANSWERABLE is final. With the flag off
nothing moves (tests/test_t2_sql_outcomes.py pins that).

T2c: G1 failed again because the retriever there shows every table, so the partial-view condition never held
(0 of 150 confirmations). The first NOT_ANSWERABLE is now always unconfirmed, whatever the view, and the
confirming attempt also lists the columns whose names resemble words in the question or the reply (the
Postgres copy lowercased every identifier: `edhrecRank` is `cards.edhrecrank`).
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
HINT = ("You replied NOT_ANSWERABLE. Every table is described below, followed by the columns whose names "
        "resemble words in the question or your reply: write the SQL if any column holds what the question asks, "
        "even under a different name or letter case; reply NOT_ANSWERABLE again only if none does.")
REPLY = "NOT_ANSWERABLE: no edhrecRank column in the tables"


def _desc(table: str, *columns: str) -> dict:
    return {"table": table, "columns": [{"name": c, "type": "text", "nullable": True} for c in columns],
            "foreign_keys": []}


DESCS = {"orders": _desc("orders", "status"), "cards": _desc("cards", "name", "edhrecRank"),
         "sets": _desc("sets", "code")}
NEAR_HEADER = "Columns whose names resemble words in the question or your reply:"


class _FakeSession:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _CfgWithCap:
    """cfg with [sql] full_schema_max_chars replaced (cfg itself is read-only)."""

    def __init__(self, cap: int, near_match_max: int | None = None):
        self._cap = cap
        self._near_max = near_match_max

    def __getattr__(self, name):
        real = getattr(config_loader.cfg, name)
        if name != "sql":
            return real
        cap = self._cap
        near = int(real.near_match_max) if self._near_max is None else self._near_max
        return SimpleNamespace(**{k: getattr(real, k) for k in ("max_tokens_sql", "retry_max")},
                               full_schema_max_chars=cap, near_match_max=near)


def _run(state: dict, replies: list[str], *, shown: list[str], dataset: list[str], cap: int | None = None,
         near_match_max: int | None = None, descs: dict | None = None):
    """Run the SQL node once. The retriever returns the `shown` tables; the dataset has the `dataset` ones.
    `descs` replaces DESCS (a dataset with other tables). Returns (update, user messages sent to the model,
    tables schema_describe_table was asked for)."""
    catalogue = descs or DESCS
    sent: list[str] = []
    described: list[str] = []
    queue = list(replies)

    class _Recorder:
        async def complete(self, system, messages, max_tokens=None):
            sent.append(messages[0]["content"])
            return SimpleNamespace(text=queue.pop(0) if len(queue) > 1 else queue[0], tokens_in=1, tokens_out=1)

    class _Retriever:
        async def retrieve(self, session, table_names, schema=None, **_context):
            return "TABLE shown", [catalogue[t] for t in shown]

    async def fake_list_tables(session, schema):
        return {"tables": [{"name": t} for t in dataset], "fingerprint": "fp"}

    async def fake_describe(session, table, schema=None):
        described.append(table)
        return catalogue[table]

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
    if cap is not None or near_match_max is not None:
        patches.append(patch.object(sa, "cfg", _CfgWithCap(cap or int(config_loader.cfg.sql.full_schema_max_chars),
                                                           near_match_max)))
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


def test_the_near_match_cap_ships_at_20():
    assert int(config_loader.cfg.sql.near_match_max) == 20


def test_record_by_history():
    first = sql_outcomes.not_answerable_record(0, REPLY, [])
    assert first["category"] == UNCONFIRMED and first["remediation_hint"] == HINT
    assert first["engine_code"] is None and first["offending_sql"] is None and first["message"] == REPLY
    # asked once already: final
    again = sql_outcomes.not_answerable_record(1, REPLY, [_unconfirmed()])
    assert again == sql_outcomes.not_answerable_error(1, REPLY) and again["category"] == "no_data_source"
    assert sql_outcomes.not_answerable_record(2, REPLY, [{"category": "syntax"}, _unconfirmed()])["category"] \
        == "no_data_source"


def test_view_is_partial_is_gone():
    assert not hasattr(sql_outcomes, "view_is_partial")        # T2c: the view no longer decides


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
def test_first_reply_after_a_partial_view_is_a_retriable_unconfirmed_outcome():
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


def test_first_reply_after_a_full_view_is_unconfirmed_too():
    # T2c: this inverts T2b's test_full_view_is_the_final_no_data_source (a full view used to be final).
    update, _, _ = _run(_state(ON), [REPLY], shown=["orders", "cards", "sets"], dataset=["orders", "cards", "sets"])
    [err] = update["errors"]
    assert err["category"] == UNCONFIRMED and err["remediation_hint"] == HINT
    assert err["category"] not in TERMINAL_CATEGORIES
    graph = pytest.importorskip("graph")
    state = StateSyncState(query_id="q", tenant_id="t", principal_id="p", session_id="s", target_schema="public",
                           raw_query="x", route="sql")
    state = merge(state, {"attempt": 0, "errors": [err]}, node_name="sql_agent")
    assert graph._route_after_sql(state) == "retry"


def test_second_reply_after_a_full_view_is_final():
    update, _, _ = _run(_state(ON, errors=[_unconfirmed()]), [REPLY],
                        shown=["orders", "cards", "sets"], dataset=["orders", "cards", "sets"])
    [err] = update["errors"]
    assert err["category"] == "no_data_source" and err["remediation_hint"] == category_hint("no_data_source")
    assert err["category"] in TERMINAL_CATEGORIES


def test_memory_hit_shows_no_table_and_the_first_reply_is_unconfirmed(monkeypatch):
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
    # the question names edhrecRank, so the near-match block lists cards.edhrecRank, after the view
    assert message.index("TABLE sets:") < message.index(NEAR_HEADER + "\ncards.edhrecRank")
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


# ── T2c: the near-match columns ─────────────────────────────────────────────
def _tables(**cols: list[str]) -> list[dict]:
    return [_desc(t, *c) for t, c in cols.items()]


def test_near_match_edhrecrank_in_the_question_lists_the_lowercased_column():
    descs = _tables(cards=["name", "edhrecrank"], sets=["code"])
    block = sql_outcomes.near_match_block("What is the edhrecRank of the card?", REPLY, descs, 20)
    assert block == NEAR_HEADER + "\ncards.edhrecrank"


def test_near_match_underscores_and_case_are_normalised():
    descs = _tables(trans=["client_id", "amount"], loan=["ClientID", "date"])
    assert sql_outcomes.near_match_columns("list each clientid", "", descs, 20) == ["trans.client_id", "loan.ClientID"]
    assert sql_outcomes.near_match_columns("client_id please", "", descs, 20) == ["trans.client_id", "loan.ClientID"]


def test_near_match_a_four_character_word_matches_only_by_equality():
    descs = _tables(t=["name", "username", "names", "nam"])
    assert sql_outcomes.near_match_columns("name", "", descs, 20) == ["t.name"]       # not username, not names
    # containment needs both sides at 5 characters or more: "name" and "nam" are too short to match "names"
    assert sql_outcomes.near_match_columns("names", "", descs, 20) == ["t.names"]


def test_near_match_five_characters_match_by_containment_both_ways():
    descs = _tables(t=["frequency", "statement_freq", "account_id", "id"])
    # word inside the column name
    assert sql_outcomes.near_match_columns("frequ", "", descs, 20) == ["t.frequency"]
    assert sql_outcomes.near_match_columns("freq?", "", descs, 20) == []                # 4 characters: equality only
    # column name inside the word
    assert sql_outcomes.near_match_columns("accountidentifier", "", descs, 20) == ["t.account_id"]   # not "id"


def test_near_match_short_columns_cannot_fill_the_list():
    descs = _tables(a=["id", "name", "code"], b=["id", "name"], c=["edhrecrank"])
    assert sql_outcomes.near_match_columns("Which identifier and codename has edhrecRank 1?", "", descs, 20) \
        == ["c.edhrecrank"]


def test_near_match_words_come_from_the_reply_without_its_marker():
    descs = _tables(account=["frequency", "notanswerable"])
    assert sql_outcomes.near_match_columns("how many?", "NOT_ANSWERABLE: no frequency here", descs, 20) \
        == ["account.frequency"]
    # the marker itself is not a word of the reply
    assert sql_outcomes.near_match_columns("how many?", "NOT_ANSWERABLE: no data", descs, 20) == []


def test_near_match_lists_each_column_once_in_table_then_column_order_and_respects_the_cap():
    descs = _tables(b=["alpha_x", "alphax"], a=["alphax"])
    assert sql_outcomes.near_match_columns("alphax alpha_x ALPHAX", "", descs, 20) \
        == ["b.alpha_x", "b.alphax", "a.alphax"]
    assert sql_outcomes.near_match_columns("alphax", "", descs, 2) == ["b.alpha_x", "b.alphax"]
    assert sql_outcomes.near_match_columns("alphax", "", descs, 0) == []


def test_near_match_no_block_when_nothing_matches():
    descs = _tables(cards=["name"], sets=["code"])
    assert sql_outcomes.near_match_block("how many foo", "NOT_ANSWERABLE: nothing", descs, 20) == ""
    assert sql_outcomes.near_match_block("", "", descs, 20) == ""


def test_near_match_node_lists_a_column_of_a_table_the_cap_cut_from_the_view():
    from schema_retriever import format_table_block
    descs = {**DESCS, "sets": _desc("sets", "code", "zorbleFlux")}
    blocks = [format_table_block(t, descs[t]) for t in ("orders", "cards", "sets")]
    cap = len(blocks[0]) + len("\n\n") + len(blocks[1])
    prior = sql_outcomes.not_answerable_error(0, "NOT_ANSWERABLE: nothing", UNCONFIRMED)
    _, messages, _ = _run(_state(ON, errors=[prior], raw_query="the zorbleflux of a set?"), ["SELECT 1"],
                          shown=["orders"], dataset=["orders", "cards", "sets"], cap=cap, descs=descs)
    [message] = messages
    assert "TABLE sets:" not in message and "cut at" in message          # sets is not in the view ...
    assert NEAR_HEADER + "\nsets.zorbleFlux" in message                 # ... but its column is listed


def test_near_match_node_cuts_the_list_at_near_match_max():
    descs = {**DESCS, "cards": _desc("cards", "rank_a", "rank_b", "rank_c")}
    _, messages, _ = _run(_state(ON, errors=[_unconfirmed()], raw_query="rank? ranka rankb rankc"), ["SELECT 1"],
                          shown=["orders"], dataset=["orders", "cards", "sets"], near_match_max=2, descs=descs)
    assert NEAR_HEADER + "\ncards.rank_a\ncards.rank_b" in messages[0] and "cards.rank_c\n" not in messages[0]
    assert not messages[0].rstrip().endswith("cards.rank_c")


def test_near_match_node_leaves_the_block_out_when_nothing_matches():
    prior = sql_outcomes.not_answerable_error(0, "NOT_ANSWERABLE: nothing", UNCONFIRMED)
    _, messages, _ = _run(_state(ON, errors=[prior], raw_query="which one is best?"), ["SELECT 1"],
                          shown=["orders"], dataset=["orders", "cards", "sets"])
    assert NEAR_HEADER not in messages[0] and HINT in messages[0]


def test_near_match_node_uses_the_recorded_reply_as_words():
    prior = sql_outcomes.not_answerable_error(0, "NOT_ANSWERABLE: no code column", UNCONFIRMED)
    _, messages, _ = _run(_state(ON, errors=[prior], raw_query="which one is best?"), ["SELECT 1"],
                          shown=["orders"], dataset=["orders", "cards", "sets"])
    assert NEAR_HEADER + "\nsets.code" in messages[0]


def test_near_match_block_is_only_on_the_confirming_attempt_and_only_with_the_flag():
    for flags, prior in (({}, _unconfirmed()), (ON, {**_unconfirmed(), "category": "unknown_column"})):
        _, messages, _ = _run(_state(flags, errors=[prior]), ["SELECT 1"],
                              shown=["orders"], dataset=["orders", "cards", "sets"])
        assert NEAR_HEADER not in messages[0]
