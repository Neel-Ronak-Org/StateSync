"""
orchestrator/library_ingest.py
Turning a parsed upload into searchable passages ("chunks").

WHY THIS EXISTS. The parser service returns text page by page. Retrieval needs
passages small enough for the embedding model (mxbai-embed-large reads about
512 tokens) and large enough to mean something, each one traceable to a page so
an answer can cite it. This module cuts pages into such chunks, then feeds them
to the MCP's library.manage in batches (the MCP embeds and stores them).

The chunker is pure and deterministic, so it is unit-tested without a model or
a database (tests/test_library_ingest.py). Its rules:

  - a chunk never spans two pages (a citation must name one page);
  - within a page, paragraphs (blank-line separated) are packed together up to
    chunk_chars; an over-long paragraph is cut at a sentence end, else a line
    break, else a space, else hard;
  - consecutive chunks of one page overlap by up to chunk_overlap_chars (the
    tail of the previous chunk, from a word boundary) so a sentence cut at a
    boundary is whole in one of the two chunks; the overlap is budgeted inside
    chunk_chars, so no chunk exceeds it;
  - fragments shorter than chunk_min_chars (page numbers, running headers) are
    dropped.

Defaults: chunk_chars 1,400, overlap 200, minimum 40 (config/statesync.toml [library]).

`ingest` runs as a background task after the upload response is sent; it never
raises, and always leaves the document `ready` or `failed`, never stuck.
"""

from __future__ import annotations

import asyncio
import logging
import time
import unicodedata
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

from config_loader import cfg

logger = logging.getLogger(__name__)

_PARAGRAPH_SEP = "\n\n"
_SENTENCE_ENDS = (". ", "? ", "! ")
_ELLIPSIS = "…"
_DEFAULT_TITLE = "Untitled document"
_DEFAULT_FILENAME = "document"
_FAILED_MESSAGE = "Processing failed."
_FAILED_UNEXPECTED = "Processing failed unexpectedly."

Call = Callable[..., Awaitable[dict[str, Any]]]


# ── Chunking ─────────────────────────────────────────────────────────────────

def _paragraphs(text: str) -> list[str]:
    """Blank-line separated paragraphs, with whitespace runs inside a line collapsed."""
    paragraphs: list[str] = []
    lines: list[str] = []
    for raw in text.splitlines():
        line = " ".join(raw.split())
        if line:
            lines.append(line)
        elif lines:
            paragraphs.append("\n".join(lines))
            lines = []
    if lines:
        paragraphs.append("\n".join(lines))
    return paragraphs


def _split_long(text: str, limit: int) -> list[str]:
    """Cut text into pieces of at most `limit` characters, preferring natural boundaries."""
    pieces: list[str] = []
    rest = text
    while len(rest) > limit:
        window = rest[:limit]
        cut = max((window.rfind(end) + 1 for end in _SENTENCE_ENDS if window.rfind(end) >= 0), default=0)
        if cut <= 0:
            cut = window.rfind("\n") + 1 if window.rfind("\n") > 0 else 0
        if cut <= 0:
            cut = window.rfind(" ") + 1 if window.rfind(" ") > 0 else 0
        if cut <= 0:
            cut = limit
        piece = rest[:cut].strip()
        if piece:
            pieces.append(piece)
        rest = rest[cut:].strip()
    if rest:
        pieces.append(rest)
    return pieces


def _overlap_tail(text: str, overlap_chars: int) -> str:
    """The last <= overlap_chars characters of text, starting at a word boundary ('' if none)."""
    if overlap_chars <= 0 or not text:
        return ""
    if len(text) <= overlap_chars:
        return text
    tail = text[-overlap_chars:]
    if not text[-overlap_chars - 1].isspace():
        space = next((i for i, ch in enumerate(tail) if ch.isspace()), None)
        if space is None:
            return ""
        tail = tail[space:]
    return tail.strip()


def _page_bodies(text: str, body_limit: int) -> list[str]:
    """One page's text packed into bodies of at most body_limit characters."""
    bodies: list[str] = []
    current = ""
    for paragraph in _paragraphs(text):
        for piece in _split_long(paragraph, body_limit):
            if current and len(current) + len(_PARAGRAPH_SEP) + len(piece) > body_limit:
                bodies.append(current)
                current = piece
            else:
                current = current + _PARAGRAPH_SEP + piece if current else piece
    if current:
        bodies.append(current)
    return bodies


