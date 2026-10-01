"""
tests/test_parser.py
The upload parser (parser/server.py, docs/V3_PLAN.md Phase 3c slice 5).

WHY THIS EXISTS. The parser is the one place StateSync reads hostile files.
Its refusals must happen before any parsing (wrong type, no length, too
large), text must come out without the characters Postgres rejects (NUL), and
a PDF must be parsed in the limited child process with its page limit
enforced. The container's isolation (no network, read-only, non-root) is
checked live, not here.

Phase B of docs/specs/rag_v2.md adds layout blocks (headings, paragraphs, list
items, whole tables). The tests build their own PDFs with reportlab, so the
expected block sequence is known exactly: a table's cells must never also be
paragraph text, headings must be ranked by size, and a page the layout pass
cannot read must still come out as flat paragraphs rather than failing the
upload.
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
    pages = server.parse_text(b"Hello\x00 world\nline two")
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
    assert status == 200 and body["ok"] is True
    assert body["pages"] == [{"page": None, "text": "# Returns\nWithin 7 days."}]
    assert body["blocks"] == [{"page": None, "kind": "heading", "level": 1, "text": "Returns"},
                              {"page": None, "kind": "paragraph", "text": "Within 7 days."}]


# ── Layout blocks (docs/specs/rag_v2.md Phase B) ─────────────────────────────

def _layout_pdf() -> bytes:
    """Title, two headings, paragraphs, a bulleted list and a ruled table; the table is on page 1."""
    import io

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import ListFlowable, ListItem, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    title = ParagraphStyle("t", fontName="Helvetica-Bold", fontSize=20, leading=24, spaceAfter=14)
    heading = ParagraphStyle("h", fontName="Helvetica-Bold", fontSize=14, leading=18, spaceBefore=10, spaceAfter=6)
    body = ParagraphStyle("b", fontName="Helvetica", fontSize=10, leading=12, spaceAfter=8)
    story = [
        Paragraph("Seller Handbook", title),
        Paragraph("1. Definitions", heading),
        Paragraph("An order is on time when it arrives before the promised date. This sentence wraps onto a "
                  "second line because it is long enough to need one on an A4 page at ten points.", body),
        Paragraph("GMV means the sum of item prices.", body),
        ListFlowable([ListItem(Paragraph("Bronze needs 85 percent.", body)),
                      ListItem(Paragraph("Gold needs 95 percent.", body))], bulletType="bullet"),
        Paragraph("2. Levels", heading),
        Paragraph("The levels are listed below.", body),
        Table([["Level", "Orders", "Share"], ["Bronze", "1 - 49", "85%"], ["Gold", "200 or more", "95%"]],
              style=TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black)])),
        Spacer(1, 12),
        Paragraph("After the table comes one more paragraph.", body),
        PageBreak(),
        Paragraph("A second page paragraph.", body),
    ]
    out = io.BytesIO()
    SimpleDocTemplate(out, pagesize=A4).build(story)
    return out.getvalue()


@pytest.fixture(scope="module")
def layout_blocks():
    pytest.importorskip("pdfplumber")
    pytest.importorskip("reportlab")
    data = _layout_pdf()
    import io

    import pdfplumber
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        pages = [{"page": n, "text": page.extract_text() or ""} for n, page in enumerate(pdf.pages, start=1)]
    return server.parse_pdf_blocks(data, pages)


def test_a_pdf_becomes_headings_paragraphs_a_list_and_a_table_in_reading_order(layout_blocks):
    summary = [(b["page"], b["kind"], b.get("level"), b.get("text", b.get("rows"))) for b in layout_blocks]
    assert summary[0] == (1, "heading", 1, "Seller Handbook")
    assert summary[1] == (1, "heading", 2, "1. Definitions")
    assert summary[2][:3] == (1, "paragraph", None) and summary[2][3].startswith("An order is on time")
    assert "second line" in summary[2][3] and "\n" not in summary[2][3]  # a wrapped paragraph is one block
    assert summary[3] == (1, "paragraph", None, "GMV means the sum of item prices.")
    assert summary[4] == (1, "list_item", None, "• Bronze needs 85 percent.")  # reportlab's bullet has no Unicode mapping
    assert summary[5] == (1, "list_item", None, "• Gold needs 95 percent.")
    assert summary[6] == (1, "heading", 2, "2. Levels")
    assert summary[7] == (1, "paragraph", None, "The levels are listed below.")
    assert summary[8] == (1, "table", None, [["Level", "Orders", "Share"], ["Bronze", "1 - 49", "85%"],
                                              ["Gold", "200 or more", "95%"]])
    assert summary[9] == (1, "paragraph", None, "After the table comes one more paragraph.")
    assert summary[10] == (2, "paragraph", None, "A second page paragraph.")
    assert len(summary) == 11


def test_table_cells_never_appear_in_paragraph_text(layout_blocks):
    prose = " ".join(b["text"] for b in layout_blocks if b["kind"] != "table")
    for cell in ("Orders", "200 or more", "1 - 49"):
        assert cell not in prose


def test_heading_levels_follow_font_size_rank(layout_blocks):
    levels = {b["text"]: b["level"] for b in layout_blocks if b["kind"] == "heading"}
    assert levels == {"Seller Handbook": 1, "1. Definitions": 2, "2. Levels": 2}


def test_a_pdf_with_no_layout_text_falls_back_to_page_paragraphs(monkeypatch):
    pytest.importorskip("pdfplumber")
    pages = [{"page": 1, "text": "First paragraph.\n\nSecond paragraph."}, {"page": 2, "text": "Third."}]
    monkeypatch.setitem(server.LIMITS, "blocks_time_budget_s", -1)  # out of time before the first page
    blocks = server.parse_pdf_blocks(_minimal_pdf(2), pages)
    assert blocks == [{"page": 1, "kind": "paragraph", "text": "First paragraph."},
                      {"page": 1, "kind": "paragraph", "text": "Second paragraph."},
                      {"page": 2, "kind": "paragraph", "text": "Third."}]


def test_unreadable_layout_never_refuses_the_upload():
    pytest.importorskip("pdfplumber")
    pages = [{"page": 1, "text": "Some text."}]
    assert server.parse_pdf_blocks(b"%PDF-1.4\n" + bytes(range(256)) * 20, pages) == [
        {"page": 1, "kind": "paragraph", "text": "Some text."}]


def test_blocks_stop_at_max_chars(monkeypatch):
    monkeypatch.setitem(server.LIMITS, "max_chars", 12)
    blocks = [{"page": None, "kind": "paragraph", "text": "abcdefgh"}, {"page": None, "kind": "paragraph", "text": "ijklmnop"},
              {"page": None, "kind": "table", "rows": [["a", "b"]]}]
    assert server._cap_blocks(blocks) == blocks[:1]


def test_markdown_headings_tables_lists_and_paragraphs():
    text = ("# Title\nIntro line one\nline two.\n\n## Rates\n\n| Level | Share |\n|---|:--:|\n| Bronze | 85% |\n"
            "| Gold | 95% |\n\n- first item\n  continued\n* second item\n1. numbered\n\n#### Deep\nEnd.")
    assert server.blocks_from_markdown(text) == [
        {"page": None, "kind": "heading", "level": 1, "text": "Title"},
        {"page": None, "kind": "paragraph", "text": "Intro line one line two."},
        {"page": None, "kind": "heading", "level": 2, "text": "Rates"},
        {"page": None, "kind": "table", "rows": [["Level", "Share"], ["Bronze", "85%"], ["Gold", "95%"]]},
        {"page": None, "kind": "list_item", "text": "first item continued"},
        {"page": None, "kind": "list_item", "text": "second item"},
        {"page": None, "kind": "list_item", "text": "numbered"},
        {"page": None, "kind": "heading", "level": 3, "text": "Deep"},
        {"page": None, "kind": "paragraph", "text": "End."},
    ]


def test_markdown_code_fences_are_not_read_as_markup():
    blocks = server.blocks_from_markdown("Before\n```\n# not a heading\n| a | b |\n```\nAfter")
    assert [b["kind"] for b in blocks] == ["paragraph", "paragraph", "paragraph"]
    assert blocks[1]["text"] == "# not a heading\n| a | b |"


def test_plain_text_is_blank_line_paragraphs_only():
    text = "# not a heading\n- not a list\nsecond line\n\n\nNext paragraph."
    assert server.blocks_from_text(text) == [
        {"page": None, "kind": "paragraph", "text": "# not a heading\n- not a list\nsecond line"},
        {"page": None, "kind": "paragraph", "text": "Next paragraph."}]


def test_a_markdown_table_needs_its_separator_row():
    blocks = server.blocks_from_markdown("a | b\nc | d")
    assert [b["kind"] for b in blocks] == ["paragraph"]
