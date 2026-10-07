"""
tests/test_repair_loop.py

WHY THIS EXISTS (orchestrator/repair_loop.py; study RQ4/H4, owner 2026-10-03). One repair when
figure grounding flags an answer. Pinned here:
- off, a passing answer, or a documents-only answer → no repair, no extra call;
- "evidence" names the untraced figures; "plain" does not (the study's control);
- a repaired answer that passes is used and recorded "fixed"; one that still fails is NOT
  used (the original stays, with its caveat) and recorded "still_flagged";
- never more than one synthesizer call.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))

import repair_loop as rl  # noqa: E402

ROWS = [{"orders": 99441, "delivered": 96478}]
STATE = {"answer_text": "96,478 of 99,441 orders were delivered; 12,350 arrived late.", "sql_rows": ROWS,
         "sql_columns": ["orders", "delivered"], "raw_query": "How many orders were delivered?", "retrievals": []}


def _synth(reply, seen):
    async def synthesize(state):
        seen.append(state)
        return {"answer_text": reply}
    return synthesize


def test_off_or_clean_answers_make_no_call():
    seen = []
    assert asyncio.run(rl.repair(STATE, "off", _synth("x", seen))) == (None, None)
    clean = {**STATE, "answer_text": "96,478 of 99,441 orders were delivered."}
    assert asyncio.run(rl.repair(clean, "evidence", _synth("x", seen))) == (None, None)
    docs_only = {**STATE, "sql_columns": []}
    assert asyncio.run(rl.repair(docs_only, "evidence", _synth("x", seen))) == (None, None)
    assert seen == []


def test_evidence_names_the_figures_and_a_passing_repair_is_used():
    seen = []
    update, record = asyncio.run(rl.repair(STATE, "evidence", _synth("96,478 of 99,441 orders were delivered.", seen)))
    assert len(seen) == 1 and "12,350" in seen[0]["repair_note"] and seen[0]["previous_answer"] == STATE["answer_text"]
    assert update == {"answer_text": "96,478 of 99,441 orders were delivered."}
    assert record["outcome"] == "fixed" and record["unmatched_before"] == ["12,350"]


def test_plain_withholds_the_evidence_and_a_failing_repair_is_not_used():
    seen = []
    update, record = asyncio.run(rl.repair(STATE, "plain", _synth("12,350 orders arrived late.", seen)))
    assert len(seen) == 1 and "12,350" not in seen[0]["repair_note"]
    assert update is None and record["outcome"] == "still_flagged" and record["unmatched_after"] == ["12,350"]
