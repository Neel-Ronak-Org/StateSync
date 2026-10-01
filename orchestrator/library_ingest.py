"""
orchestrator/library_ingest.py
Turning a parsed upload into searchable chunks (docs/V3_PLAN.md Phase 3c slice 5).

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

That is `chunk_pages`, the shipped chunker ([library].chunker = "paragraph").
`chunk_blocks` (chunker = "layout", docs/specs/rag_v2.md Phase C) cuts the
parser's layout blocks instead, so that what retrieval returns is a unit a
reader would recognise:

  - a heading opens a section; the section path is the stack of open headings
    (a heading of the same or a shallower level closes the deeper ones); a
    chunk never contains text from two sections, nor from two pages;
  - paragraphs and list items are packed up to layout_chunk_chars; one longer
    than that is cut at sentence ends; consecutive chunks of one section and
    page overlap by up to layout_overlap_chars (never across a section, a page
    or a table); a fragment under chunk_min_chars is merged into the previous
    text chunk of its section and page, or dropped if there is none;
  - a table is one chunk of Markdown when it fits table_chunk_max_chars, else
    row groups that each repeat the header row; the short paragraph right
    before a table (it usually says what the table is) is copied into its
    chunk;
  - every chunk starts with a breadcrumb line, "{title} › {h1} › {h2}", then a
    blank line, so the text that is embedded and shown always says where it is
    from. A heading that repeats the document title is left out of the path.

`ingest` runs as a background task after the upload response is sent; it never
raises, and always leaves the document `ready` or `failed`, never stuck.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import unicodedata
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

from config_loader import cfg

logger = logging.getLogger(__name__)

_PARAGRAPH_SEP = "\n\n"
_SENTENCE_ENDS = (". ", "? ", "! ")
_CRUMB_SEP = " › "
LAYOUT_CHUNKER = "layout"
_LIST_MARKER = re.compile(r"^(?:•|[-*]\s|\d+[.)]\s)")
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


# ── Layout chunking (docs/specs/rag_v2.md Phase C) ───────────────────────────

def _norm(text: str) -> str:
    return " ".join(text.casefold().split())


def _markdown_row(cells: list[str], width: int) -> str:
    padded = [" ".join(str(cell).split()).replace("|", "\\|") for cell in cells] + [""] * (width - len(cells))
    return "| " + " | ".join(padded) + " |"


def _table_markdown(rows: list[list[str]]) -> list[str]:
    """A table as Markdown lines: the header row, the separator row, then one line per body row."""
    width = max(len(row) for row in rows)
    lines = [_markdown_row(rows[0], width), "|" + "|".join(["---"] * width) + "|"]
    lines.extend(_markdown_row(row, width) for row in rows[1:])
    return lines


def _table_groups(rows: list[list[str]], limit: int) -> list[str]:
    """Markdown of a table in one piece if it fits `limit`, else row groups that each repeat the header."""
    lines = _table_markdown(rows)
    whole = "\n".join(lines)
    if len(whole) <= limit:
        return [whole]
    header = lines[:2]
    groups: list[str] = []
    current: list[str] = []
    for row in lines[2:]:
        if current and len("\n".join(header + current + [row])) > limit:
            groups.append("\n".join(header + current))
            current = []
        current.append(row)
    if current:
        groups.append("\n".join(header + current))
    return groups


def _render_piece(block: dict) -> str:
    """A paragraph as is; a list item with a bullet, unless the document's own marker is already there."""
    text = block["text"]
    return text if block["kind"] == "paragraph" or _LIST_MARKER.match(text) else "- " + text


def _pack_text(pieces: list[tuple[str, str]], limit: int) -> list[str]:
    """Bodies of at most `limit` characters from (kind, text) pieces; list items stay on adjacent lines."""
    bodies: list[str] = []
    current = ""
    previous_kind = ""
    for kind, text in pieces:
        for part in _split_long(text, limit):
            separator = "\n" if kind == "list_item" and previous_kind == "list_item" else _PARAGRAPH_SEP
            if current and len(current) + len(separator) + len(part) > limit:
                bodies.append(current)
                current = part
            else:
                current = current + separator + part if current else part
            previous_kind = kind
    if current:
        bodies.append(current)
    return bodies


