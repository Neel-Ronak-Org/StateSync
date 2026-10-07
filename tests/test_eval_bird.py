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


# ── protocol v2 (docs/research/evidence_tiers_study.md v2, fixed before any held-out run) ──────
# The held-out set excludes the pilot and the cross-engine disagreements; labels v2 compare a
# SQLite gold with a PostgreSQL prediction to 5 significant digits; the answer label reads the text;
# refusal reads only the first sentence; "any data check" is the four checks and nothing else.

def test_heldout_excludes_the_pilot_and_the_cross_engine_disagreements():
    qs = [{"question_id": i} for i in (1, 2, 3, 532, 788, 900)] + [{"question_id": 3}]   # 3 duplicated, as 137/138
    assert eb.heldout_ids(qs, pilot=[2]) == [1, 3, 900]


def test_norm_v2_makes_sqlite_and_postgres_values_equal():
    from datetime import date, datetime
    assert eb.norm_v2(866.25) == eb.norm_v2(866.2500610351562)        # REAL in PostgreSQL vs double in SQLite
    assert eb.norm_v2(5) == eb.norm_v2(Decimal("5.000")) == eb.norm_v2("5")
    assert eb.norm_v2(date(2012, 8, 25)) == "2012-08-25"
    assert eb.norm_v2(datetime(2012, 8, 25, 10, 0)) == "2012-08-25 10:00:00"
    assert eb.norm_v2(None) is None and eb.norm_v2(True) is True and eb.norm_v2("Ant's") == "Ant's"
    assert eb.norm_v2(4941) != eb.norm_v2(4890)                       # a real difference stays one


def test_v2_matching_tolerates_float_storage_but_v1_is_unchanged():
    gold, pred = [("Sacha", "Harrison", 866.25)], [("Harrison", "Sacha", 866.2500610351562)]
    assert not eb.rows_match_relaxed(pred, gold)                       # v1: 6 decimals
    assert eb.rows_match_relaxed(pred, gold, norm=eb.norm_v2)


def test_refusal_reads_the_first_sentence_only():
    assert eb.refused("I can’t determine which country the oldest driver is from because the results lack ages.")
    assert eb.refused("I cannot determine the school with the lowest excellence rate.")
    assert not eb.refused("The query returned 660 rows. I can't list all of them here, so here are the first 50.")
    assert not eb.refused("No schools found. The percentage cannot be computed from an empty result.")


def test_answer_label_reads_the_gold_cells_in_the_text():
    assert eb.answer_label("Full name: Lance Stroll. Nationality: Canadian.", [("Lance", "Stroll", "Canadian")])
    assert eb.answer_label("About 44.3% of the schools qualify.", [(44.26229508196721,)])
    assert eb.answer_label("The share is 20.6%.", [(0.20566810835419985,)])           # a fraction as a percent
    assert eb.answer_label("140 patients came after 1990.", [(1590,)]) is False
    assert eb.answer_label("I can’t determine it; candidates: October Speaker, November Speaker.",
                           [("November Speaker",)]) is False                         # a refusal is not an answer
    assert eb.answer_label("anything", [(1, 2), (3, 4), (5, 6)]) is None              # more than 4 cells
    assert eb.answer_label("anything", [(None,)]) is None and eb.answer_label("anything", []) is None


def test_v2_signals():
    base = {"executed_sql": "SELECT 1", "errors": [], "number_grounding": {"unmatched": []}, "evidence": "checked",
            "answer_text": "There are 9 drivers.", "sql_rows_count": 1, "caveats": []}
    s = eb.signals(base)
    assert not any(s[k] for k in ("truncation", "null_heavy", "grounding_before_repair", "any_data_check",
                                  "empty_result", "refusal"))
    assert eb.signals({**base, "sql_rows_count": eb.MAX_ROWS})["any_data_check"]
    assert eb.signals({**base, "caveats": ["No matching data: the result is empty."]})["null_heavy"]
    # Asked before caveats were recorded: NULL-heavy comes from the re-executed rows.
    assert eb.signals({**base, "caveats": None}, pred_rows=[(None,)])["null_heavy"]
    # The tier and SQL retries are not data checks in v2.
    v = eb.signals({**base, "evidence": "unchecked", "errors": [{"category": "empty_result"}]})
    assert v["tier_unchecked"] and v["any_sql_retry"] and not v["any_data_check"]
    # Grounding before repair: the repair may have cleared the flag on the final text.
    fixed = {**base, "repair": {"outcome": "fixed", "previous_answer": "I cannot determine the total of 12,350."}}
    v = eb.signals(fixed)
    assert v["grounding_before_repair"] and v["any_data_check"] and v["refusal"]
    assert eb.signals({**base, "sql_rows_count": 0})["empty_result"]


def test_the_fanout_signal_reads_a_retry_or_its_caveat():
    base = {"executed_sql": "SELECT 1", "errors": [], "number_grounding": {"unmatched": []}, "evidence": "checked",
            "answer_text": "x", "sql_rows_count": 1, "caveats": []}
    assert not eb.signals(base)["fanout_retry"]
    assert eb.signals({**base, "errors": [{"category": "join_cardinality"}]})["fanout_retry"]
    caveat = "These figures may count some rows more than once (join fan-out)."
    assert eb.signals({**base, "caveats": [caveat]})["fanout_retry"] and eb.signals({**base, "caveats": [caveat]})["any_data_check"]


def test_norm_v2_makes_arrays_and_json_comparable():
    # Seen on the held-out run: a predicted query returned a PostgreSQL array, and the scorer crashed.
    assert eb.norm_v2([1, Decimal("2.0")]) == (1.0, 2.0) and eb.norm_v2({"b": 1, "a": 2}) == '{"a": 2, "b": 1}'
    assert not eb.rows_match([([1, 2],)], [(1,)], eb.norm_v2)
