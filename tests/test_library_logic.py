"""
tests/test_library_logic.py
The refusals in the document-library tool (mcp/tools/library_tools.py) that
happen before any database access.

WHY THIS EXISTS. library.manage takes its arguments from the orchestrator, which
took them from a browser. Everything it can refuse without a database it must
refuse first: an unknown action, a missing principal, a blank folder name, a
chunk over the embedding budget, a batch bigger than one Ollama request should
carry, a malformed digest, a type the parser would not read, a status nobody
may set, a malformed folder list. None of these tests has a database; if one
reached it, the call would raise instead of returning a refusal. Ownership,
quotas and storage are verified live (the API smoke test).
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

DIGEST = "a" * 64
GOOD_DOC = {"folder_id": "fd_1", "title": "T", "filename": "t.pdf", "content_type": "application/pdf",
            "pages": 1, "bytes": 10, "sha256": DIGEST}


def _manage(action: str, principal_id: str = "p1", **params):
    async def run():
        set_session(tenant_id="t-test")
        return await lt.manage(action, principal_id, **params)
    return asyncio.run(run())


def _refused(result: dict, code: str = "invalid") -> None:
    assert result["ok"] is False and result["error"] == code and result["message"]


def test_unknown_action_and_missing_principal():
    _refused(_manage("explode"))
    _refused(_manage("folders", principal_id=""))


def test_blank_folder_names_are_refused():
    _refused(_manage("create_folder", name="   "))
    _refused(_manage("create_folder"))
    _refused(_manage("rename_folder", folder_id="fd_1", name=""))


def test_chunk_refusals():
    ok = {"ordinal": 0, "page": 1, "text": "hello"}
    _refused(_manage("add_chunks", document_id="up_1", chunks=[]))
    _refused(_manage("add_chunks", document_id="up_1", chunks="text"))
    _refused(_manage("add_chunks", document_id="up_1",
                     chunks=[{**ok, "text": "x" * (int(cfg.library.chunk_max_chars) + 1)}]))
    _refused(_manage("add_chunks", document_id="up_1", chunks=[ok] * (int(cfg.library.embed_batch) + 1)))
    _refused(_manage("add_chunks", document_id="up_1", chunks=[{**ok, "text": "   "}]))
    _refused(_manage("add_chunks", document_id="up_1", chunks=[{**ok, "text": "a\x00b"}]))
    _refused(_manage("add_chunks", document_id="up_1", chunks=[{**ok, "ordinal": -1}]))
    # structure fields (migration 027): a kind the CHECK constraint would refuse, a section that is too long
    _refused(_manage("add_chunks", document_id="up_1", chunks=[{**ok, "kind": "image"}]))
    _refused(_manage("add_chunks", document_id="up_1", chunks=[{**ok, "section": 5}]))
    _refused(_manage("add_chunks", document_id="up_1",
                     chunks=[{**ok, "section": "s" * (int(cfg.library.section_max_chars) + 1)}]))
    _refused(_manage("add_chunks", document_id="up_1", chunks=[{**ok, "section": "a\x00b"}]))


def test_chunk_kind_and_section_are_optional_and_cleaned():
    ok = {"ordinal": 0, "page": 1, "text": " hello "}
    clean, refusal = lt._validate_chunks([ok])
    assert refusal is None and clean == [{"ordinal": 0, "page": 1, "text": "hello", "kind": "text", "section": None}]
    longest = "s" * int(cfg.library.section_max_chars)
    clean, refusal = lt._validate_chunks([{**ok, "kind": "table", "section": f" {longest} "},
                                          {**ok, "ordinal": 1, "section": "  "}])
    assert refusal is None
    assert [(c["kind"], c["section"]) for c in clean] == [("table", longest), ("text", None)]


@pytest.mark.parametrize("change", [
    {"sha256": "xyz"}, {"sha256": "a" * 63}, {"sha256": None},
    {"content_type": "image/png"}, {"pages": 0}, {"pages": "3"}, {"bytes": 0},
    {"bytes": int(cfg.library.max_upload_bytes) + 1}, {"title": "  "},
])
def test_start_document_refusals(change):
    _refused(_manage("start_document", **{**GOOD_DOC, **change}))


def test_finish_document_status_must_be_ready_or_failed():
    _refused(_manage("finish_document", document_id="up_1", status="processing"))
    _refused(_manage("finish_document", document_id="up_1"))


def test_workspace_folder_ids_must_be_a_list_of_strings():
    _refused(_manage("set_workspace_folders", workspace_id="w1", folder_ids="fd_1"))
    _refused(_manage("set_workspace_folders", workspace_id="w1", folder_ids=[1, 2]))
    _refused(_manage("set_workspace_folders", workspace_id="w1", folder_ids=None))
    too_many = [f"fd_{i}" for i in range(int(cfg.library.max_folders) + 1)]
    _refused(_manage("set_workspace_folders", workspace_id="w1", folder_ids=too_many))
