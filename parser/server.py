"""
parser/server.py
The upload parser (docs/V3_PLAN.md Phase 3c slice 5): turns an uploaded file
into plain text, page by page, and (docs/specs/rag_v2.md Phase B) into layout
blocks: headings, paragraphs, list items and whole tables. Nothing else.

WHY THIS IS ITS OWN SERVICE. An uploaded PDF is the most hostile input
StateSync accepts: PDF parsers have a long history of crashes, runaway memory
and near-infinite loops on crafted files. So parsing happens here, in a
container that
  - sits on an internal-only network (parser_net) that reaches neither the
    internet nor the databases, and is only reachable from the orchestrator;
  - holds no credentials and no secrets, runs as a non-root user on a
    read-only filesystem with every Linux capability dropped;
  - parses each file in a fresh child process with an address-space limit, a
    CPU-time limit and a wall-clock timeout, so a hostile file can at worst
    kill its own child, never the service.
The limits come from [library] in config/statesync.toml (mounted read-only).

API (HTTP, JSON out):
  GET  /health  -> {"ok": true}
  POST /parse   body = the file's bytes, Content-Type = one of
                [library].content_types, Content-Length required.
                200 {"ok": true, "pages": [{"page": 1, "text": "..."}, ...],
                     "blocks": [{"page": 1, "kind": "heading", "level": 1, "text": "..."},
                                {"page": 1, "kind": "paragraph" | "list_item", "text": "..."},
                                {"page": 1, "kind": "table", "rows": [["h1", "h2"], ["a", "b"]]}, ...]}
                    (text files have one "pages" entry with "page": null; their blocks have
                    "page": null too. "pages" is exactly what it was before blocks existed.)
                4xx {"ok": false, "error": "<code>", "message": "..."}
                    codes: too_large, unsupported_type, too_many_pages,
                    encrypted, unreadable, timeout, no_text,
                    limits_unavailable (the safety limits could not be set)

WHY BLOCKS. Flat page text loses what a reader uses to find an answer: which
heading a passage sits under, and which cells of a table belong together.
pdfplumber supplies the layout: tables come from `find_tables()` and the
narrative text is read from the page OUTSIDE every table's bounding box, so a
table's cells never also appear as paragraph text. Headings are lines that are
short and either larger than the body font (the document's character-weighted
median size) or bold. Paragraphs break where the line pitch exceeds
paragraph_gap_ratio line heights (measured baseline to baseline, as typesetters
specify line spacing: a 10 pt font set at 12 pt pitch is 1.2 line heights).

KNOWN LIMITATIONS: multi-column pages are read top-to-bottom across the full
width (no column detection), so a two-column page interleaves its columns;
scanned pages have no text layer (no OCR); a table drawn without ruling lines
may not be found and then stays narrative text. If block extraction fails for a
document or page, the pypdf text of that page becomes paragraph blocks, so an
upload is never refused because of the layout pass alone.

Only the Python standard library, pypdf and pdfplumber are used.
"""

from __future__ import annotations

import json
import logging
import multiprocessing
import os
import queue
import re
import resource
import time
import tomllib
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from collections import Counter
from pathlib import Path
from typing import Any

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("parser")

_CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/app/config"))
LIMITS: dict[str, Any] = tomllib.loads((_CONFIG_DIR / "statesync.toml").read_text())["library"]
_MB = 1024 * 1024
_PDF = "application/pdf"
_MARKDOWN = "text/markdown"
_POLL_S = 0.2


class ParseError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def clean(text: str) -> str:
    """Postgres TEXT cannot hold NUL; other control characters are noise."""
    return "".join(ch for ch in text if ch in "\n\t" or ch >= " ").strip()


def parse_text(data: bytes) -> list[dict[str, Any]]:
    text = clean(data.decode("utf-8", errors="replace"))
    if not text:
        raise ParseError("no_text", "The file contains no text.")
    return [{"page": None, "text": text[: LIMITS["max_chars"]]}]


# ── Blocks: text and Markdown ────────────────────────────────────────────────

_MD_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_MD_LIST = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$")
_MD_SEPARATOR = re.compile(r"^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?\s*$")
_MD_FENCE = re.compile(r"^\s*(```|~~~)")
_MAX_HEADING_LEVEL = 3


