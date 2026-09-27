"""
tests/test_history_turns.py
Pins how conversation turns are numbered and who may write into a session.

WHY THIS EXISTS (docs/V3_PLAN.md 0.1 and 0.2).

1. History silently stopped after the 4th question. The orchestrator numbered
   a turn len(recent_turns) + 1, but recent turns are a 3-turn window, so from
   the 5th question on it re-sent 4; the insert hit the unique key and
   ON CONFLICT DO NOTHING dropped the turn with no error. Confirmed end to end
   on 2026-09-26: 6 questions stored 4 rows. Every benchmark conversation is 3
   turns long, which is why nothing measured it.
2. A second principal could write into someone else's session. session_id is
   client-generated; the unique key ignores principal_id; so the intruder's
   turns were dropped (and could have displaced the owner's). Confirmed the same
   day: the second principal's question was answered and never stored.

The fix moves numbering into history.append_turn (MAX + 1 in the same
statement) and refuses writes into a session another principal owns. These
tests drive the MCP functions against a scripted fake connection; the SQL
itself was verified against the live database when the fix landed.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MCP_DIR = REPO_ROOT / "mcp"
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))
sys.path.insert(0, str(MCP_DIR))

# mcp/db.py imports psycopg_pool, present in the container but not necessarily
# on a host or CI runner. No test here opens a real connection.
if "psycopg_pool" not in sys.modules:
    try:
        import psycopg_pool  # noqa: F401
    except ImportError:
        import unittest.mock
        sys.modules["psycopg_pool"] = unittest.mock.MagicMock()

pytest.importorskip("psycopg")
import tools.history_tools as ht  # noqa: E402

NOW = dt.datetime(2026, 9, 26, tzinfo=dt.timezone.utc)
OWNER = "p_owner"
OTHER = "p_other"


class _Cursor:
    def __init__(self, one=None, many=None, rowcount=0):
        self._one, self._many, self.rowcount = one, many or [], rowcount

    async def fetchone(self):
        return self._one

    async def fetchall(self):
        return self._many


class _Conn:
    """Answers each statement by its shape; records every INSERT into conversation_turns."""

    def __init__(self, insert_results, owner=None, recent_rows=None, schema=None):
        self.insert_results = list(insert_results)
        self.owner = owner
        self.schema = schema
        self.recent_rows = recent_rows or []
        self.turn_inserts: list[tuple[str, object]] = []

    async def execute(self, sql, params=None):
        if "INSERT INTO session_state.conversation_turns" in sql:
            self.turn_inserts.append((sql, params))
            return _Cursor(one=self.insert_results.pop(0))
        if "SELECT principal_id, target_schema FROM session_state.conversation_turns" in sql:
            return _Cursor(one=(self.owner, self.schema) if self.owner else None)
        if "FROM session_state.conversation_turns" in sql and "ORDER BY turn_index DESC" in sql:
            return _Cursor(many=self.recent_rows)
        return _Cursor()        # turn_rows insert / prune


def _use(conn, monkeypatch):
    class _Ctx:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(ht, "get_history_connection", lambda: _Ctx())
    monkeypatch.setattr(ht, "get_tenant_id", lambda: "t_test")


def _append(principal=OWNER, target_schema="olist"):
    return asyncio.run(ht.append_turn(
        session_id="s1", principal_id=principal, raw_query="q", rewritten_query="q",
        rewrite_applied=False, context_guessed=False, answer_text="a", target_schema=target_schema,
    ))


def test_caller_cannot_supply_a_turn_number():
    """The parameter that carried the wrong number is gone from the tool's signature."""
    import inspect
    assert "turn_index" not in inspect.signature(ht.append_turn).parameters


def test_turn_number_is_assigned_by_the_insert(monkeypatch):
    conn = _Conn(insert_results=[(101, NOW, 6)])
    _use(conn, monkeypatch)

    result = _append()

    assert result == {"written": True, "turn_id": 101, "computed_at": NOW.isoformat(), "turn_index": 6}
    sql, params = conn.turn_inserts[0]
    assert "COALESCE(MAX(t.turn_index), 0) + 1" in sql
    assert "HAVING COALESCE(BOOL_AND(t.principal_id = %(principal)s), TRUE)" in sql
    assert params["principal"] == OWNER and params["session"] == "s1"
    assert params["target_schema"] == "olist" and "%(target_schema)s::text" in sql


def test_write_into_another_principals_session_is_refused_not_dropped(monkeypatch):
    conn = _Conn(insert_results=[None], owner=OTHER)
    _use(conn, monkeypatch)

    result = _append(principal=OWNER)

    assert result["written"] is False
    assert result["error"] == "session_owned_by_another_principal"
    assert len(conn.turn_inserts) == 1          # refused at once, no retry loop


def test_concurrent_write_taking_the_same_number_is_retried(monkeypatch):
    conn = _Conn(insert_results=[None, (102, NOW, 7)], owner=OWNER)
    _use(conn, monkeypatch)

    result = _append()

    assert result["written"] is True and result["turn_index"] == 7
    assert len(conn.turn_inserts) == 2


def test_gives_up_loudly_after_the_configured_attempts(monkeypatch):
    attempts = ht.cfg.mcp.history_append_max_attempts
    conn = _Conn(insert_results=[None] * attempts, owner=OWNER)
    _use(conn, monkeypatch)

    result = _append()

    assert result["written"] is False and result["error"] == "turn_index_contention"
    assert len(conn.turn_inserts) == attempts


@pytest.mark.parametrize("owner, schema, expected", [
    (None, None, {"exists": False, "usable": True, "target_schema": None}),     # new id: first write claims it
    (OWNER, "olist", {"exists": True, "usable": True, "target_schema": "olist"}),
    (OWNER, None, {"exists": True, "usable": True, "target_schema": None}),     # pre-018 turns
    # someone else's conversation: refused, and its dataset is not disclosed
    (OTHER, "olist", {"exists": True, "usable": False, "target_schema": None}),
])
def test_check_session(monkeypatch, owner, schema, expected):
    _use(_Conn(insert_results=[], owner=owner, schema=schema), monkeypatch)

    result = asyncio.run(ht.check_session(session_id="s1", principal_id=OWNER))

    assert result == expected


def test_next_turn_number_comes_from_the_newest_row_not_the_window_size(monkeypatch):
    """Seven turns stored, a 3-turn window read: the next number is 8, not 4."""
    def row(n):
        return (n, "q", "q", False, False, "sql", None, "a", NOW, None, None, None, None, "olist")
    _use(_Conn(insert_results=[], recent_rows=[row(7), row(6), row(5)]), monkeypatch)

    result = asyncio.run(ht.get_recent_turns(session_id="s1", principal_id=OWNER, limit=3))

    assert result["next_turn_index"] == 8
    assert [t["turn_index"] for t in result["turns"]] == [5, 6, 7]
    assert all(t["target_schema"] == "olist" for t in result["turns"])


def test_empty_session_starts_at_one(monkeypatch):
    _use(_Conn(insert_results=[], recent_rows=[]), monkeypatch)

    result = asyncio.run(ht.get_recent_turns(session_id="s1", principal_id=OWNER, limit=3))

    assert result["next_turn_index"] == 1
