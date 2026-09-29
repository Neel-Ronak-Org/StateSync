"""
mcp/tools/library_tools.py
MCP document-library tool (docs/V3_PLAN.md Phase 3c slice 5, decision D-v3-17,
migration 024): the account's folders of uploaded documents, and which folders
each workspace searches.

One JSON-RPC method, library.manage (decision D-v3-10), takes an `action`:

  folders, create_folder, rename_folder, delete_folder   folders in the account's library
  documents, start_document, delete_document             the documents inside them
  add_chunks, finish_document                            ingestion, driven by the orchestrator
  workspace_folders, set_workspace_folders               which folders a workspace searches
  workspace_documents                                    the READY documents a workspace can search
                                                         (titles, for the router)

WHY INGESTION IS SPLIT INTO start / add_chunks / finish. The orchestrator
parses the upload (in the isolated parser service) and chunks it; this tool
embeds and stores. A large PDF is hundreds of chunks, so it arrives in batches
of [library].embed_batch, each one Ollama request that fits inside the MCP
timeout. A document is `processing` from start_document until finish_document
says `ready` or `failed`; retrieval reads READY documents only.

THE RULES, enforced here rather than trusted from a caller:

1. Every statement filters on tenant_id (bound server-side, A0.6). A folder is
   owned when its owner_principal_id is the caller; a document is owned through
   its folder; a workspace through session_state.workspaces. Anything not owned
   is `not_found`, so existence is never revealed. Row-level security
   (migration 024) repeats the tenant check as a backstop.
2. Sizes, counts and lengths come from config [library]; nothing is hard-coded.
3. chunks are only added to a document that is still `processing`.
4. A `processing` document older than [library].processing_stale_s is REPORTED
   as failed (the orchestrator restarted mid-ingest); nothing is written.

Failures are data ({"ok": False, "error": code, "message": text}), the same
contract as workspaces.manage. Codes: not_found, invalid, limit_reached.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

import psycopg

from config_loader import cfg
from db import get_history_connection
from embeddings import embed_texts
from serialization import to_json_safe
from session import get_tenant_id

logger = logging.getLogger(__name__)

_ACTIONS = frozenset({
    "folders", "create_folder", "rename_folder", "delete_folder",
    "documents", "start_document", "add_chunks", "finish_document", "delete_document",
    "workspace_folders", "set_workspace_folders", "workspace_documents",
})
_FINISH_STATUSES = frozenset({"ready", "failed"})
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")  # noqa: literals - a SHA-256 digest is 64 hex characters
_ERROR_MAX_CHARS = 300  # noqa: literals - a stored error message is truncated for display
_ID_HEX_CHARS = 16  # noqa: literals - length of the random part of a generated id
_STALE_MESSAGE = "Processing stopped before finishing."


def _fail(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": code, "message": message}


def clean_text(value: Any, max_chars: int) -> str | None:
    """Trimmed text within max_chars, or None if missing or blank."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text[:max_chars] if text else None


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _new_id(prefix: str) -> str:
    return prefix + uuid.uuid4().hex[:_ID_HEX_CHARS]


# ── Views ────────────────────────────────────────────────────────────────────

_FOLDER_VIEW = """
    SELECT f.folder_id, f.name, f.created_at,
           (SELECT count(*) FROM workspace_docs.documents d WHERE d.folder_id = f.folder_id),
           (SELECT count(*) FROM workspace_docs.documents d WHERE d.folder_id = f.folder_id AND d.status = 'ready'),
           COALESCE((SELECT array_agg(wf.workspace_id ORDER BY wf.workspace_id)
                       FROM workspace_docs.workspace_folders wf WHERE wf.folder_id = f.folder_id), '{}')
    FROM workspace_docs.folders f
    WHERE f.tenant_id = %s AND f.owner_principal_id = %s
"""

