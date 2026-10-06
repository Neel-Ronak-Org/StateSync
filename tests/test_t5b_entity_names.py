"""
tests/test_t5b_entity_names.py
Pins T5b (docs/specs/partd_tasks/T5b_entity_names.md): the dependent hybrid's SQL half is asked for entity
names, and a document title's name that occurs inside a longer entity matches it.

WHY THIS EXISTS. With pipeline.hybrid_entities = "on" the documents half narrowed to the right filings
only when the SQL rows held a company name that a title contains ("Coca-Cola" -> "COCACOLA 2022 10K"). It
failed when the SQL half returned only a ticker ("KO"), and when the entity was longer than the title's
name ("JPMORGAN CHASE & CO" against "JPMORGAN 2022 10K"). T5b asks for names and matches both ways.

Pinned here:
1. title_name: the words before the first year word, reduced to lower-case letters and digits.
2. match_titles: the old rule is kept; a title name inside the entity matches when it is long enough or
   equals the entity; order and duplicates as before.
3. hybrid_plan_node: the sentence is appended once, for both split sources, only with the flag on and the
   documents dependent on the SQL half; flag off is exactly as before.
4. The new config key ships at 4.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))
if "psycopg_pool" not in sys.modules:
    try:
        import psycopg_pool  # noqa: F401
    except ImportError:
        import unittest.mock
        sys.modules["psycopg_pool"] = unittest.mock.MagicMock()

from config_loader import cfg  # noqa: E402
from nodes import unstructured_knowledge as uk  # noqa: E402

RUN = asyncio.run
SENTENCE = "Return each entity's full name as well as any code or identifier."


def _titles(entities, titles, **kw):
    docs = [{"document_id": f"d{i}", "title": t} for i, t in enumerate(titles)]
    return [d["title"] for d in uk.match_titles(entities, docs, **kw)]


# ── title names ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("title,name", [
    ("COCACOLA 2022 10K", "cocacola"),
    ("JOHNSON JOHNSON 2023 8K dated 2023-05-01", "johnsonjohnson"),
    ("3M 2022 10K", "3m"),
    ("Procter & Gamble Annual Report", "proctergambleannualreport"),   # no year word: all the words
    ("ACME 2023Q2 10Q", "acme"),                                        # a word that starts with a year
    ("ACME 10K 2022", "acme10k"),                                       # "10K" is not a year
    ("ACME 123 2022", "acme123"),                                       # three digits is not a year
    ("2022 10K", ""),                                                   # the year comes first: no name
    ("", ""),
])
def test_title_names(title, name):
    assert uk.title_name(title) == name


# ── matching ────────────────────────────────────────────────────────────────
def test_an_entity_longer_than_the_titles_name_matches():
    assert _titles(["JPMORGAN CHASE & CO"], ["JPMORGAN 2022 10K", "COCACOLA 2022 10K"]) == ["JPMORGAN 2022 10K"]


def test_equality_matches_a_short_title_name():
    assert _titles(["3M"], ["3M 2022 10K", "COCACOLA 2022 10K"]) == ["3M 2022 10K"]
    assert _titles(["3m"], ["3M 2022 10K"]) == ["3M 2022 10K"]


def test_a_title_name_shorter_than_the_minimum_does_not_match_by_containment():
    assert _titles(["AESOP CORP"], ["AES 2022 10K"]) == []
    assert _titles(["AES"], ["AES 2022 10K"]) == ["AES 2022 10K"]          # equality still does
    assert _titles(["AESOP CORP"], ["AES 2022 10K"], title_name_min_chars=3) == ["AES 2022 10K"]   # the minimum rules


def test_the_title_name_minimum_is_inclusive():
    assert _titles(["ABCD HOLDINGS"], ["ABCD 2022 10K"]) == ["ABCD 2022 10K"]      # four characters, the shipped minimum


def test_the_old_rule_still_matches():
    assert _titles(["Coca-Cola"], ["COCACOLA 2022 10K", "PEPSICO 2022 10K"]) == ["COCACOLA 2022 10K"]
    assert _titles(["2022"], ["COCACOLA 2022 10K", "3M 2018 10K"]) == ["COCACOLA 2022 10K"]


def test_order_and_duplicates_follow_the_documents_given():
    titles = ["PEPSICO 2022 10K", "COCACOLA 2022 10K", "PEPSICO 2023 10K"]
    # three entities name the first document (by the old rule and the new one): it is listed once, in the documents' order
    assert _titles(["Coca-Cola Company", "PepsiCo", "PEPSICO 2022 10K"], titles) == titles
    assert _titles(["PepsiCo Inc", "PepsiCo Inc"], titles) == [titles[0], titles[2]]


def test_nothing_matches_an_empty_or_symbol_entity_or_a_title_without_a_name():
    assert _titles(["---", " ", ""], ["ACME 2022 10K"]) == []
    assert _titles(["acme"], ["2022 10K"]) == []                  # an empty title name never matches by containment
    assert uk.match_titles(["acme"], [{"document_id": "x", "title": None}, {"document_id": "y"}]) == []


def test_the_minimum_ships_at_four():
    assert int(cfg.hybrid.title_name_min_chars) == 4


# ── the plan node ───────────────────────────────────────────────────────────
def _plan(monkeypatch, flag, depends, subtasks, *, decomposed=("sql?", "docs?"), decomposition=True):
    async def decompose(query_text, target_schema):
        return decomposed[0], decomposed[1], 3, 4

    monkeypatch.setattr(uk, "_decompose", decompose)
    flags = {"retrieval.hybrid_decomposition.enabled": decomposition}
    if flag is not None:
        flags["pipeline.hybrid_entities"] = flag
    state = {"normalized_query": "the question", "query_id": "q1", "flags_snapshot": flags}
    if depends is not None:
        state["docs_depend_on_sql"] = depends
    if subtasks is not None:
        state["subtasks"] = subtasks
    return RUN(uk.hybrid_plan_node(state))


def test_the_sentence_is_appended_once_after_one_space_for_a_front_door_split(monkeypatch):
    update = _plan(monkeypatch, "on", True, ["Which company had the lowest margin?", "What drove it?"])
    assert update["subtasks"] == [f"Which company had the lowest margin? {SENTENCE}", "What drove it?"]
    assert update["metrics"]["llm_calls"] == 0                        # no decomposer call, as before


def test_the_sentence_is_appended_for_a_decomposer_split(monkeypatch):
    update = _plan(monkeypatch, "on", True, None, decomposed=("lowest margin?", "what drove it?"))
    assert update["subtasks"] == [f"lowest margin? {SENTENCE}", "what drove it?"]
    assert update["metrics"]["llm_calls"] == 1


def test_the_sentence_is_appended_when_decomposition_is_off_too(monkeypatch):
    update = _plan(monkeypatch, "on", True, None, decomposition=False)
    assert update["subtasks"] == [f"the question {SENTENCE}", "the question"]


def test_the_sentence_is_not_appended_twice(monkeypatch):
    already = f"Which company had the lowest margin? {SENTENCE}"
    update = _plan(monkeypatch, "on", True, [already, "What drove it?"])
    assert update["subtasks"] == [already, "What drove it?"]
    again = _plan(monkeypatch, "on", True, update["subtasks"])        # feeding the result back changes nothing
    assert again["subtasks"] == update["subtasks"]


@pytest.mark.parametrize("flag,depends", [
    ("off", True), (None, True),            # flag off or absent
    ("on", False), ("on", None),            # the documents do not depend on the SQL half
    ("llm", True), ("", True),              # only "on" turns it on
])
def test_the_sentence_is_not_appended_otherwise(monkeypatch, flag, depends):
    update = _plan(monkeypatch, flag, depends, ["sql part", "docs part"])
    assert update["subtasks"] == ["sql part", "docs part"]
    decomposed = _plan(monkeypatch, flag, depends, None, decomposed=("sql part", "docs part"))
    assert decomposed["subtasks"] == ["sql part", "docs part"]
