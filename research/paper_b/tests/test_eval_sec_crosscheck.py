"""
tests/test_eval_sec_crosscheck.py

WHY THIS EXISTS (docs/specs/sec_crosscheck.md §4-§5, pre-registered). The scoring rule decides
every number in the study, so it is pinned before any answer exists:
- units follow the question's own instruction ("USD millions", "USD billions", percent);
- the FIRST number of an answer counts, after unit words ("1.6 billion" in a millions question
  is 1600); a leading minus or accounting parentheses make it negative;
- correct = within 1% relative, or equal after rounding to the gold's decimals;
- an answer whose first sentence holds several numbers is flagged for blind adjudication;
- Wilson intervals and Fisher's exact test match textbook values.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import eval_sec_crosscheck as ev  # noqa: E402


def test_the_question_sets_the_unit():
    assert ev.question_unit("What is the FY2018 capex (in USD millions) for 3M?") == "millions"
    assert ev.question_unit("what is the year end FY2018 net PPNE for 3M? Answer in USD billions.") == "billions"
    assert ev.question_unit("Answer in units of percents and round to one decimal place.") == "percent"
    assert ev.question_unit("What is the FY2019 fixed asset turnover ratio?") == "plain"


def test_the_first_number_counts_in_the_questions_unit():
    assert ev.extract("3M's FY2018 capital expenditure was $1,577 million.", "millions") == (1577.0, False)
    assert ev.extract("About 1.6 billion dollars.", "millions")[0] == 1600.0
    assert ev.extract("Net PP&E was $8.7 billion at year end.", "billions")[0] == 8.7
    assert ev.extract("Growth was 30.8%.", "percent")[0] == 30.8
    assert ev.extract("ROA was (0.02).", "plain")[0] == -0.02
    assert ev.extract("ROA was -0.02.", "plain")[0] == -0.02
    assert ev.extract("I could not find that.", "plain") == (None, False)
    # names and dates are not figures
    assert ev.extract("In 2018, 3M spent $1,577 million on capex.", "millions") == (1577.0, False)
    assert ev.extract("10X Genomics reported revenue of $1.6B.", "millions")[0] == 1600.0


def test_several_numbers_in_the_first_sentence_are_flagged():
    value, ambiguous = ev.extract("Revenue was 6,489 and PP&E averaged 267, so the ratio is 24.26.", "plain")
    assert value == 6489.0 and ambiguous is True


def test_correct_means_one_percent_or_equal_at_the_golds_decimals():
    assert ev.matches(1577.0, 1577.0, 2)
    assert ev.matches(24.3, 24.26, 2)          # within 1%
    assert not ev.matches(25.0, 24.26, 2)
    assert ev.matches(0.0149, 0.01, 2)         # equal after rounding to 2 decimals
    assert not ev.matches(None, 1.0, 0)
    assert ev.decimals("$1577.00") == 2 and ev.decimals("1.9%") == 1 and ev.decimals("6.25") == 2


def test_gold_strings_parse_in_their_question_units():
    v, u = ev.numbers("$1577.00")[0]
    assert ev.in_unit(v, u, "millions") == 1577.0
    v, u = ev.numbers("1.9%")[0]
    assert (v, u) == (1.9, "%")


def test_wilson_and_fisher_match_textbook_values():
    lo, hi = ev.wilson(5, 10)
    assert abs(lo - 0.2366) < 1e-3 and abs(hi - 0.7634) < 1e-3
    assert ev.wilson(0, 0) is None
    assert abs(ev.fisher_two_sided(3, 1, 1, 3) - 0.4857) < 1e-3
    assert ev.fisher_two_sided(5, 0, 0, 5) < 0.01