_DOC_VIEW = """
    SELECT d.document_id, d.folder_id, d.title, d.filename, d.content_type, d.pages, d.bytes,
           d.status, d.error, d.chunk_count, d.created_at
    FROM workspace_docs.documents d
    JOIN workspace_docs.folders f ON f.folder_id = d.folder_id
    WHERE d.tenant_id = %s AND f.tenant_id = %s AND f.owner_principal_id = %s
"""


def _folder_from_row(row: tuple) -> dict[str, Any]:
    folder_id, name, created, total, ready, workspace_ids = row
    return to_json_safe({
        "folder_id": folder_id, "name": name, "created_at": created,
        "document_count": total, "ready_count": ready, "workspace_ids": list(workspace_ids or []),
    })


def _doc_from_row(row: tuple) -> dict[str, Any]:
    (document_id, folder_id, title, filename, content_type, pages, size, status, error, chunks, created) = row
    if status == "processing" and (datetime.now(timezone.utc) - created).total_seconds() > float(
            cfg.library.processing_stale_s):
        status, error = "failed", _STALE_MESSAGE
    return to_json_safe({
        "document_id": document_id, "folder_id": folder_id, "title": title, "filename": filename,
        "content_type": content_type, "pages": pages, "bytes": size, "status": status, "error": error,
        "chunk_count": chunks, "created_at": created,
    })


async def _folder_view(conn: Any, tenant_id: str, principal_id: str, folder_id: str) -> dict | None:
    row = await (await conn.execute(_FOLDER_VIEW + " AND f.folder_id = %s",
                                    (tenant_id, principal_id, folder_id))).fetchone()
    return _folder_from_row(row) if row else None


async def _doc_view(conn: Any, tenant_id: str, principal_id: str, document_id: str) -> dict | None:
    row = await (await conn.execute(_DOC_VIEW + " AND d.document_id = %s",
                                    (tenant_id, tenant_id, principal_id, document_id))).fetchone()
    return _doc_from_row(row) if row else None


async def _owns_folder(conn: Any, tenant_id: str, principal_id: str, folder_id: Any) -> bool:
    if not isinstance(folder_id, str) or not folder_id:
        return False
    row = await (await conn.execute(
        "SELECT 1 FROM workspace_docs.folders WHERE tenant_id = %s AND owner_principal_id = %s AND folder_id = %s",
        (tenant_id, principal_id, folder_id))).fetchone()
    return row is not None


async def _owns_workspace(conn: Any, tenant_id: str, principal_id: str, workspace_id: Any) -> bool:
    if not isinstance(workspace_id, str) or not workspace_id:
        return False
    row = await (await conn.execute(
        "SELECT 1 FROM session_state.workspaces WHERE tenant_id = %s AND owner_principal_id = %s "
        "AND workspace_id = %s", (tenant_id, principal_id, workspace_id))).fetchone()
    return row is not None


def _folder_name(name: Any) -> str | None:
    return clean_text(name, int(cfg.library.folder_name_max_chars))


_NAME_TAKEN = "A folder with that name already exists."


# ── Folders ──────────────────────────────────────────────────────────────────

async def _folders(tenant_id, principal_id, **_):
    async with get_history_connection() as conn:
        rows = await (await conn.execute(_FOLDER_VIEW + " ORDER BY f.name", (tenant_id, principal_id))).fetchall()
    return {"ok": True, "folders": [_folder_from_row(r) for r in rows]}


