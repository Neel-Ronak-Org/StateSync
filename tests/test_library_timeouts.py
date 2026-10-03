"""
tests/test_library_timeouts.py

WHY THIS EXISTS. On 2026-10-01 a FinanceBench ingest (docs/eval/external_datasets.md)
failed document after document "after 20.0s": the orchestrator gave every MCP call the
session timeout (statement timeout 15 s + overhead 5 s), but add_chunks embeds a whole
batch through Ollama and the MCP waits up to retrieval.embedding_timeout_s (30 s) for it.
A slow batch timed out in the orchestrator while the MCP was still working. add_chunks
must now wait longer than the MCP's own embedding wait, and nothing else may change.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))

from config_loader import cfg  # noqa: E402
from mcp_client import library_manage  # noqa: E402


class _Session:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict, float | None]] = []

    async def call(self, method, params, timeout=None):
        self.sent.append((method, params, timeout))
        return {"ok": True}


def test_add_chunks_waits_longer_than_the_mcp_waits_for_ollama():
    """WHY: the MCP must always answer first -- stored, or "unavailable" with nothing stored."""
    session = _Session()
    asyncio.run(library_manage(session, "p_1", "add_chunks", document_id="up_1", chunks=[]))
    _, params, timeout = session.sent[0]
    assert params == {"action": "add_chunks", "principal_id": "p_1", "document_id": "up_1", "chunks": []}
    assert timeout == float(cfg.retrieval.embedding_timeout_s) + float(cfg.mcp.network_overhead_s)
    assert timeout > float(cfg.retrieval.embedding_timeout_s)


def test_every_other_library_action_keeps_the_session_timeout():
    """WHY: only the embedding call is slow by nature; the rest keep the 20 s session default."""
    session = _Session()
    for action in ("start_document", "finish_document", "list_documents"):
        asyncio.run(library_manage(session, "p_1", action, document_id="up_1"))
    assert [timeout for _, _, timeout in session.sent] == [None, None, None]
