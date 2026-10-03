"""
tests/test_doc_definitions.py
Definitions found in uploaded documents (orchestrator/doc_definitions.py).

WHY THIS EXISTS. The feature's promise is that nothing reaches the owner
unless the document really says it: a proposal whose quote is not in the cited
passage, or does not contain its term, must be dropped. And the model must
never read the whole library: passages are chosen in code, within a budget,
by their definitional wording. The job must not overwrite the owner's own
definitions, and must say what it did (found / dropped) without raising.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))

import doc_definitions as dd  # noqa: E402
from workspace_setup import new_job  # noqa: E402

DEF = ("An order is On time when it is delivered on or before the estimated delivery date. "
       "Late orders are the rest.")
GLOSSARY = "GMV: the sum of item prices of all delivered orders, before refunds."
PROSE = "Our company was founded in Curitiba and ships furniture across Brazil."


def _chunk(n: int, text: str, page: int | None = 1) -> dict:
    return {"chunk_id": f"up_doc_c{n:05d}", "document_id": "up_doc", "title": "Ops handbook", "page": page,
            "text": text}


def test_scores_definitional_wording_only():
    assert dd.definitional_score(PROSE) == 0
    assert dd.definitional_score(GLOSSARY) >= 1
    assert dd.definitional_score('"Active seller" means a seller with a sale in the last 90 days.') >= 1
    assert dd.definitional_score(DEF) >= 1          # "is On time when ..."


def test_selection_keeps_the_best_within_budget_in_reading_order():
    chunks = [_chunk(0, PROSE), _chunk(1, GLOSSARY), _chunk(2, "Refunds refer to money returned. " + PROSE),
              _chunk(3, '"Churn" means a customer with no order in 12 months. Churn is defined as that.')]
    chosen = dd.select_passages(chunks, max_chars=10_000, min_score=1)
    assert [c["chunk_id"] for c in chosen] == ["up_doc_c00001", "up_doc_c00002", "up_doc_c00003"]
    # With room for one passage, the most definitional one wins.
    tight = dd.select_passages(chunks, max_chars=len(chunks[3]["text"]), min_score=1)
    assert [c["chunk_id"] for c in tight] == ["up_doc_c00003"]


def test_batches_respect_the_size():
    passages = [_chunk(i, "x" * 400) for i in range(5)]
    assert [len(b) for b in dd.batch_passages(passages, 1000)] == [2, 2, 1]
    assert [len(b) for b in dd.batch_passages([_chunk(0, "x" * 5000)], 1000)] == [1]


def test_batch_labels_carry_title_and_page():
    text = dd.format_batch([_chunk(0, DEF, page=4), _chunk(1, GLOSSARY, page=None)])
    assert text.startswith("[P1] Ops handbook, page 4\n") and "\n\n[P2] Ops handbook\nGMV:" in text


def _reply(*items: dict) -> str:
    return json.dumps({"definitions": list(items)})


def _parse(reply: str, batch: list[dict]):
    return dd.parse_proposals(reply, batch, term_max=60, definition_max=400, quote_max=400)


def test_a_grounded_proposal_is_kept_with_its_citation():
    batch = [_chunk(0, DEF, page=4)]
    kept, dropped = _parse(_reply({"term": "On time", "definition": "Delivered by the estimate.", "passage": "P1",
                                   "quote": "An order is on time when it is delivered on or before the "
                                            "estimated delivery date."}), batch)
    assert dropped == 0
    assert kept == [{"term": "On time", "definition": "Delivered by the estimate.", "source": "document",
                     "citation": {"document_id": "up_doc", "chunk_id": "up_doc_c00000", "page": 4,
                                  "title": "Ops handbook",
                                  "quote": "An order is on time when it is delivered on or before the "
                                           "estimated delivery date."}}]


def test_ungrounded_proposals_are_dropped():
    batch = [_chunk(0, DEF), _chunk(1, GLOSSARY)]
    kept, dropped = _parse(_reply(
        # the quote is not in the passage (invented)
        {"term": "On time", "definition": "d", "passage": "P1", "quote": "An order is on time within 3 days."},
        # the quote is real but does not contain the term
        {"term": "Delay", "definition": "d", "passage": "P1", "quote": "Late orders are the rest."},
        # a passage that does not exist
        {"term": "GMV", "definition": "d", "passage": "P7", "quote": GLOSSARY},
        # the right quote cited against the wrong passage
        {"term": "GMV", "definition": "d", "passage": "P1", "quote": GLOSSARY},
        # missing fields
        {"term": "Refund", "definition": "d"},
        "not an object",
    ), batch)
    assert kept == [] and dropped == 6


def test_quotes_match_despite_typography_and_duplicates_are_dropped():
    batch = [_chunk(0, "“Net revenue” — the price minus refunds.")]
    kept, dropped = _parse(_reply(
        {"term": "Net revenue", "definition": "Price minus refunds.", "passage": "[P1]",
         "quote": '"Net revenue" - the price minus refunds.'},
        {"term": "net revenue", "definition": "again", "passage": "P1",
         "quote": '"Net revenue" - the price minus refunds.'},
    ), batch)
    assert [k["term"] for k in kept] == ["Net revenue"] and dropped == 1


def test_an_unreadable_reply_is_a_reason():
    assert isinstance(_parse("Sure! Here are the terms", [_chunk(0, DEF)]), str)
    assert isinstance(_parse('{"terms": []}', [_chunk(0, DEF)]), str)
    assert _parse('```json\n{"definitions": []}\n```', [_chunk(0, DEF)]) == ([], 0)


class _Harness:
    def __init__(self, chunks: list[dict], replies: list[str], existing: list[dict] | None = None):
        self.chunks, self.replies, self.existing = chunks, list(replies), existing or []
        self.saved: list[list[dict]] = []
        self.prompts: list[str] = []

    async def library(self, action, **params):
        assert action == "workspace_chunks"
        return {"ok": True, "chunks": self.chunks}

    async def manage(self, action, **params):
        if action == "definitions":
            return {"ok": True, "definitions": self.existing}
        assert action == "save_definitions"
        self.saved.append(params["items"])
        return {"ok": True}

    async def complete(self, system, user, max_tokens):
        self.prompts.append(user)
        return self.replies.pop(0)


def _run(h: _Harness):
    job = new_job("mine", "p", "ws")
    asyncio.run(dd.run_mining(job, h.manage, h.library, h.complete, ""))
    return job


def test_the_job_saves_grounded_drafts_and_skips_the_owners_terms():
    reply = _reply(
        {"term": "On time", "definition": "Delivered by the estimate.", "passage": "P1",
         "quote": "An order is On time when it is delivered on or before the estimated delivery date."},
        {"term": "GMV", "definition": "Sum of prices.", "passage": "P2", "quote": GLOSSARY},
        {"term": "Refund", "definition": "Made up.", "passage": "P2", "quote": "Refunds are always instant."})
    h = _Harness([_chunk(0, DEF), _chunk(1, GLOSSARY), _chunk(2, PROSE)], [reply],
                 existing=[{"term": "gmv", "source": "owner"}])
    job = _run(h)
    assert job.state == "done" and job.found == 1 and job.dropped == 1 and job.finished_at
    assert [i["term"] for i in h.saved[0]] == ["On time"]
    assert h.saved[0][0]["source"] == "document"
    assert PROSE not in h.prompts[0]           # a passage with no definitional wording is never sent


def test_the_job_explains_empty_cases_without_calling_the_model():
    job = _run(_Harness([], []))
    assert job.state == "failed" and "no ready documents" in job.error
    job = _run(_Harness([_chunk(0, PROSE)], []))
    assert job.state == "done" and "No passage" in job.error


def test_the_job_fails_cleanly_when_every_reply_is_unreadable():
    job = _run(_Harness([_chunk(0, DEF)], ["no json here"]))
    assert job.state == "failed" and job.finished_at
