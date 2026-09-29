"""
parser/server.py
The upload parser (docs/V3_PLAN.md Phase 3c slice 5): turns an uploaded file
into plain text, page by page. Nothing else.

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
                200 {"ok": true, "pages": [{"page": 1, "text": "..."}, ...]}
                    (text files have one entry with "page": null)
                4xx {"ok": false, "error": "<code>", "message": "..."}
                    codes: too_large, unsupported_type, too_many_pages,
                    encrypted, unreadable, timeout, no_text

Only the Python standard library and pypdf are used.
"""

from __future__ import annotations

import json
import logging
import multiprocessing
import os
import queue
import resource
import tomllib
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("parser")

_CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/app/config"))
LIMITS: dict[str, Any] = tomllib.loads((_CONFIG_DIR / "statesync.toml").read_text())["library"]
_MB = 1024 * 1024
_PDF = "application/pdf"


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


def _child(data: bytes, results: multiprocessing.Queue) -> None:
    memory = LIMITS["parse_memory_mb"] * _MB
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    cpu = LIMITS["parse_cpu_s"]
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
    try:
        results.put({"ok": True, "pages": parse_pdf(data)})
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
    try:
        outcome = results.get(timeout=LIMITS["parse_timeout_s"])
    except queue.Empty:
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
                outcome = {"ok": True, "pages": parse_text(data)}
            except ParseError as exc:
                outcome = {"ok": False, "error": exc.code, "message": exc.message}

        if outcome["ok"]:
            logger.info("parsed %s: %d bytes, %d pages", content_type, length, len(outcome["pages"]))
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