def _clean_blocks(blocks: list[dict]) -> list[dict]:
    """The blocks this chunker understands, nothing else (the parser is trusted, but not blindly)."""
    kept: list[dict] = []
    for block in blocks if isinstance(blocks, list) else []:
        if not isinstance(block, dict):
            continue
        kind = block.get("kind")
        page = block.get("page")
        if kind in ("heading", "paragraph", "list_item") and isinstance(block.get("text"), str) and block["text"].strip():
            entry = {"kind": kind, "page": page, "text": block["text"].strip()}
            if kind == "heading":
                level = block.get("level")
                entry["text"] = " ".join(entry["text"].split())
                entry["level"] = level if isinstance(level, int) and level >= 1 else 1
            kept.append(entry)
        elif kind == "table" and isinstance(block.get("rows"), list):
            rows = [[str(cell) for cell in row] for row in block["rows"] if isinstance(row, list) and any(map(str, row))]
            if rows:
                kept.append({"kind": "table", "page": page, "rows": rows})
    return kept


def chunk_blocks(blocks: list[dict], title: str, cfg: Any) -> list[dict]:
    """Chunks as [{ordinal, page, text, kind, section}] from the parser's layout blocks, in reading order.

    `cfg` is the whole config object (its `library` section is read), so a test can pass small budgets.
    """
    lib = cfg.library
    chunk_chars, overlap_chars = int(lib.layout_chunk_chars), int(lib.layout_overlap_chars)
    min_chars, table_chars = int(lib.chunk_min_chars), int(lib.table_chunk_max_chars)
    context_chars, section_chars = int(lib.table_context_max_chars), int(lib.section_max_chars)
    hard_limit = int(lib.chunk_max_chars)
    title = " ".join(str(title or "").split())

    chunks: list[dict] = []
    stack: list[tuple[int, str]] = []  # the open headings, shallowest first

    def section() -> str | None:
        path = [text for _, text in stack if _norm(text) != _norm(title)]
        return _CRUMB_SEP.join(path)[:section_chars].rstrip() or None

    def emit(page: Any, kind: str, body: str, where: str | None) -> dict:
        crumb = _CRUMB_SEP.join(part for part in (title, where) if part)
        text = (crumb + _PARAGRAPH_SEP + body if crumb else body)[:hard_limit]
        chunk = {"ordinal": len(chunks), "page": page, "text": text, "kind": kind, "section": where}
        chunks.append(chunk)
        return chunk

    run: list[tuple[str, str]] = []  # (kind, rendered text) pieces of the current section and page
    run_page: Any = None

    def flush_run() -> None:
        nonlocal run
        where = section()
        previous_body = ""
        for body in _pack_text(run, chunk_chars):
            if len(body.strip()) < min_chars:
                last = chunks[-1] if chunks else None
                if last and last["kind"] == "text" and last["section"] == where and last["page"] == run_page:
                    last["text"] = (last["text"] + _PARAGRAPH_SEP + body)[:hard_limit]
                continue
            tail = _overlap_tail(previous_body, overlap_chars)
            emit(run_page, "text", tail + _PARAGRAPH_SEP + body if tail else body, where)
            previous_body = body
        run = []

    previous_block: dict | None = None
    for block in _clean_blocks(blocks):
        kind = block["kind"]
        if kind == "heading":
            flush_run()
            while stack and stack[-1][0] >= block["level"]:
                stack.pop()
            stack.append((block["level"], block["text"]))
        elif kind == "table":
            flush_run()
            where = section()
            context = ""
            if (previous_block and previous_block["kind"] == "paragraph" and previous_block["page"] == block["page"]
                    and len(previous_block["text"]) <= context_chars):
                context = previous_block["text"] + _PARAGRAPH_SEP
            for group in _table_groups(block["rows"], table_chars):
                emit(block["page"], "table", context + group, where)
        else:
            if run and block["page"] != run_page:
                flush_run()
            run_page = block["page"]
            run.append((kind, _render_piece(block)))
        previous_block = block
    flush_run()
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


def make_chunks(pages: list[dict], blocks: list[dict] | None, title: str | None) -> list[dict]:
    """The document's chunks by the configured chunker; flat pages whenever layout blocks are not usable."""
    if cfg.library.chunker == LAYOUT_CHUNKER and blocks:
        chunks = chunk_blocks(blocks, title or _DEFAULT_TITLE, cfg)
        if chunks:
            return chunks
        logger.warning("library: the layout chunker produced no chunks; cutting flat pages instead")
    return chunk_pages(pages, int(cfg.library.chunk_chars), int(cfg.library.chunk_overlap_chars),
                       int(cfg.library.chunk_min_chars))


async def ingest(call: Call, document_id: str, pages: list[dict], blocks: list[dict] | None = None,
                 title: str | None = None) -> None:
    """Chunk, embed (through the MCP) and finish one document. Never raises."""
    started = time.monotonic()
    try:
        chunks = make_chunks(pages, blocks, title)
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
