"""
tests/test_context_carry.py
Follow-up questions with no model call (orchestrator/context_carry.py).

A follow-up ("And in fiscal 2022?", "Has it reported any legal battles?") needs the conversation. The router
detects one in code, carries the company and year the question leaves out (the company may be named only in
the previous ANSWER), and gives the writers one context line and the document search the carried words. The
failures that matter are quiet ones: a company taken from the wrong turn, or context added to a question that
is not a follow-up. Everything here is a pure function.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))

import context_carry  # noqa: E402
from context_carry import carry, entities, is_follow_up, search_suffix, years  # noqa: E402


NAMES = ["cocacola", "pepsico", "generalmills", "3m", "nike"]
LISTED = ["COCACOLA", "PEPSICO", "GENERAL MILLS", "3M", "NIKE"]     # workspace_names keeps the titles' own words
FILINGS = [{"document_id": "d_ko", "title": "COCACOLA 2022 10K"}, {"document_id": "d_pep", "title": "PEPSICO 2022 10K"},
           {"document_id": "d_gm", "title": "GENERAL MILLS 2022 10K"}, {"document_id": "d_mmm", "title": "3M 2022 10K"},
           {"document_id": "d_nike", "title": "NIKE 2023 10K"}]

# The spec's example: "it" in turn 2 is PepsiCo, named only in turn 1's answer.
Q1 = "Which of Coca-Cola, PepsiCo and General Mills had the highest revenue in fiscal 2022?"
A1 = "PepsiCo had the highest revenue in fiscal 2022, at $86.4 billion, ahead of General Mills and Coca-Cola."
Q2 = "Has it reported any legal battles?"
PEPSICO_TURN = {"turn_index": 1, "rewritten_query": Q1, "route": "sql", "answer_text": A1, "answered": True}
MMM_TURN = {"turn_index": 1, "rewritten_query": "What was 3M's capex in fiscal 2018?", "route": "sql",
            "answer_text": "3M's capex in fiscal 2018 was $1,577 million.", "answered": True}



# ── entities ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text,names,expected", [
    ("What was Coca-Cola's revenue?", ["cocacola"], ["cocacola"]),
    ("COCACOLA vs coca cola", ["cocacola"], ["cocacola"]),                        # reduced on both sides, each once
    ("JPMORGAN CHASE & CO net income?", ["jpmorgan"], ["jpmorgan"]),               # the title name inside a longer mention
    ("Which is bigger, PepsiCo or Coca-Cola?", NAMES, ["pepsico", "cocacola"]),    # order of appearance, not of names
    ("What was 3M's FY2018 capex?", NAMES, ["3m"]),                                # a short name equals a whole word
    ("What about 3M?", ["3m"], ["3m"]),
    ("Kokomo is a city", ["ko"], []),                                              # ... and is not inside a word
    ("AESOP Corp", ["aes"], []),
    ("AES revenue", ["aes"], ["aes"]),
    ("Morgan Stanley revenue", ["morgan", "morganstanley"], ["morganstanley"]),    # the longest match uses its span up
    ("Morgan and Morgan Stanley", ["morgan", "morganstanley"], ["morgan", "morganstanley"]),
    ("Which company grew fastest?", NAMES, []),
    ("", NAMES, []),
    ("Coca-Cola", [], []),
    ("Coca-Cola", ["", "---"], []),                                                # a name with no letter or digit
])
def test_entities(text, names, expected):
    assert entities(text, names) == expected


def test_entities_returns_the_names_as_given():
    assert entities("Tell me about Coca-Cola", ["Coca-Cola"]) == ["Coca-Cola"]


# ── years ───────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text,expected", [
    ("And in fiscal 2022?", ["2022"]),
    ("revenue in 2021 and 2022", ["2021", "2022"]),
    ("FY22", ["2022"]), ("fy2022", ["2022"]), ("fiscal 22", ["2022"]), ("Fiscal 95", ["1995"]),
    ("Q3 FY23 against FY22", ["2023", "2022"]),
    ("2022, FY2022 and fiscal 22", ["2022"]),                                       # each once
    ("1989 and 2040", []),                                                          # out of range
    ("FY2050 and FY45", []),
    ("12022 and 20223", []),                                                        # not a four-digit word
    ("$2,023 million, 1,577 units", []),
    ("", []),
])
def test_years(text, expected):
    assert years(text) == expected


# ── is_follow_up ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("question", [
    "And in fiscal 2022?", "Has it reported any legal battles?", "Which ones?", "Who are its primary customers?",
    "What about Nike?", "How about 2021?", "Is that company profitable?", "And for 2023?", "Did they grow?",
    "Show those again", "the same for 2021", "Also its margin?", "Then what?",
])
def test_an_anaphor_makes_a_follow_up(question):
    follow_up, reason = is_follow_up(question, PEPSICO_TURN, NAMES)
    assert follow_up is True and reason.startswith("anaphor")


def test_the_reason_names_the_anaphor():
    assert is_follow_up("Has it reported any legal battles?", PEPSICO_TURN, NAMES) == (True, 'anaphor "it"')
    assert is_follow_up("AND  IN 2022?", PEPSICO_TURN, NAMES) == (True, 'anaphor "and in"')


def test_an_anaphor_is_a_whole_word():
    turn = {"rewritten_query": "How many orders?", "answer_text": "There were 12."}       # no company in it
    for question in ("What is the digital transformation spend?", "Which items sold best?", "What is the sitemap?",
                     "Is the athlete list complete?", "What were the cothen results?"):
        assert is_follow_up(question, turn, NAMES)[0] is False


def test_a_first_turn_is_never_a_follow_up():
    assert is_follow_up("And in fiscal 2022?", None, NAMES) == (False, context_carry.REASON_FIRST_TURN)
    assert is_follow_up("Has it reported any legal battles?", {}, NAMES)[0] is False


def test_naming_a_different_company_is_not_a_follow_up():
    assert is_follow_up("What was Nike's revenue in fiscal 2023?", MMM_TURN, NAMES) == (
        False, context_carry.REASON_NAMES_COMPANY)


def test_no_company_named_while_the_previous_turn_named_one_is_a_follow_up():
    follow_up, reason = is_follow_up("What were the operating expenses?", MMM_TURN, NAMES)
    assert follow_up is True and "3m" in reason
    # named only in the previous answer
    turn = {"rewritten_query": "Which company is largest?", "answer_text": "Nike is the largest."}
    assert is_follow_up("What were the operating expenses?", turn, NAMES)[0] is True


def test_no_company_in_either_turn_and_no_anaphor_is_not_a_follow_up():
    turn = {"rewritten_query": "How many orders?", "answer_text": "There were 12."}
    assert is_follow_up("How many returns?", turn, NAMES) == (False, context_carry.REASON_NO_SIGNAL)


# ── carry ───────────────────────────────────────────────────────────────────
def test_the_company_comes_from_the_previous_answer_before_the_previous_question_and_the_year_from_the_question():
    result = carry(Q2, PEPSICO_TURN, NAMES)
    assert (result.follow_up, result.company, result.year) == (True, "pepsico", "2022")
    assert (result.company_from, result.year_from) == ("previous answer", "previous question")
    assert result.line == (f'Earlier in this conversation: "{Q1}"; its answer began: "{A1}". '
                           "Carried from it: company pepsico; year 2022.")


def test_the_company_falls_back_to_the_previous_questions_first_when_the_answer_names_none():
    turn = {"rewritten_query": Q1, "answer_text": "It was about 86 billion."}
    assert carry(Q2, turn, NAMES).company == "cocacola"


def test_the_questions_own_company_and_year_win_and_are_not_listed_as_carried():
    result = carry("And in fiscal 2023, what about Nike?", PEPSICO_TURN, NAMES)
    assert (result.company, result.year, result.company_from, result.year_from) == ("nike", "2023", "question", "question")
    assert result.line == f'Earlier in this conversation: "{Q1}"; its answer began: "{A1}".'      # nothing carried


def test_only_the_missing_slot_is_carried():
    both_own = carry("And in fiscal 2021, how did PepsiCo's margin move?", PEPSICO_TURN, NAMES)
    assert "Carried" not in both_own.line
    company_only = carry("And in fiscal 2021?", PEPSICO_TURN, NAMES)
    assert company_only.line.endswith(" Carried from it: company pepsico.")
    year_missing = carry("And what about Nike?", {"rewritten_query": "What is revenue?", "answer_text": "Big."}, NAMES)
    assert year_missing.year is None and "year" not in year_missing.line


def test_the_year_falls_back_to_the_previous_answer():
    turn = {"rewritten_query": "What was 3M's capex?", "answer_text": "In fiscal 2019 it was $1,577 million."}
    result = carry("And its revenue?", turn, NAMES)
    assert (result.year, result.year_from) == ("2019", "previous answer")
    assert result.line.endswith(" Carried from it: company 3m; year 2019.")


def test_the_line_quotes_the_first_answer_chars_of_the_answer_exactly():
    answer = "x" * 250
    line = carry("Which ones?", {"rewritten_query": "q", "answer_text": answer}, NAMES).line
    assert line == f'Earlier in this conversation: "q"; its answer began: "{"x" * 200}".'
    assert carry("Which ones?", {"rewritten_query": "q", "answer_text": ""}, NAMES).line == (
        'Earlier in this conversation: "q"; its answer began: "".')


def test_carry_with_no_previous_turn_has_no_line_and_no_slots_from_it():
    result = carry("What was Nike's revenue in fiscal 2023?", None, NAMES)
    assert (result.follow_up, result.line, result.company, result.year) == (False, "", None, None)


def test_a_turn_with_question_and_answer_keys_is_read_too():
    result = carry(Q2, {"question": Q1, "answer": A1}, NAMES)
    assert result.company == "pepsico" and Q1 in result.line


def test_the_trace_and_the_carried_state_shapes():
    result = carry(Q2, PEPSICO_TURN, NAMES)
    assert result.trace() == {"follow_up": True, "reason": 'anaphor "it"', "company": "pepsico", "year": "2022"}
    assert result.carried() == {"company": "pepsico", "year": "2022", "reason": 'anaphor "it"'}


# ── what the answering steps read ───────────────────────────────────────────
def test_question_context_is_empty_without_a_line():
    assert context_carry.question_context({}) == "" and context_carry.question_context({"conversation_context": None}) == ""
    assert context_carry.question_context({"conversation_context": "L"}) == "\n\nL"


def test_the_search_suffix_adds_the_missing_slots_only():
    state = {"conversation_context": "L", "carried": {"company": "pepsico", "year": "2022", "reason": "r"}}
    assert search_suffix(state, "Has it reported any legal battles?") == " pepsico 2022"
    assert search_suffix(state, "Has PepsiCo reported any legal battles?") == " 2022"          # the company is there
    assert search_suffix(state, "Legal battles in fiscal 2022 or FY22?") == " pepsico"
    assert search_suffix(state, "PepsiCo legal battles in 2022") == ""
    assert search_suffix({**state, "carried": {"company": None, "year": "2022", "reason": "r"}}, "q") == " 2022"
    assert search_suffix({**state, "carried": {"company": "pepsico", "year": None, "reason": "r"}}, "q") == " pepsico"


def test_the_search_suffix_is_empty_without_a_line_or_without_carried_slots():
    assert search_suffix({"carried": {"company": "pepsico", "year": "2022"}}, "q") == ""
    assert search_suffix({"conversation_context": "L"}, "q") == ""
    assert search_suffix({}, "q") == ""



def test_the_search_text_carries_the_titles_own_words():
    """The carried company goes into the search text as the title writes it, not reduced."""
    state = {"conversation_context": "Earlier in this conversation: ...", "carried": {"company": "JOHNSON JOHNSON", "year": "2022"}}
    assert context_carry.search_suffix(state, "Who are its primary customers?") == " JOHNSON JOHNSON 2022"


def test_a_company_named_two_turns_back_is_carried():
    names = ["3M", "NIKE"]
    earlier = [{"question": "What was 3M's capital expenditure in fiscal 2018?", "answer": "$1577.00"}]
    previous = {"question": "And in fiscal 2022?", "answer": "2022, 1749000000"}
    c = context_carry.carry("What drove its operating margin change that year?", previous, names, earlier=earlier)
    assert (c.follow_up, c.company, c.year) == (True, "3M", "2022")
    assert "company 3M" in c.line and "year 2022" in c.line
    # without the earlier turns, nothing names a company
    assert context_carry.carry("What drove its operating margin change that year?", previous, names).company is None
