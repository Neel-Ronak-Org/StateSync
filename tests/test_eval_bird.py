"""
tests/test_eval_bird.py

WHY THIS EXISTS (scripts/eval_bird.py; docs/research/evidence_tiers_study.md). The pilot's sample,
question text, correctness rule and signals are fixed before any run. Pinned here:
- the sample is stratified in BIRD's proportions and the same for the same seed;
- the hint is appended as "Hint: ..." only when BIRD gives one;
- correct = the SETS of result rows are equal, floats to 6 decimals, order ignored;
- connected-dataset qualifiers (cn_<id>.) are stripped before the scorer runs the SQL;
- the judge's p_correct is parsed and clamped; malformed replies give None.
"""

from __future__ import annotations

import collections
import sys
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import eval_bird as eb  # noqa: E402


def _qs():
    out, i = [], 0
    for diff, count in (("simple", 148), ("moderate", 250), ("challenging", 102)):
        for _ in range(count):
            out.append({"question_id": i, "difficulty": diff, "db_id": f"db{i % 11}"})
            i += 1
    return out


def test_the_sample_is_stratified_and_reproducible():
    ids = eb.stratified_sample(_qs(), 100, eb.SAMPLE_SEED)
    by_id = {q["question_id"]: q for q in _qs()}
    assert len(ids) == 100 and len(set(ids)) == 100
    assert collections.Counter(by_id[i]["difficulty"] for i in ids) == {"simple": 30, "moderate": 50, "challenging": 20}
    assert ids == eb.stratified_sample(_qs(), 100, eb.SAMPLE_SEED)


def test_question_text_appends_the_hint_only_when_given():
    assert eb.question_text({"question": "How many?", "evidence": "x refers to y"}) == "How many?\nHint: x refers to y"
    assert eb.question_text({"question": "How many?", "evidence": ""}) == "How many?"


def test_rows_match_is_set_equality_with_rounded_floats():
    assert eb.rows_match([(1, 2.0000000001), (3, 4.0)], [(3, 4), (1, Decimal("2.0"))])
    assert eb.rows_match([(1,), (1,)], [(1,)])           # BIRD's rule compares sets
    assert not eb.rows_match([(1,)], [(2,)])
    assert not eb.rows_match(None, [(1,)])


def test_connected_qualifiers_are_stripped():
    assert eb.unqualify('SELECT * FROM cn_0123456789abcdef.cards c JOIN "cn_0123456789abcdef".sets s ON true') == \
        'SELECT * FROM cards c JOIN sets s ON true'


def test_judge_scores_are_parsed_and_clamped():
    assert eb.parse_p('{"p_correct": 0.82, "reason": "ok"}') == 0.82
    assert eb.parse_p('```json\n{"p_correct": 1.4}\n```') == 1.0
    assert eb.parse_p("I think it is right") is None


def test_relaxed_match_ignores_column_order_and_extra_columns():
    gold = [("2125 Jefferson Avenue", "Berkeley", "CA", "94703")]
    swapped = [("2125 Jefferson Avenue", "Berkeley", "94703", "CA")]
    extra = [("Some School", "2125 Jefferson Avenue", "Berkeley", "CA", "94703")]
    assert not eb.rows_match(swapped, gold) and eb.rows_match_relaxed(swapped, gold)
    assert eb.rows_match_relaxed(extra, gold)
    assert not eb.rows_match_relaxed([("2607 Myrtle Street", "Oakland", "CA", "94607")], gold)
    assert not eb.rows_match_relaxed([("2125 Jefferson Avenue", "Berkeley")], gold)   # a gold column missing