def _md_cells(line: str) -> list[str]:
    """The cells of one pipe-table row, outer pipes dropped."""
    cells = re.split(r"(?<!\\)\|", line.strip())
    if cells and not cells[0].strip():
        cells = cells[1:]
    if cells and not cells[-1].strip():
        cells = cells[:-1]
    return [" ".join(cell.replace("\\|", "|").split()) for cell in cells]


def _is_md_table_start(lines: list[str], index: int) -> bool:
    """A header row with a pipe, followed by a separator row (---|---)."""
    return (index + 1 < len(lines) and "|" in lines[index] and "-" in lines[index + 1]
            and "|" in lines[index + 1] and bool(_MD_SEPARATOR.match(lines[index + 1])))


def blocks_from_markdown(text: str) -> list[dict[str, Any]]:
    """Markdown into blocks: # to ### headings, pipe tables, list items, blank-line paragraphs."""
    lines = text.splitlines()
    blocks: list[dict[str, Any]] = []
    paragraph: list[str] = []
    item: list[str] | None = None
    fenced: list[str] | None = None

    def flush() -> None:
        nonlocal paragraph, item
        if paragraph:
            blocks.append({"page": None, "kind": "paragraph", "text": " ".join(paragraph)})
        if item is not None:
            blocks.append({"page": None, "kind": "list_item", "text": " ".join(item)})
        paragraph, item = [], None

    index = 0
    while index < len(lines):
        line = lines[index]
        if fenced is not None:  # a code fence is one paragraph; its lines are never read as markup
            if _MD_FENCE.match(line):
                if any(fenced):
                    blocks.append({"page": None, "kind": "paragraph", "text": "\n".join(fenced).strip()})
                fenced = None
            else:
                fenced.append(line.rstrip())
            index += 1
            continue
        if _MD_FENCE.match(line):
            flush()
            fenced = []
            index += 1
            continue
        if not line.strip():
            flush()
            index += 1
            continue
        heading = _MD_HEADING.match(line)
        if heading:
            flush()
            blocks.append({"page": None, "kind": "heading", "level": min(len(heading.group(1)), _MAX_HEADING_LEVEL),
                           "text": " ".join(heading.group(2).split())})
            index += 1
            continue
        if _is_md_table_start(lines, index):
            flush()
            rows = [_md_cells(line)]
            index += 2
            while index < len(lines) and lines[index].strip() and "|" in lines[index]:
                rows.append(_md_cells(lines[index]))
                index += 1
            blocks.append({"page": None, "kind": "table", "rows": rows})
            continue
        bullet = _MD_LIST.match(line)
        if bullet:
            flush()
            item = [" ".join(bullet.group(1).split())]
        elif item is not None:
            item.append(" ".join(line.split()))
        else:
            paragraph.append(" ".join(line.split()))
        index += 1
    if fenced:
        blocks.append({"page": None, "kind": "paragraph", "text": "\n".join(fenced).strip()})
    flush()
    return blocks


def blocks_from_text(text: str, page: int | None = None) -> list[dict[str, Any]]:
    """Blank-line paragraphs only; line breaks inside a paragraph are kept, as they always were."""
    blocks: list[dict[str, Any]] = []
    lines: list[str] = []
    for raw in [*text.splitlines(), ""]:
        line = " ".join(raw.split())
        if line:
            lines.append(line)
        elif lines:
            blocks.append({"page": page, "kind": "paragraph", "text": "\n".join(lines)})
            lines = []
    return blocks


