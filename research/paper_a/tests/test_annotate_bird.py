"""
tests/test_annotate_bird.py
Blind annotation (scripts/annotate_bird.py; protocol v2 §5).

WHY THIS EXISTS. Annotations must not lean on the signals under test, so a worksheet item carries
the question, the gold and predicted SQL and rows and the text before repair, and nothing else:
no signal, judge verdict, self-consistency or automatic label. Cohen's kappa is pinned against a
hand-computed value.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import annotate_bird as ab  # noqa: E402


def test_a_worksheet_item_is_blind_to_the_signals():
    answer = {"question_asked": "How many?", "executed_sql": "SELECT 1", "answer_text": "after repair",
              "repair": {"previous_answer": "before repair"}, "number_grounding": {"unmatched": ["5"]},
              "self_consistency": {"risk": 0.8}, "errors": [{"category": "join_cardinality"}], "caveats": ["x"]}
    item = ab.blind_item(7, "db", answer, "SELECT 2", [(2,)], [(1,)])
    assert set(item) == {"question_id", "db_id", "question", "gold_sql", "gold_rows", "gold_row_count", "predicted_sql",
                         "predicted_rows", "predicted_row_count", "answer_text", "annotation", "note"}
    assert item["answer_text"] == "before repair" and item["gold_rows"] == [["2"]] and item["annotation"] is None


def test_kappa():
    assert ab.kappa(["y", "y", "n", "n"], ["y", "n", "n", "n"]) == 0.5     # observed 0.75, chance 0.5
    assert ab.kappa(["a", "b"], ["a", "b"]) == 1.0 and ab.kappa([], []) is None
