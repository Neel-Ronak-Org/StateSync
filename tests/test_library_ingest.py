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
"""

from __future__ import annotations

import asyncio
import math
import os
import sys
from pathlib import Path

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


class FlakyEmbedCall(FakeCall):
    """add_chunks answers "unavailable" (embedding failed, nothing stored) the first `failures` times."""

    def __init__(self, failures: int) -> None:
        super().__init__()
        self.failures = failures

    async def __call__(self, action: str, **params):
        self.calls.append((action, params))
        if action == "add_chunks" and self.failures > 0:
            self.failures -= 1
            return {"ok": False, "error": "unavailable", "message": "The embedding service failed."}
        return {"ok": True}


def _no_sleep(monkeypatch) -> list[float]:
    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr(li.asyncio, "sleep", fake_sleep)
    return delays


def test_an_unavailable_embedding_is_retried_and_the_document_still_becomes_ready(monkeypatch):
    """WHY: on 2026-10-01 a CPU Ollama slowed about 4x for minutes during a long FinanceBench ingest
    and every document then failed on its first slow batch. "unavailable" means nothing was
    stored, so the same batch is sent again after a growing pause instead of failing at once."""
    delays = _no_sleep(monkeypatch)
    fake = FlakyEmbedCall(failures=int(cfg.library.embed_retries))
    asyncio.run(li.ingest(fake, "up_1", [{"page": 1, "text": _sentences("p", 3)}]))
    adds = [c for c in fake.calls if c[0] == "add_chunks"]
    assert len(adds) == int(cfg.library.embed_retries) + 1
    assert all(c[1] == adds[0][1] for c in adds), "a retry resends exactly the same batch"
    assert delays == [float(cfg.library.embed_retry_backoff_s) * n for n in range(1, int(cfg.library.embed_retries) + 1)]
    assert fake.calls[-1] == ("finish_document", {"document_id": "up_1", "status": "ready"})


def test_an_embedding_that_stays_unavailable_fails_the_document_after_the_retries(monkeypatch):
    """WHY: retries are bounded; an Ollama that stays down must still end in "failed", not hang."""
    _no_sleep(monkeypatch)
    fake = FlakyEmbedCall(failures=int(cfg.library.embed_retries) + 1)
    asyncio.run(li.ingest(fake, "up_1", [{"page": 1, "text": _sentences("p", 3)}]))
    assert [c[0] for c in fake.calls].count("add_chunks") == int(cfg.library.embed_retries) + 1
    assert fake.calls[-1][1] == {"document_id": "up_1", "status": "failed", "error": "The embedding service failed."}


def test_a_refusal_other_than_unavailable_is_never_retried(monkeypatch):
    """WHY: only "unavailable" promises that nothing was stored; resending after any other answer
    could duplicate chunks or repeat a refusal that will not change."""
    delays = _no_sleep(monkeypatch)
    fake = FakeCall(fail_on="add_chunks")
    asyncio.run(li.ingest(fake, "up_1", _many_pages()))
    assert [c[0] for c in fake.calls] == ["add_chunks", "finish_document"] and delays == []


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
