"""
tests/test_library_ingest.py
The chunker and the background ingest job (orchestrator/library_ingest.py).

WHY THIS EXISTS. A chunk is what retrieval returns and what an answer cites, so
the chunker's promises are load-bearing: a chunk never spans two pages (the
citation would name the wrong one), never exceeds the embedding model's budget,
overlaps its neighbour on the same page (so a sentence cut at a boundary
survives whole), and drops page-number-sized fragments. `ingest` must leave a
document ready or failed and must never raise, because it runs as a background
task nobody awaits.

The layout chunker (docs/specs/rag_v2.md Phase C) adds promises of its own that
retrieval and citations rely on: a chunk never mixes two sections or two pages,
overlap never crosses either, a table is one chunk (or row groups that each
repeat the header, so a cell is never separated from its column names), and
every chunk opens with an exact breadcrumb.
"""

from __future__ import annotations

import asyncio
import math
import os
import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))

import library_ingest as li  # noqa: E402
from config_loader import cfg  # noqa: E402

CHUNK, OVERLAP, MIN = 400, 80, 40


def _sentences(prefix: str, count: int) -> str:
    return " ".join(f"{prefix} sentence number {i} says something useful about suppliers." for i in range(count))


def test_chunks_never_cross_pages_and_ordinals_are_contiguous():
    pages = [{"page": 1, "text": _sentences("alpha", 30)}, {"page": 2, "text": _sentences("beta", 30)}]
    chunks = li.chunk_pages(pages, CHUNK, OVERLAP, MIN)
    assert [c["ordinal"] for c in chunks] == list(range(len(chunks)))
    assert {c["page"] for c in chunks} == {1, 2}
    for chunk in chunks:
        assert ("alpha" in chunk["text"]) != ("beta" in chunk["text"])
        assert ("alpha" in chunk["text"]) == (chunk["page"] == 1)
    assert [c["page"] for c in chunks] == sorted(c["page"] for c in chunks)


def test_every_chunk_fits_the_budget():
    text = "\n\n".join(_sentences(f"p{i}", 9) for i in range(20))
    for chunk in li.chunk_pages([{"page": 1, "text": text}], CHUNK, OVERLAP, MIN):
        assert len(chunk["text"]) <= CHUNK


def test_consecutive_chunks_on_a_page_overlap_and_pages_do_not():
    pages = [{"page": 1, "text": _sentences("alpha", 40)}, {"page": 2, "text": _sentences("beta", 40)}]
    chunks = li.chunk_pages(pages, CHUNK, OVERLAP, MIN)
    by_page = {p: [c for c in chunks if c["page"] == p] for p in (1, 2)}
    for group in by_page.values():
        assert len(group) > 2
        for prev, nxt in zip(group, group[1:]):
            head = nxt["text"].split("\n\n")[0]
            assert 0 < len(head) <= OVERLAP
            assert prev["text"].rstrip().endswith(head)
    first_of_page_2 = by_page[2][0]["text"]
    assert "alpha" not in first_of_page_2 and "\n\n" not in first_of_page_2.split("sentence")[0]
    assert first_of_page_2.startswith("beta")


def test_short_fragments_are_dropped():
    pages = [{"page": 1, "text": "12"}, {"page": 2, "text": "Header\n\n" + _sentences("real", 2)}]
    chunks = li.chunk_pages(pages, CHUNK, OVERLAP, MIN)
    assert len(chunks) == 1 and chunks[0]["page"] == 2 and chunks[0]["ordinal"] == 0
    assert li.chunk_pages([{"page": 1, "text": "   \n\n  "}], CHUNK, OVERLAP, MIN) == []


def test_a_paragraph_with_no_spaces_is_hard_cut():
    chunks = li.chunk_pages([{"page": 1, "text": "x" * 5000}], CHUNK, OVERLAP, MIN)
    assert len(chunks) > 5000 // CHUNK
    assert all(len(c["text"]) <= CHUNK for c in chunks)
    assert sum(len(c["text"]) for c in chunks) == 5000


