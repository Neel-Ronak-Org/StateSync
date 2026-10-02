"""
tests/test_pin_example.py

WHY THIS EXISTS (docs/V3_PLAN.md 5.3: "pinned tiles become approved examples, so the chat and the
dashboard agree"; mcp/tools/tile_tools.py property 6). Until 2026-10-02 pinning saved a tile and
nothing else, so the chat could compute a pinned number differently next time. Pinned here:
- only a plain database answer (route "sql") becomes an example: a hybrid turn's SQL answers part
  of its question, and storing the whole question against it would make the fast path wrong;
- the example's question is the stand-alone form (a follow-up's rewrite), the text matched later;
- the workspace must use the dataset the answer came from;
- a failed example never undoes the pin, and a pin without a workspace (Build with AI) saves none.
The database path is exercised live by scripts/verify_tiles.py.
"""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))
sys.path.insert(0, str(REPO_ROOT / "mcp"))
if "psycopg_pool" not in sys.modules:
    try:
        import psycopg_pool  # noqa: F401
    except ImportError:
        import unittest.mock
        sys.modules["psycopg_pool"] = unittest.mock.MagicMock()

pytest.importorskip("pglast")
pytest.importorskip("psycopg")
import tools.tile_tools as tt  # noqa: E402
import tools.workspace_tools as wt  # noqa: E402


def test_only_plain_sql_answers_with_a_question_qualify():
    assert tt.example_skip_reason("Top 5 states by orders", "sql") is None
    assert "plain database answer" in tt.example_skip_reason("Revenue and what the contract says", "hybrid")
    assert tt.example_skip_reason("anything", None) is not None
    assert tt.example_skip_reason("  ", "sql") is not None


def test_a_follow_up_is_stored_in_its_stand_alone_form():
    assert tt.title_fallback("And bottom?", "Bottom 5 states by customers", True) == "Bottom 5 states by customers"
    assert tt.title_fallback("Top 5 states", "Top 5 states", False) == "Top 5 states"


def test_a_hybrid_pin_saves_no_example_and_never_calls_the_store(monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("must not be called")
    monkeypatch.setattr(wt, "add_pinned_example", boom)
    out = asyncio.run(tt._example_from_pin("t", "p", "w", "q", "SELECT 1", "public", "hybrid"))
    assert out["saved"] is False and "reason" in out


def test_a_failing_store_is_reported_not_raised(monkeypatch):
    async def broken(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(wt, "add_pinned_example", broken)
    out = asyncio.run(tt._example_from_pin("t", "p", "w", "q", "SELECT 1", "public", "sql"))
    assert out == {"saved": False, "reason": "The approved example could not be saved."}


def test_a_saved_example_is_tagged_tile_and_returned(monkeypatch):
    seen = {}

    async def fake_add(tenant_id, principal_id, workspace_id=None, question=None, sql=None, source="owner", **_):
        seen.update(question=question, sql=sql, source=source, workspace_id=workspace_id)
        return {"ok": True, "example_id": "ex1"}

    _patch_owned(monkeypatch, ("w1", "public", "", 0, "ready"))
    monkeypatch.setattr(wt, "_add_example", fake_add)
    out = asyncio.run(tt._example_from_pin("t", "p", "w1", "Top 5 states", "SELECT 1", "public", "sql"))
    assert out == {"saved": True, "example_id": "ex1"}
    assert seen == {"question": "Top 5 states", "sql": "SELECT 1", "source": "tile", "workspace_id": "w1"}


def test_the_workspace_must_use_the_answers_dataset(monkeypatch):
    _patch_owned(monkeypatch, ("w1", "olist", "", 0, "ready"))
    out = asyncio.run(wt.add_pinned_example("t", "p", "w1", "q", "SELECT 1", "public"))
    assert out["ok"] is False and out["error"] == "invalid"
    _patch_owned(monkeypatch, None)
    out = asyncio.run(wt.add_pinned_example("t", "p", "nope", "q", "SELECT 1", "public"))
    assert out["error"] == "not_found"


def _patch_owned(monkeypatch, row):
    @asynccontextmanager
    async def fake_conn():
        yield object()

    async def fake_owned(conn, tenant_id, principal_id, workspace_id):
        return row

    monkeypatch.setattr(wt, "get_history_connection", fake_conn)
    monkeypatch.setattr(wt, "_owned", fake_owned)