async def _create_folder(tenant_id, principal_id, name=None, **_):
    clean = _folder_name(name)
    if clean is None:
        return _fail("invalid", "name must not be blank.")
    folder_id = _new_id("fd_")
    try:
        async with get_history_connection() as conn:
            count = (await (await conn.execute(
                "SELECT count(*) FROM workspace_docs.folders WHERE tenant_id = %s AND owner_principal_id = %s",
                (tenant_id, principal_id))).fetchone())[0]
            if count >= int(cfg.library.max_folders):
                return _fail("limit_reached", f"You already have {count} folders; the limit is "
                                              f"{cfg.library.max_folders}.")
            taken = await (await conn.execute(
                "SELECT 1 FROM workspace_docs.folders WHERE tenant_id = %s AND owner_principal_id = %s AND name = %s",
                (tenant_id, principal_id, clean))).fetchone()
            if taken:
                return _fail("invalid", _NAME_TAKEN)
            await conn.execute(
                "INSERT INTO workspace_docs.folders (folder_id, tenant_id, owner_principal_id, name) "
                "VALUES (%s, %s, %s, %s)", (folder_id, tenant_id, principal_id, clean))
            view = await _folder_view(conn, tenant_id, principal_id, folder_id)
    except psycopg.errors.UniqueViolation:
        return _fail("invalid", _NAME_TAKEN)
    logger.info("library: created folder %s (tenant=%s principal=%s)", folder_id, tenant_id, principal_id)
    return {"ok": True, "folder": view}


async def _rename_folder(tenant_id, principal_id, folder_id=None, name=None, **_):
    clean = _folder_name(name)
    if clean is None:
        return _fail("invalid", "name must not be blank.")
    try:
        async with get_history_connection() as conn:
            if not await _owns_folder(conn, tenant_id, principal_id, folder_id):
                return _fail("not_found", "No such folder.")
            taken = await (await conn.execute(
                "SELECT 1 FROM workspace_docs.folders WHERE tenant_id = %s AND owner_principal_id = %s "
                "AND name = %s AND folder_id <> %s", (tenant_id, principal_id, clean, folder_id))).fetchone()
            if taken:
                return _fail("invalid", _NAME_TAKEN)
            await conn.execute(
                "UPDATE workspace_docs.folders SET name = %s WHERE tenant_id = %s AND owner_principal_id = %s "
                "AND folder_id = %s", (clean, tenant_id, principal_id, folder_id))
            view = await _folder_view(conn, tenant_id, principal_id, folder_id)
    except psycopg.errors.UniqueViolation:
        return _fail("invalid", _NAME_TAKEN)
    return {"ok": True, "folder": view}


async def _delete_folder(tenant_id, principal_id, folder_id=None, **_):
    if not isinstance(folder_id, str) or not folder_id:
        return _fail("not_found", "No such folder.")
    async with get_history_connection() as conn:
        row = await (await conn.execute(
            "DELETE FROM workspace_docs.folders WHERE tenant_id = %s AND owner_principal_id = %s "
            "AND folder_id = %s RETURNING folder_id", (tenant_id, principal_id, folder_id))).fetchone()
    if row is None:
        return _fail("not_found", "No such folder.")
    logger.info("library: deleted folder %s (tenant=%s)", folder_id, tenant_id)
    return {"ok": True, "deleted": 1}


# ── Documents ────────────────────────────────────────────────────────────────

async def _documents(tenant_id, principal_id, folder_id=None, **_):
    async with get_history_connection() as conn:
        if folder_id is not None:
            if not await _owns_folder(conn, tenant_id, principal_id, folder_id):
                return _fail("not_found", "No such folder.")
            rows = await (await conn.execute(
                _DOC_VIEW + " AND d.folder_id = %s ORDER BY d.created_at DESC",
                (tenant_id, tenant_id, principal_id, folder_id))).fetchall()
        else:
            rows = await (await conn.execute(
                _DOC_VIEW + " ORDER BY d.created_at DESC", (tenant_id, tenant_id, principal_id))).fetchall()
    return {"ok": True, "documents": [_doc_from_row(r) for r in rows]}


