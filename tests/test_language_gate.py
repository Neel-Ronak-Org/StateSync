"""
tests/test_language_gate.py
Pins the script/word language gate (orchestrator/language_gate.py) against the
golden question sets.

WHY THIS EXISTS. pipeline.language_gate = "script" skips the language model for
questions the gate calls English. That is only safe if the gate (a) sends every
Hindi/Hinglish question to the model (recall) and (b) does not waste the call
on English (false positives). The fixtures are the ground truth for both; the
marker list lives in config and is not tailored to any fixture sentence.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))

from language_gate import needs_language_model  # noqa: E402

GOLDEN = REPO_ROOT / "fixtures" / "golden"


def _load(name: str) -> dict:
    return json.loads((GOLDEN / f"{name}.json").read_text(encoding="utf-8"))


def _english_questions() -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for name in ("olist", "olist_hard", "phase_c2", "phase_e1"):
        for q in _load(name)["queries"]:
            assert q.get("lang", "en") == "en", f"{name}:{q['id']} is not English"
            found.append((f"{name}:{q['id']}", q["text"]))
    for conv in _load("multi_turn")["conversations"]:
        for turn in conv["turns"]:
            found.append((f"multi_turn:{turn['turn_id']}", turn["text"]))
    return found


def test_every_hindi_and_hinglish_question_needs_the_model():
    queries = [q for q in _load("phase_d_multilingual")["queries"] if q["lang"] in ("hi", "hinglish")]
    assert len(queries) == 40  # noqa: literals - 20 hi + 20 hinglish in the fixture
    missed = [f"{q['id']} ({q['lang']}): {q['text']}" for q in queries
              if not needs_language_model(q["text"])]
    assert not missed, f"recall {len(queries) - len(missed)}/{len(queries)}; missed:\n" + "\n".join(missed)


def test_no_english_question_needs_the_model():
    questions = _english_questions()
    assert questions, "no English questions loaded"
    flagged = [f"{qid}: {text}" for qid, text in questions if needs_language_model(text)]
    assert not flagged, (
        f"false positives {len(flagged)}/{len(questions)}; flagged:\n" + "\n".join(flagged)
    )
