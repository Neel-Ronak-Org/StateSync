"""
tests/test_parser.py
The upload parser (parser/server.py, docs/V3_PLAN.md Phase 3c slice 5).

WHY THIS EXISTS. The parser is the one place StateSync reads hostile files.
Its refusals must happen before any parsing (wrong type, no length, too
large), text must come out without the characters Postgres rejects (NUL), and
a PDF must be parsed in the limited child process with its page limit
enforced. The container's isolation (no network, read-only, non-root) is
checked live, not here.
"""

from __future__ import annotations

import http.client
import json
import os
import sys
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))
sys.path.insert(0, str(REPO_ROOT / "parser"))

import server  # noqa: E402

PDF = REPO_ROOT / "fixtures" / "documents" / "supplier_agreement_S001.pdf"


def test_clean_drops_nul_and_control_characters_but_keeps_lines():
    assert server.clean("a\x00b\x07c\n\td ") == "abc\n\td"


def test_text_files_become_one_page_without_a_number():
    pages = server.parse_text("Hello\x00 world\nline two".encode())
    assert pages == [{"page": None, "text": "Hello world\nline two"}]


def test_empty_text_is_refused():
    with pytest.raises(server.ParseError) as err:
        server.parse_text(b"   \n ")
    assert err.value.code == "no_text"


def test_text_is_capped_at_max_chars(monkeypatch):
    monkeypatch.setitem(server.LIMITS, "max_chars", 5)
    assert server.parse_text(b"abcdefgh")[0]["text"] == "abcde"


def _minimal_pdf(pages: int) -> bytes:
    objs = ["<< /Type /Catalog /Pages 2 0 R >>",
            "<< /Type /Pages /Kids [" + " ".join(f"{i + 3} 0 R" for i in range(pages)) + f"] /Count {pages} >>"]
    objs += ["<< /Type /Page /Parent 2 0 R /MediaBox [0 0 10 10] >>"] * pages
    out = b"%PDF-1.4\n"
    offsets = []
    for i, obj in enumerate(objs):
        offsets.append(len(out))
        out += f"{i + 1} 0 obj {obj} endobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer << /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return out


def test_pdf_page_limit_and_blank_pdfs():
    pytest.importorskip("pypdf")
    with pytest.raises(server.ParseError) as err:
        server.parse_pdf(_minimal_pdf(server.LIMITS["max_pages"] + 1))
    assert err.value.code == "too_many_pages"
    with pytest.raises(server.ParseError) as err:
        server.parse_pdf(_minimal_pdf(1))
    assert err.value.code == "no_text"


def test_garbage_is_unreadable_not_a_crash():
    pytest.importorskip("pypdf")
    with pytest.raises(server.ParseError) as err:
        server.parse_pdf(b"%PDF-1.4\n" + bytes(range(256)) * 20)
    assert err.value.code in ("unreadable", "no_text")


def test_a_real_pdf_is_parsed_in_the_limited_child_or_not_at_all():
    pytest.importorskip("pypdf")
    outcome = server.parse_pdf_isolated(PDF.read_bytes())
    if sys.platform.startswith("linux"):
        assert outcome["ok"] is True
        assert outcome["pages"][0]["page"] == 1
        assert "Supplier Agreement" in outcome["pages"][0]["text"]
    else:
        # macOS refuses the address-space limit: the parser must fail closed, quickly.
        assert outcome == {"ok": False, "error": "limits_unavailable",
                           "message": "The parser could not apply its safety limits, so the file was not read."}


@pytest.fixture
def running_server():
    httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1]
    httpd.shutdown()


def _post(port: int, body: bytes, headers: dict[str, str]) -> tuple[int, dict]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    conn.putrequest("POST", "/parse", skip_accept_encoding=True)
    for name, value in headers.items():
        conn.putheader(name, value)
    conn.endheaders()
    if body:
        conn.send(body)
    response = conn.getresponse()
    return response.status, json.loads(response.read())


def test_refusals_happen_before_parsing(running_server, monkeypatch):
    status, body = _post(running_server, b"x", {"Content-Type": "image/png", "Content-Length": "1"})
    assert (status, body["error"]) == (415, "unsupported_type")
    status, body = _post(running_server, b"", {"Content-Type": "text/plain"})
    assert (status, body["error"]) == (411, "too_large")
    monkeypatch.setitem(server.LIMITS, "max_upload_bytes", 4)
    status, body = _post(running_server, b"", {"Content-Type": "text/plain", "Content-Length": "5"})
    assert (status, body["error"]) == (413, "too_large")


def test_a_text_upload_round_trips(running_server):
    status, body = _post(running_server, b"# Returns\nWithin 7 days.",
                         {"Content-Type": "text/markdown; charset=utf-8", "Content-Length": "24"})
    assert status == 200 and body == {"ok": True, "pages": [{"page": None, "text": "# Returns\nWithin 7 days."}]}