async def _start_document(tenant_id, principal_id, folder_id=None, title=None, filename=None, content_type=None,
                          pages=None, bytes=None, sha256=None, **_):  # noqa: A002 - `bytes` is the wire name
    if not _is_int(pages) or pages < 1:
        return _fail("invalid", "pages must be a whole number of at least 1.")
    if not _is_int(bytes) or bytes < 1 or bytes > int(cfg.library.max_upload_bytes):
        return _fail("invalid", "bytes must be between 1 and the upload limit.")
    if content_type not in list(cfg.library.content_types):
        return _fail("invalid", "That file type is not supported.")
    clean_title = clean_text(title, int(cfg.library.title_max_chars))
    clean_filename = clean_text(filename, int(cfg.library.title_max_chars))
    if clean_title is None or clean_filename is None:
        return _fail("invalid", "title and filename must not be blank.")
    digest = sha256.lower() if isinstance(sha256, str) else ""
    if not _SHA256_HEX.match(digest):
        return _fail("invalid", "sha256 must be 64 hexadecimal characters.")
    document_id = _new_id("up_")
    async with get_history_connection() as conn:
        if not await _owns_folder(conn, tenant_id, principal_id, folder_id):
            return _fail("not_found", "No such folder.")
        duplicate = await (await conn.execute(
            "SELECT 1 FROM workspace_docs.documents WHERE tenant_id = %s AND folder_id = %s AND sha256 = %s",
            (tenant_id, folder_id, digest))).fetchone()
        if duplicate:
            return _fail("invalid", "This file is already in the folder.")
        total = (await (await conn.execute(
            "SELECT count(*) FROM workspace_docs.documents d JOIN workspace_docs.folders f "
            "ON f.folder_id = d.folder_id WHERE d.tenant_id = %s AND f.owner_principal_id = %s",
            (tenant_id, principal_id))).fetchone())[0]
        if total >= int(cfg.library.max_documents):
            return _fail("limit_reached", f"Your library already has {total} documents; the limit is "
                                          f"{cfg.library.max_documents}.")
        await conn.execute(
            "INSERT INTO workspace_docs.documents (document_id, tenant_id, folder_id, title, filename, pages, "
            "bytes, uploaded_by, content_type, sha256, status) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'processing')",
            (document_id, tenant_id, folder_id, clean_title, clean_filename, pages, bytes, principal_id,
             content_type, digest))
        view = await _doc_view(conn, tenant_id, principal_id, document_id)
    logger.info("library: started %s (%d pages, %d bytes, tenant=%s)", document_id, pages, bytes, tenant_id)
    return {"ok": True, "document": view}


def _validate_chunks(chunks: Any) -> tuple[list[dict[str, Any]] | None, dict[str, Any] | None]:
    """The chunks as clean dicts, or a refusal. Runs before any database access."""
    if not isinstance(chunks, list) or not chunks:
        return None, _fail("invalid", "chunks must be a non-empty list.")
    if len(chunks) > int(cfg.library.embed_batch):
        return None, _fail("invalid", f"At most {cfg.library.embed_batch} chunks per call.")
    clean: list[dict[str, Any]] = []
    for item in chunks:
        if not isinstance(item, dict):
            return None, _fail("invalid", "Each chunk must be an object.")
        ordinal, page, text = item.get("ordinal"), item.get("page"), item.get("text")
        if not _is_int(ordinal) or ordinal < 0:
            return None, _fail("invalid", "Each chunk needs a whole-number ordinal of at least 0.")
        if page is not None and (not _is_int(page) or page < 0):
            return None, _fail("invalid", "A chunk's page must be a whole number or null.")
        if not isinstance(text, str) or not text.strip():
            return None, _fail("invalid", "Each chunk needs some text.")
        if len(text) > int(cfg.library.chunk_max_chars):
            return None, _fail("invalid", f"A chunk can be at most {cfg.library.chunk_max_chars} characters.")
        if "\x00" in text:
            return None, _fail("invalid", "Chunk text must not contain NUL characters.")
        clean.append({"ordinal": ordinal, "page": page, "text": text.strip()})
    return clean, None