def parse_text_blocks(content_type: str, pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Blocks for a text or Markdown upload, from the same capped text as `pages`."""
    text = pages[0]["text"]
    return blocks_from_markdown(text) if content_type == _MARKDOWN else blocks_from_text(text)


def parse_pdf(data: bytes) -> list[dict[str, Any]]:
    """Runs INSIDE the limited child process."""
    import io

    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise ParseError("encrypted", "The PDF is password-protected.")
        count = len(reader.pages)
    except ParseError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError, OSError) as exc:
        raise ParseError("unreadable", f"The PDF could not be read ({type(exc).__name__}).") from exc
    if count > LIMITS["max_pages"]:
        raise ParseError("too_many_pages", f"The PDF has {count} pages; the limit is {LIMITS['max_pages']}.")

    pages: list[dict[str, Any]] = []
    total = 0
    for number, page in enumerate(reader.pages, start=1):
        try:
            text = clean(page.extract_text() or "")
        except Exception:  # noqa: BLE001 -- one bad page must not lose the rest
            logger.warning("page %d could not be read; skipped", number)
            continue
        if not text:
            continue
        text = text[: max(0, LIMITS["max_chars"] - total)]
        total += len(text)
        pages.append({"page": number, "text": text})
        if total >= LIMITS["max_chars"]:
            break
    if not pages:
        raise ParseError("no_text", "No text was found in the PDF (a scanned PDF needs OCR, which is not supported).")
    return pages


# ── Blocks: PDF ──────────────────────────────────────────────────────────────

_LIST_MARKER = re.compile(r"^(?:•|[-*]\s|\d+[.)]\s)")
_UNMAPPED_BULLET = re.compile(r"^\(cid:\d+\)\s*")  # a bullet glyph pdfminer has no Unicode mapping for
_NUMBERED_SECTION = re.compile(r"^\d+(\.\d+)*\.?\s+\S")
_SIZE_DIGITS = 1
_MIN_TABLE_ROWS = 2     # a "table" of one row or one column is a ruled box, not a table
_MIN_TABLE_COLUMNS = 2


def _line_facts(line: dict[str, Any]) -> dict[str, Any] | None:
    """One pdfplumber text line as the facts the block rules need, or None for a blank line."""
    text = clean(_UNMAPPED_BULLET.sub("• ", " ".join(str(line.get("text") or "").split())))
    glyphs = [c for c in line.get("chars", []) if not str(c.get("text", " ")).isspace()]
    if not text or not glyphs:
        return None
    sizes = Counter(round(float(c.get("size") or 0.0), _SIZE_DIGITS) for c in glyphs)
    bold = all("bold" in str(c.get("fontname", "")).lower() for c in glyphs)
    return {"kind": "line", "top": float(line["top"]), "bottom": float(line["bottom"]), "text": text,
            "size": sizes.most_common(1)[0][0], "bold": bold, "glyphs": len(glyphs)}


def _table_rows(table: Any) -> list[list[str]]:
    """A pdfplumber table as rows of clean cell strings; None cells are empty, empty rows are dropped."""
    rows = [[clean(" ".join(str(cell).split())) if cell is not None else "" for cell in row]
            for row in table.extract()]
    return [row for row in rows if any(row)]


def _body_size(sizes: Counter) -> float | None:
    """The median font size, weighted by how many glyphs are set in each size."""
    total = sum(sizes.values())
    if not total:
        return None
    seen = 0
    for size in sorted(sizes):
        seen += sizes[size]
        if seen * 2 >= total:
            return size
    return None


def _is_heading(item: dict[str, Any], body: float | None) -> bool:
    text = item["text"]
    if len(text) > LIMITS["heading_max_chars"] or text.endswith("."):
        return False
    larger = body is not None and item["size"] >= LIMITS["heading_size_ratio"] * body
    if larger or (item["bold"] and len(text.split()) <= LIMITS["heading_bold_max_words"]):
        return True
    return bool(_NUMBERED_SECTION.match(text)) and item["bold"]


def _page_blocks(number: int, items: list[dict[str, Any]], body: float | None) -> list[dict[str, Any]]:
    """One page's lines and tables, in reading order, as blocks (heading levels are set later)."""
    blocks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None  # the paragraph or list item still collecting lines
    previous: dict[str, Any] | None = None  # the last line read, if the item before this one was a line
    heading_open = False  # the last block is a heading that a same-sized next line continues

    def close() -> None:
        nonlocal current
        if current is not None:
            current["text"] = " ".join(current.pop("parts"))
            blocks.append(current)
        current = None

    def adjacent(item: dict[str, Any]) -> bool:
        """Is this line set close enough under the previous one to be part of the same block?"""
        return (previous is not None
                and item["top"] - previous["top"] <= LIMITS["paragraph_gap_ratio"] * (previous["bottom"] - previous["top"]))

    for item in sorted(items, key=lambda i: i["top"]):
        if item["kind"] == "table":
            close()
            blocks.append({"page": number, "kind": "table", "rows": item["rows"]})
            previous, heading_open = None, False
        elif _is_heading(item, body):
            close()
            if heading_open and adjacent(item) and previous["size"] == item["size"]:
                blocks[-1]["text"] += " " + item["text"]  # a heading that wrapped onto a second line
            else:
                blocks.append({"page": number, "kind": "heading", "size": item["size"], "text": item["text"]})
            previous, heading_open = item, True
        else:
            starts_item = bool(_LIST_MARKER.match(item["text"]))
            if current is None or starts_item or not adjacent(item):
                close()
                current = {"page": number, "kind": "list_item" if starts_item else "paragraph", "parts": []}
            current["parts"].append(item["text"])
            previous, heading_open = item, False
    close()
    return blocks


def _assign_heading_levels(blocks: list[dict[str, Any]]) -> None:
    """Level = rank of the heading's font size among all heading sizes (largest = 1), capped at 3."""
    ranked = sorted({b["size"] for b in blocks if b["kind"] == "heading"}, reverse=True)
    for block in blocks:
        if block["kind"] == "heading":
            block["level"] = min(ranked.index(block.pop("size")) + 1, _MAX_HEADING_LEVEL)


def blocks_from_pages(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Paragraph blocks from flat page text: the fallback when the layout pass cannot read a page."""
    return [block for entry in pages for block in blocks_from_text(entry["text"], entry["page"])]


def _cap_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Blocks up to [library].max_chars of text in total, as `pages` is capped."""
    kept: list[dict[str, Any]] = []
    total = 0
    for block in blocks:
        size = len(block["text"]) if "text" in block else sum(len(cell) for row in block["rows"] for cell in row)
        if total + size > LIMITS["max_chars"]:
            break
        total += size
        kept.append(block)
    return kept


def parse_pdf_blocks(data: bytes, pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Layout blocks of a PDF. Runs INSIDE the limited child process, after parse_pdf succeeded.

    Never raises: a document pdfplumber cannot read, or a page it fails on, falls back
    to the pypdf text of `pages` (blank-line paragraphs).
    """
    import io

    import pdfplumber

    fallback = {entry["page"]: entry for entry in pages}
    laid_out: dict[int, list[dict[str, Any]]] = {}
    sizes: Counter = Counter()
    deadline = time.monotonic() + LIMITS["blocks_time_budget_s"]
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for number, page in enumerate(pdf.pages, start=1):
                if number not in fallback:
                    continue  # pypdf found no text on this page
                if time.monotonic() > deadline:
                    logger.warning("layout pass out of time at page %d; the rest is flat text", number)
                    break
                try:
                    tables = [(t, _table_rows(t)) for t in page.find_tables()]
                    tables = [(t, rows) for t, rows in tables if len(rows) >= _MIN_TABLE_ROWS and len(rows[0]) >= _MIN_TABLE_COLUMNS]
                    narrative = page
                    for table, _ in tables:
                        narrative = narrative.outside_bbox(table.bbox)
                    items = [{"kind": "table", "top": float(t.bbox[1]), "rows": rows} for t, rows in tables]
                    for raw in narrative.extract_text_lines(return_chars=True):
                        facts = _line_facts(raw)
                        if facts:
                            items.append(facts)
                            sizes[facts["size"]] += facts["glyphs"]
                    laid_out[number] = items
                except Exception:  # noqa: BLE001 -- one bad page must not lose the rest
                    logger.warning("page %d layout could not be read; flat text used", number)
                finally:
                    page.flush_cache()
    except Exception as exc:  # noqa: BLE001 -- the layout pass is an improvement, never a reason to refuse
        logger.warning("layout pass failed (%s); flat text used", type(exc).__name__)
        return _cap_blocks(blocks_from_pages(pages))

    body = _body_size(sizes)
    blocks: list[dict[str, Any]] = []
    for entry in pages:
        number = entry["page"]
        blocks.extend(_page_blocks(number, laid_out[number], body) if number in laid_out
                      else blocks_from_text(entry["text"], number))
    _assign_heading_levels(blocks)
    return _cap_blocks(blocks)


def _child(data: bytes, results: multiprocessing.Queue) -> None:
    try:
        memory = LIMITS["parse_memory_mb"] * _MB
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        cpu = LIMITS["parse_cpu_s"]
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    except (ValueError, OSError):
        # Fail closed: never parse a PDF without the limits (macOS, for one,
        # refuses RLIMIT_AS; the container is Linux, where it works).
        results.put({"ok": False, "error": "limits_unavailable",
                     "message": "The parser could not apply its safety limits, so the file was not read."})
        return
    try:
        pages = parse_pdf(data)
        results.put({"ok": True, "pages": pages, "blocks": parse_pdf_blocks(data, pages)})
    except ParseError as exc:
        results.put({"ok": False, "error": exc.code, "message": exc.message})
    except MemoryError:
        results.put({"ok": False, "error": "unreadable", "message": "The PDF needs more memory than allowed."})
    except Exception as exc:  # noqa: BLE001 -- report, never crash silently
        results.put({"ok": False, "error": "unreadable", "message": f"The PDF could not be read ({type(exc).__name__})."})


def parse_pdf_isolated(data: bytes) -> dict[str, Any]:
    """Parse in a fresh child process with memory, CPU and wall-clock limits."""
    context = multiprocessing.get_context("spawn")
    results: multiprocessing.Queue = context.Queue()
    child = context.Process(target=_child, args=(data, results), daemon=True)
    child.start()
    deadline = time.monotonic() + LIMITS["parse_timeout_s"]
    outcome: dict[str, Any] | None = None
    try:
        # Poll, so a child killed by its own limits (SIGKILL on memory, SIGXCPU
        # on CPU) is noticed at once instead of after the full timeout.
        while outcome is None and time.monotonic() < deadline:
            try:
                outcome = results.get(timeout=_POLL_S)
            except queue.Empty:
                if not child.is_alive():
                    try:
                        outcome = results.get(timeout=_POLL_S)
                    except queue.Empty:
                        outcome = {"ok": False, "error": "unreadable",
                                   "message": "The PDF could not be read within the parser's limits."}
        if outcome is None:
            outcome = {"ok": False, "error": "timeout", "message": "Parsing took too long and was stopped."}
    finally:
        if child.is_alive():
            child.kill()
        child.join()
    return outcome


class Handler(BaseHTTPRequestHandler):
    server_version = "statesync-parser"
    sys_version = ""

    def _send(self, status: HTTPStatus, body: dict[str, Any]) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _fail(self, status: HTTPStatus, code: str, message: str) -> None:
        self._send(status, {"ok": False, "error": code, "message": message})

    def log_message(self, fmt: str, *args: Any) -> None:  # route through logging, no client data
        logger.info("%s %s", self.command, self.path)

    def do_GET(self) -> None:  # noqa: N802 -- http.server naming
        if self.path == "/health":
            self._send(HTTPStatus.OK, {"ok": True})
        else:
            self._fail(HTTPStatus.NOT_FOUND, "not_found", "Unknown path.")

    def do_POST(self) -> None:  # noqa: N802 -- http.server naming
        if self.path != "/parse":
            self._fail(HTTPStatus.NOT_FOUND, "not_found", "Unknown path.")
            return
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if content_type not in LIMITS["content_types"]:
            self._fail(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "unsupported_type",
                       "Only PDF, plain-text and Markdown files are supported.")
            return
        try:
            length = int(self.headers.get("Content-Length") or "")
        except ValueError:
            self._fail(HTTPStatus.LENGTH_REQUIRED, "too_large", "Content-Length is required.")
            return
        if length <= 0 or length > LIMITS["max_upload_bytes"]:
            self._fail(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "too_large",
                       f"Files must be between 1 byte and {LIMITS['max_upload_bytes'] // _MB} MB.")
            return
        data = self.rfile.read(length)

        if content_type == _PDF:
            outcome = parse_pdf_isolated(data)
        else:
            try:
                pages = parse_text(data)
                outcome = {"ok": True, "pages": pages, "blocks": parse_text_blocks(content_type, pages)}
            except ParseError as exc:
                outcome = {"ok": False, "error": exc.code, "message": exc.message}

        if outcome["ok"]:
            logger.info("parsed %s: %d bytes, %d pages, %d blocks", content_type, length, len(outcome["pages"]),
                        len(outcome["blocks"]))
            self._send(HTTPStatus.OK, outcome)
        else:
            logger.info("refused %s: %d bytes, %s", content_type, length, outcome["error"])
            self._send(HTTPStatus.UNPROCESSABLE_ENTITY, outcome)


def main() -> None:
    port = int(os.environ.get("PARSER_PORT", "8095"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)  # noqa: S104 -- internal network only
    logger.info("parser listening on %d", port)
    server.serve_forever()


if __name__ == "__main__":
    main()
