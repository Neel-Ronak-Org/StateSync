"""
tests/test_exact_vector_search.py
Pins that document retrieval ranks by exact cosine distance, not by the
approximate HNSW index (docs/V3_PLAN.md 0.12).

WHY THIS EXISTS. docs/limitations.md already recorded (cycle 006) that the
HNSW index was "silently approximate the whole time." On 2026-09-26, after
the index was rebuilt, it failed outright: asked about FastShip 3 / LP003's
committed transit time, the index's top 5 were Olist review digests (cosine
~0.55) while the exact top match was LP003's own annexure (0.77), so the
answer said the documents held no such information. Raising hnsw.ef_search
found the annexure at 100 and 200 and lost it again at 400, so no tuning
could be trusted. Exact search over the whole corpus took 71 ms.

The fix switches index scans off for the one transaction that runs a vector
query. These tests pin that the switch is issued, and issued BEFORE the
search statement -- after it, it would change nothing.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MCP_DIR = REPO_ROOT / "mcp"
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))
sys.path.insert(0, str(MCP_DIR))

if "psycopg_pool" not in sys.modules:
    try:
        import psycopg_pool  # noqa: F401
    except ImportError:
        import unittest.mock
        sys.modules["psycopg_pool"] = unittest.mock.MagicMock()

pytest.importorskip("psycopg")
import tools.vector_tools as vt  # noqa: E402

EXACT = "SET LOCAL enable_indexscan = off"


class _Cursor:
    description = [SimpleNamespace(name="chunk_id")]

    async def fetchall(self):
        return [("doc_sla_annexure_LP003_c001",)]


class _Conn:
    def __init__(self):
        self.statements: list[str] = []

    async def execute(self, sql, params=None):
        self.statements.append(" ".join(sql.split()))
        return _Cursor()


def _wire(monkeypatch, conn):
    class _Ctx:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, *exc):
            return False

    async def _embed(*args, **kwargs):
        return [0.0] * int(vt.cfg.retrieval.embedding_dim)

    monkeypatch.setattr(vt, "get_connection", lambda: _Ctx())
    monkeypatch.setattr(vt, "get_tenant_id", lambda: "t_test")
    monkeypatch.setattr(vt, "embed_text", _embed)


def test_shipped_config_asks_for_exact_search():
    assert vt.cfg.retrieval.exact_vector_search is True


def test_semantic_search_turns_index_scans_off_before_searching(monkeypatch):
    conn = _Conn()
    _wire(monkeypatch, conn)

    result = asyncio.run(vt.semantic_search("What transit time has LP003 committed to?"))

    assert conn.statements[0] == EXACT
    assert conn.statements[1].startswith("SELECT dc.chunk_id")
    assert "ORDER BY dc.embedding <=>" in conn.statements[1]
    assert result["matches"] == [{"chunk_id": "doc_sla_annexure_LP003_c001"}]


def test_switch_is_not_issued_when_exact_search_is_off(monkeypatch):
    conn = _Conn()
    monkeypatch.setattr(vt, "cfg", SimpleNamespace(retrieval=SimpleNamespace(exact_vector_search=False)))

    asyncio.run(vt._use_exact_search(conn))

    assert conn.statements == []