async def _processing_status(conn: Any, tenant_id: str, principal_id: str, document_id: Any) -> str | None:
    """The stored status of an owned document, or None if it is not owned."""
    if not isinstance(document_id, str) or not document_id:
        return None
    row = await (await conn.execute(
        "SELECT d.status FROM workspace_docs.documents d JOIN workspace_docs.folders f ON f.folder_id = d.folder_id "
        "WHERE d.tenant_id = %s AND f.tenant_id = %s AND f.owner_principal_id = %s AND d.document_id = %s",
        (tenant_id, tenant_id, principal_id, document_id))).fetchone()
    return row[0] if row else None


async def _add_chunks(tenant_id, principal_id, document_id=None, chunks=None, **_):
    clean, refusal = _validate_chunks(chunks)
    if refusal:
        return refusal
    async with get_history_connection() as conn:
        status = await _processing_status(conn, tenant_id, principal_id, document_id)
    if status is None:
        return _fail("not_found", "No such document.")
    if status != "processing":
        return _fail("invalid", "This document is no longer being processed.")
    try:
        vectors = await embed_texts([c["text"] for c in clean], model=cfg.retrieval.embedding_model,
                                    dim=int(cfg.retrieval.embedding_dim))
    except Exception as exc:
        logger.error("library: embedding failed for %s: %s", document_id, exc, exc_info=True)
        return _fail("invalid", "The embedding service failed.")
    async with get_history_connection() as conn:
        if await _processing_status(conn, tenant_id, principal_id, document_id) != "processing":
            return _fail("invalid", "This document is no longer being processed.")
        for item, vector in zip(clean, vectors, strict=True):
            await conn.execute(
                "INSERT INTO workspace_docs.chunks (chunk_id, document_id, tenant_id, ordinal, page, content_md, "
                "embedding) VALUES (%s, %s, %s, %s, %s, %s, %s::vector) ON CONFLICT (chunk_id) DO NOTHING",
                (f"{document_id}_c{item['ordinal']:05d}", document_id, tenant_id, item["ordinal"], item["page"],
                 item["text"], "[" + ",".join(repr(float(x)) for x in vector) + "]"))
    return {"ok": True, "added": len(clean)}


async def _finish_document(tenant_id, principal_id, document_id=None, status=None, error=None, **_):
    if status not in _FINISH_STATUSES:
        return _fail("invalid", f"status must be one of {sorted(_FINISH_STATUSES)}.")
    async with get_history_connection() as conn:
        if await _processing_status(conn, tenant_id, principal_id, document_id) is None:
            return _fail("not_found", "No such document.")
        stored_error: str | None = None
        if status == "ready":
            count = (await (await conn.execute(
                "SELECT count(*) FROM workspace_docs.chunks WHERE tenant_id = %s AND document_id = %s",
                (tenant_id, document_id))).fetchone())[0]
            if count == 0:
                status, stored_error = "failed", "No text was found."
        else:
            count = 0
            stored_error = (error.strip() if isinstance(error, str) else "")[:_ERROR_MAX_CHARS] or "Processing failed."
        if status == "failed":
            await conn.execute("DELETE FROM workspace_docs.chunks WHERE tenant_id = %s AND document_id = %s",
                               (tenant_id, document_id))
            count = 0
        await conn.execute(
            "UPDATE workspace_docs.documents SET status = %s, error = %s, chunk_count = %s "
            "WHERE tenant_id = %s AND document_id = %s", (status, stored_error, count, tenant_id, document_id))
        view = await _doc_view(conn, tenant_id, principal_id, document_id)
    logger.info("library: finished %s as %s (%d chunks, tenant=%s)", document_id, status, count, tenant_id)
    return {"ok": True, "document": view}


async def _delete_document(tenant_id, principal_id, document_id=None, **_):
    async with get_history_connection() as conn:
        if await _processing_status(conn, tenant_id, principal_id, document_id) is None:
            return _fail("not_found", "No such document.")
        await conn.execute("DELETE FROM workspace_docs.documents WHERE tenant_id = %s AND document_id = %s",
                           (tenant_id, document_id))
    return {"ok": True, "deleted": 1}