def test_text_files_keep_a_null_page_and_whitespace_is_collapsed():
    chunks = li.chunk_pages([{"page": None, "text": "Payment   terms\tare   net 30 days from the invoice date, always."}],
                            CHUNK, OVERLAP, MIN)
    assert chunks == [{"ordinal": 0, "page": None,
                       "text": "Payment terms are net 30 days from the invoice date, always."}]


def test_safe_filename_and_title():
    assert li.safe_filename("../../etc/passwd") == "passwd"
    assert li.safe_filename("C:\\Users\\me\\report.pdf") == "report.pdf"
    assert li.safe_filename("bad\x00na\x07me\n.txt") == "bad" + "na" + "me.txt"
    assert li.safe_filename("") == "document" and li.safe_filename("..") == "document"
    assert len(li.safe_filename("a" * 1000)) == int(cfg.library.title_max_chars)
    assert li.title_from_filename("Q3_sales-report.pdf") == "Q3 sales report"
    assert li.title_from_filename("___.pdf") == "Untitled document"


class FakeCall:
    def __init__(self, fail_on: str | None = None, raise_on: str | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.fail_on, self.raise_on = fail_on, raise_on

    async def __call__(self, action: str, **params):
        self.calls.append((action, params))
        if action == self.raise_on:
            raise RuntimeError("boom")
        if action == self.fail_on:
            return {"ok": False, "error": "invalid", "message": "The embedding service failed."}
        return {"ok": True}


def _many_pages() -> list[dict]:
    return [{"page": n, "text": _sentences(f"page{n}", 25)} for n in range(1, 9)]


def test_ingest_sends_batches_then_finishes_ready():
    fake = FakeCall()
    asyncio.run(li.ingest(fake, "up_1", _many_pages()))
    total = len(li.chunk_pages(_many_pages(), int(cfg.library.chunk_chars), int(cfg.library.chunk_overlap_chars),
                               int(cfg.library.chunk_min_chars)))
    batch = int(cfg.library.embed_batch)
    adds = [c for c in fake.calls if c[0] == "add_chunks"]
    assert len(adds) == math.ceil(total / batch) and all(len(c[1]["chunks"]) <= batch for c in adds)
    assert fake.calls[-1] == ("finish_document", {"document_id": "up_1", "status": "ready"})
    assert [c[0] for c in fake.calls].count("finish_document") == 1


def test_ingest_failure_result_finishes_failed_without_raising():
    fake = FakeCall(fail_on="add_chunks")
    asyncio.run(li.ingest(fake, "up_1", _many_pages()))
    assert [c[0] for c in fake.calls] == ["add_chunks", "finish_document"]
    assert fake.calls[-1][1] == {"document_id": "up_1", "status": "failed", "error": "The embedding service failed."}


def test_ingest_exception_finishes_failed_without_raising():
    fake = FakeCall(raise_on="add_chunks")
    asyncio.run(li.ingest(fake, "up_1", _many_pages()))
    assert fake.calls[-1][0] == "finish_document" and fake.calls[-1][1]["status"] == "failed"

    async def always_raise(action, **params):
        raise RuntimeError("down")
    asyncio.run(li.ingest(always_raise, "up_1", _many_pages()))  # even the failed-marker call raising is swallowed


def test_ingest_tasks_keep_references_until_done():
    async def run():
        tasks = li.IngestTasks()
        gate = asyncio.Event()

        async def waiter():
            await gate.wait()
        tasks.start(waiter())
        assert len(tasks) == 1
        gate.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert len(tasks) == 0
    asyncio.run(run())


# ── Layout chunker (docs/specs/rag_v2.md Phase C) ────────────────────────────

_LIBRARY_KEYS = ("chunk_chars", "chunk_overlap_chars", "chunk_min_chars", "chunk_max_chars", "chunker",
                 "layout_chunk_chars", "layout_overlap_chars", "table_chunk_max_chars", "table_context_max_chars",
                 "section_max_chars", "embed_batch")


def _config(**overrides) -> SimpleNamespace:
    """The real [library] values with some overridden, for small budgets in tests."""
    values = {key: getattr(cfg.library, key) for key in _LIBRARY_KEYS} | overrides
    return SimpleNamespace(library=SimpleNamespace(**values))


LAYOUT = _config(layout_chunk_chars=400, layout_overlap_chars=80, chunk_min_chars=20, table_chunk_max_chars=200,
                 table_context_max_chars=60)
TITLE = "Seller handbook"


def _h(level: int, text: str, page: int = 1) -> dict:
    return {"page": page, "kind": "heading", "level": level, "text": text}


def _p(text: str, page: int = 1) -> dict:
    return {"page": page, "kind": "paragraph", "text": text}


def _t(rows: list[list[str]], page: int = 1) -> dict:
    return {"page": page, "kind": "table", "rows": rows}


def test_chunks_never_mix_sections_and_open_with_an_exact_breadcrumb():
    blocks = [_h(1, "Seller Handbook"), _h(2, "1. Purpose"), _p("Purpose text about measuring sellers well."),
              _h(2, "2. Definitions"), _h(3, "2.1 On time"), _p("On time means delivered before the estimate."),
              _h(3, "2.2 Late"), _p("Late means delivered after the estimate."),
              _h(2, "3. Levels"), _p("Levels text about bronze silver and gold sellers.")]
    chunks = li.chunk_blocks(blocks, TITLE, LAYOUT)
    assert [(c["section"], c["text"]) for c in chunks] == [
        ("1. Purpose", "Seller handbook › 1. Purpose\n\nPurpose text about measuring sellers well."),
        ("2. Definitions › 2.1 On time",
         "Seller handbook › 2. Definitions › 2.1 On time\n\nOn time means delivered before the estimate."),
        ("2. Definitions › 2.2 Late",
         "Seller handbook › 2. Definitions › 2.2 Late\n\nLate means delivered after the estimate."),
        ("3. Levels", "Seller handbook › 3. Levels\n\nLevels text about bronze silver and gold sellers."),
    ]
    assert [c["ordinal"] for c in chunks] == [0, 1, 2, 3] and {c["kind"] for c in chunks} == {"text"}


def test_a_heading_repeating_the_title_is_left_out_and_unheaded_text_has_a_title_only_breadcrumb():
    chunks = li.chunk_blocks([_p("Text before any heading, long enough."), _h(1, "SELLER  handbook"),
                              _p("Text under the title heading, long enough.")], TITLE, LAYOUT)
    assert [(c["section"], c["text"]) for c in chunks] == [
        (None, "Seller handbook\n\nText before any heading, long enough."),
        (None, "Seller handbook\n\nText under the title heading, long enough.")]


def test_chunks_never_cross_pages_and_a_section_continues_with_the_same_path():
    blocks = [_h(1, "Seller Handbook"), _h(2, "Returns"), _p("First page return text, long enough.", 1),
              _p("Second page return text, long enough.", 2), _h(2, "Refunds", 2),
              _p("Refund text on the second page, ok.", 2)]
    chunks = li.chunk_blocks(blocks, TITLE, LAYOUT)
    assert [(c["page"], c["section"]) for c in chunks] == [(1, "Returns"), (2, "Returns"), (2, "Refunds")]
    assert "Second" not in chunks[0]["text"] and "First" not in chunks[1]["text"]
    assert chunks[1]["text"].startswith("Seller handbook › Returns\n\nSecond page")


def test_overlap_only_inside_one_section_and_page():
    long_a = _sentences("alpha", 30)
    long_b = _sentences("beta", 30)
    blocks = [_h(2, "A"), _p(long_a, 1), _p(long_a, 2), _h(2, "B"), _p(long_b, 2)]
    chunks = li.chunk_blocks(blocks, TITLE, LAYOUT)
    runs: dict[tuple, list[dict]] = {}
    for chunk in chunks:
        runs.setdefault((chunk["section"], chunk["page"]), []).append(chunk)
    assert set(runs) == {("A", 1), ("A", 2), ("B", 2)}
    for group in runs.values():
        assert len(group) > 2
        assert group[0]["text"].count("\n\n") == 1  # the first chunk of a run has a breadcrumb but no overlap
        for previous, following in zip(group, group[1:]):
            head = following["text"].split("\n\n")[1]
            assert 0 < len(head) <= 80 and previous["text"].rstrip().endswith(head)
    assert all("alpha" not in c["text"] for c in runs[("B", 2)])


def test_a_table_is_one_markdown_chunk_with_its_context_paragraph():
    rows = [["Level", "Orders", "Share"], ["Bronze", "1 - 49", "85%"], ["Gold", "200 or more", "95%"]]
    blocks = [_h(1, "Seller Handbook"), _h(2, "3. Levels"), _p("The levels are below."), _t(rows)]
    chunks = li.chunk_blocks(blocks, TITLE, LAYOUT)
    assert [c["kind"] for c in chunks] == ["text", "table"]
    assert chunks[1]["text"] == ("Seller handbook › 3. Levels\n\nThe levels are below.\n\n"
                                 "| Level | Orders | Share |\n|---|---|---|\n| Bronze | 1 - 49 | 85% |\n"
                                 "| Gold | 200 or more | 95% |")
    assert chunks[1]["section"] == "3. Levels" and chunks[1]["page"] == 1


def test_the_context_paragraph_must_be_short_adjacent_and_on_the_same_page():
    rows = [["a", "b"], ["c", "d"]]
    too_long = _p("This paragraph is much longer than the context limit allows for a table.")
    for blocks in ([too_long, _t(rows)], [_p("Short intro on page one.", 1), _t(rows, 2)],
                   [_p("Short intro before a heading."), _h(2, "Next"), _t(rows)]):
        table = [c for c in li.chunk_blocks(blocks, TITLE, LAYOUT) if c["kind"] == "table"][0]
        assert table["text"].split("\n\n")[-2].startswith("Seller handbook")


def test_a_long_table_is_split_by_rows_with_the_header_repeated_and_nothing_lost():
    rows = [["Region", "Days"]] + [[f"Region number {i}", str(i)] for i in range(20)]
    chunks = li.chunk_blocks([_h(2, "Rates"), _t(rows)], TITLE, LAYOUT)
    assert len(chunks) > 2 and {c["kind"] for c in chunks} == {"table"}
    seen = []
    for chunk in chunks:
        body = chunk["text"].split("\n\n", 1)[1]
        lines = body.split("\n")
        assert lines[:2] == ["| Region | Days |", "|---|---|"]
        assert len(body) <= 200
        seen += lines[2:]
    assert seen == [f"| Region number {i} | {i} |" for i in range(20)]


def test_table_cells_are_escaped_and_ragged_rows_padded():
    chunks = li.chunk_blocks([_t([["a", "b|c"], ["only one"]])], TITLE, LAYOUT)
    assert chunks[0]["text"] == "Seller handbook\n\n| a | b\\|c |\n|---|---|\n| only one |  |"


def test_tiny_fragments_merge_into_the_previous_chunk_of_the_same_section_and_page_or_are_dropped():
    almost_full = ("word " * 79).strip() + ". Ok yes."  # a 395-character sentence, then a 7-character remainder
    chunks = li.chunk_blocks([_h(2, "A"), _p(almost_full)], TITLE, LAYOUT)
    assert len(chunks) == 1 and chunks[0]["text"].count("\n\n") == 2
    assert chunks[0]["text"].split("\n\n")[-1] == "Ok yes."
    # no previous chunk, a different page, a table before it: dropped
    assert li.chunk_blocks([_p("12")], TITLE, LAYOUT) == []
    on_another_page = li.chunk_blocks([_h(2, "A"), _p("A paragraph that is long enough to stand alone.", 1),
                                       _p("13", 2)], TITLE, LAYOUT)
    assert len(on_another_page) == 1 and "13" not in on_another_page[0]["text"]
    after_table = li.chunk_blocks([_t([["a", "b"], ["c", "d"]]), _p("14")], TITLE, LAYOUT)
    assert [c["kind"] for c in after_table] == ["table"]


def test_list_items_stay_together_and_long_paragraphs_are_cut_at_sentences():
    blocks = [{"page": 1, "kind": "list_item", "text": "first item here, long enough"},
              {"page": 1, "kind": "list_item", "text": "• second item here, long enough"},
              _p(_sentences("gamma", 20))]
    chunks = li.chunk_blocks(blocks, TITLE, LAYOUT)
    assert chunks[0]["text"].startswith("Seller handbook\n\n- first item here, long enough\n• second item here")
    assert len(chunks) > 2
    for chunk in chunks:
        assert len(chunk["text"].split("\n\n", 1)[1]) <= 400 + 80 + 2


def test_the_section_is_clipped_to_the_mcp_limit_and_malformed_blocks_are_ignored():
    config = _config(section_max_chars=10)
    chunks = li.chunk_blocks([_h(2, "A very long heading indeed"), "junk", {"kind": "table", "rows": "x"},
                              {"kind": "paragraph", "text": 5}, _p("Real paragraph text that is long enough.")],
                             TITLE, config)
    assert len(chunks) == 1 and chunks[0]["section"] == "A very lon"
    assert li.chunk_blocks([], TITLE, LAYOUT) == [] and li.chunk_blocks(None, TITLE, LAYOUT) == []


def test_every_chunk_fits_the_mcp_limit():
    config = _config(chunk_max_chars=300)
    rows = [["h", "h"], ["x" * 1000, "y"]]
    assert all(len(c["text"]) <= 300 for c in li.chunk_blocks([_t(rows)], TITLE, config))


def test_make_chunks_uses_layout_only_when_configured_and_blocks_exist(monkeypatch):
    pages = [{"page": 1, "text": "Flat page text that is long enough to be a chunk."}]
    blocks = [_h(2, "Heading"), _p("Block text that is long enough to be a chunk.")]
    monkeypatch.setattr(li, "cfg", _config(chunker="layout"))
    assert li.make_chunks(pages, blocks, TITLE)[0]["kind"] == "text"
    assert li.make_chunks(pages, blocks, TITLE)[0]["section"] == "Heading"
    assert "section" not in li.make_chunks(pages, None, TITLE)[0]          # no blocks: flat pages
    assert "section" not in li.make_chunks(pages, [_p("x")], TITLE)[0]     # blocks that yield nothing: flat pages
    monkeypatch.setattr(li, "cfg", _config(chunker="paragraph"))
    assert "section" not in li.make_chunks(pages, blocks, TITLE)[0]


def test_ingest_passes_blocks_and_title_to_the_layout_chunker(monkeypatch):
    monkeypatch.setattr(li, "cfg", _config(chunker="layout"))
    fake = FakeCall()
    blocks = [_h(2, "Heading"), _p("Block text that is long enough to be a chunk.")]
    asyncio.run(li.ingest(fake, "up_1", [{"page": 1, "text": "flat"}], blocks, TITLE))
    sent = fake.calls[0][1]["chunks"]
    assert sent == [{"ordinal": 0, "page": 1, "kind": "text", "section": "Heading",
                     "text": "Seller handbook › Heading\n\nBlock text that is long enough to be a chunk."}]
    assert fake.calls[-1] == ("finish_document", {"document_id": "up_1", "status": "ready"})