def chunk_pages(pages: list[dict], chunk_chars: int, overlap_chars: int, min_chars: int) -> list[dict]:
    """Chunks as [{ordinal, page, text}], ordinals 0..n-1 in reading order."""
    overlap = max(min(overlap_chars, chunk_chars - len(_PARAGRAPH_SEP) - 1), 0)
    body_limit = max(chunk_chars - overlap - len(_PARAGRAPH_SEP), 1)
    chunks: list[dict] = []
    for entry in pages:
        text = entry.get("text")
        if not isinstance(text, str):
            continue
        previous_body = ""
        for body in _page_bodies(text, body_limit):
            if len(body.strip()) < min_chars:
                continue
            tail = _overlap_tail(previous_body, overlap)
            chunks.append({"ordinal": len(chunks), "page": entry.get("page"),
                           "text": tail + _PARAGRAPH_SEP + body if tail else body})
            previous_body = body
    return chunks


# ── Names ────────────────────────────────────────────────────────────────────

def safe_filename(name: str) -> str:
    """The file's base name only: no directories, no control characters, capped in length."""
    base = str(name).replace("\\", "/").rsplit("/", 1)[-1]
    base = "".join(ch for ch in base if unicodedata.category(ch) != "Cc").strip()
    if base in ("", ".", ".."):
        return _DEFAULT_FILENAME
    return base[: int(cfg.library.title_max_chars)]


def title_from_filename(name: str) -> str:
    """A readable title: the safe file name without its last extension, separators as spaces."""
    base = safe_filename(name)
    dot = base.rfind(".")
    stem = base[:dot] if dot > 0 else base
    title = " ".join(stem.replace("_", " ").replace("-", " ").split())
    return title or _DEFAULT_TITLE


# ── The background job ───────────────────────────────────────────────────────

_RETRYABLE = "unavailable"  # the MCP's code for "the embedding service failed; nothing was stored"


async def _add_chunks(call: Call, document_id: str, chunks: list[dict]) -> dict[str, Any]:
    """One add_chunks batch, sent again up to [library].embed_retries times while the MCP answers
    "unavailable" (a slow or busy Ollama). Safe to resend: on that answer nothing was stored.
    Any other answer, success or refusal, is returned at once."""
    result = await call("add_chunks", document_id=document_id, chunks=chunks)
    for attempt in range(1, int(cfg.library.embed_retries) + 1):
        if result.get("ok") or result.get("error") != _RETRYABLE:
            break
        logger.warning("library: %s embedding unavailable; retry %d of %d", document_id, attempt,
                       cfg.library.embed_retries)
        await asyncio.sleep(float(cfg.library.embed_retry_backoff_s) * attempt)
        result = await call("add_chunks", document_id=document_id, chunks=chunks)
    return result


async def _finish_failed(call: Call, document_id: str, message: str) -> None:
    try:
        await call("finish_document", document_id=document_id, status="failed", error=message)
    except Exception:
        logger.error("library: could not mark %s failed", document_id, exc_info=True)


def make_chunks(pages: list[dict]) -> list[dict]:
    """The document's chunks, with the [library] sizes."""
    return chunk_pages(pages, int(cfg.library.chunk_chars), int(cfg.library.chunk_overlap_chars),
                       int(cfg.library.chunk_min_chars))


async def ingest(call: Call, document_id: str, pages: list[dict]) -> None:
    """Chunk, embed (through the MCP) and finish one document. Never raises."""
    started = time.monotonic()
    try:
        chunks = make_chunks(pages)
        batch = int(cfg.library.embed_batch)
        for start in range(0, len(chunks), batch):
            result = await _add_chunks(call, document_id, chunks[start:start + batch])
            if not result.get("ok"):
                logger.warning("library: %s add_chunks refused: %s", document_id, result.get("message"))
                await _finish_failed(call, document_id, str(result.get("message") or _FAILED_MESSAGE))
                return
        result = await call("finish_document", document_id=document_id, status="ready")
        if not result.get("ok"):
            await _finish_failed(call, document_id, str(result.get("message") or _FAILED_MESSAGE))
            return
        logger.info("library: %s ingested, %d pages -> %d chunks in %.1fs", document_id, len(pages),
                    len(chunks), time.monotonic() - started)
    except Exception:
        logger.error("library: ingesting %s failed after %.1fs", document_id, time.monotonic() - started,
                     exc_info=True)
        await _finish_failed(call, document_id, _FAILED_UNEXPECTED)


class IngestTasks:
    """Strong references to running background jobs (asyncio keeps only weak ones)."""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task] = set()

    def start(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def __len__(self) -> int:
        return len(self._tasks)