# ── Workspace links ──────────────────────────────────────────────────────────

async def _workspace_folders(tenant_id, principal_id, workspace_id=None, **_):
    async with get_history_connection() as conn:
        if not await _owns_workspace(conn, tenant_id, principal_id, workspace_id):
            return _fail("not_found", "No such workspace.")
        rows = await (await conn.execute(
            "SELECT folder_id FROM workspace_docs.workspace_folders WHERE tenant_id = %s AND workspace_id = %s "
            "ORDER BY folder_id", (tenant_id, workspace_id))).fetchall()
    return {"ok": True, "folder_ids": [r[0] for r in rows]}


async def _set_workspace_folders(tenant_id, principal_id, workspace_id=None, folder_ids=None, **_):
    if not isinstance(folder_ids, list) or not all(isinstance(f, str) and f for f in folder_ids):
        return _fail("invalid", "folder_ids must be a list of folder ids.")
    unique = list(dict.fromkeys(folder_ids))
    if len(unique) > int(cfg.library.max_folders):
        return _fail("invalid", f"At most {cfg.library.max_folders} folders.")
    async with get_history_connection() as conn:
        if not await _owns_workspace(conn, tenant_id, principal_id, workspace_id):
            return _fail("not_found", "No such workspace.")
        for folder_id in unique:
            if not await _owns_folder(conn, tenant_id, principal_id, folder_id):
                return _fail("not_found", "No such folder.")
        await conn.execute("DELETE FROM workspace_docs.workspace_folders WHERE tenant_id = %s AND workspace_id = %s",
                           (tenant_id, workspace_id))
        for folder_id in unique:
            await conn.execute(
                "INSERT INTO workspace_docs.workspace_folders (workspace_id, folder_id, tenant_id) "
                "VALUES (%s, %s, %s)", (workspace_id, folder_id, tenant_id))
    return {"ok": True, "folder_ids": sorted(unique)}


async def _workspace_documents(tenant_id, principal_id, workspace_id=None, **_):
    async with get_history_connection() as conn:
        if not await _owns_workspace(conn, tenant_id, principal_id, workspace_id):
            return _fail("not_found", "No such workspace.")
        rows = await (await conn.execute(
            "SELECT d.document_id, d.title FROM workspace_docs.documents d "
            "JOIN workspace_docs.workspace_folders wf ON wf.folder_id = d.folder_id "
            "WHERE d.tenant_id = %s AND wf.workspace_id = %s AND d.status = 'ready' "
            "ORDER BY d.created_at DESC LIMIT %s",
            (tenant_id, workspace_id, int(cfg.library.router_titles_max)))).fetchall()
    return {"ok": True, "documents": [{"document_id": r[0], "title": r[1]} for r in rows]}


_HANDLERS = {
    "folders": _folders, "create_folder": _create_folder, "rename_folder": _rename_folder,
    "delete_folder": _delete_folder, "documents": _documents, "start_document": _start_document,
    "add_chunks": _add_chunks, "finish_document": _finish_document, "delete_document": _delete_document,
    "workspace_folders": _workspace_folders, "set_workspace_folders": _set_workspace_folders,
    "workspace_documents": _workspace_documents,
}


async def manage(action: str, principal_id: str = "", **params: Any) -> dict[str, Any]:
    """library.manage -- see the module docstring for the actions and guarantees."""
    tenant_id = get_tenant_id()
    if not isinstance(principal_id, str) or not principal_id:
        return _fail("invalid", "principal_id is required.")
    if action not in _ACTIONS:
        return _fail("invalid", f"Unknown action {action!r}. Valid: {sorted(_ACTIONS)}.")
    return await _HANDLERS[action](tenant_id, principal_id, **params)
