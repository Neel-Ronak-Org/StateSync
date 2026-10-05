"""
tests/test_t5_workspace_documents_limit.py
Pins the optional `limit` of library.manage's workspace_documents (T5, docs/specs/partd_tasks/T5_hybrid_split.md).

WHY THIS EXISTS. The router sees the router_titles_max newest titles of a workspace. The hybrid documents half
must match entities against ALL of the workspace's titles, so it passes `limit`. Without it the answer is what it
always was (the cap), so an older caller is unaffected; a limit is bounded by the account's document limit, and
a malformed one is refused before any database access.
"""

from __future__ import annotations

import asyncio
import os
import sys
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

pytest.importorskip("psycopg")
pytest.importorskip("httpx")
import tools.library_tools as lt  # noqa: E402
from config_loader import cfg  # noqa: E402
from session import set_session  # noqa: E402


def _list(monkeypatch, **params):
    """library.manage workspace_documents against a fake connection; returns (result, the LIMIT it sent)."""
    sent: list[tuple] = []

    class _Result:
        async def fetchall(self):
            return [("d1", "Title one")]

    class _Conn:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, sql, args):
            sent.append(args)
            return _Result()

    async def owns(*args):
        return True

    monkeypatch.setattr(lt, "get_history_connection", lambda: _Conn())
    monkeypatch.setattr(lt, "_owns_workspace", owns)

    async def run():
        set_session(tenant_id="t-test")
        return await lt.manage("workspace_documents", "p1", workspace_id="w1", **params)
    result = asyncio.run(run())
    return result, (sent[0][-1] if sent else None)


def test_without_a_limit_the_router_cap_applies_as_before(monkeypatch):
    result, limit = _list(monkeypatch)
    assert result == {"ok": True, "documents": [{"document_id": "d1", "title": "Title one"}]}
    assert limit == int(cfg.library.router_titles_max)


def test_a_limit_replaces_the_router_cap_up_to_the_account_document_limit(monkeypatch):
    assert _list(monkeypatch, limit=100)[1] == 100  # noqa: literals - any limit above the router cap
    assert _list(monkeypatch, limit=int(cfg.library.max_documents))[1] == int(cfg.library.max_documents)
    assert _list(monkeypatch, limit=int(cfg.library.max_documents) * 10)[1] == int(cfg.library.max_documents)
    assert int(cfg.library.max_documents) > int(cfg.library.router_titles_max)


@pytest.mark.parametrize("bad", [0, -1, "5", 2.5, True, [3]])
def test_a_malformed_limit_is_refused_before_the_database(monkeypatch, bad):
    result, sent = _list(monkeypatch, limit=bad)
    assert result["ok"] is False and result["error"] == "invalid" and result["message"] and sent is None
