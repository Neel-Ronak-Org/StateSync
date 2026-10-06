"""
tests/test_t4d_context_carry.py
Pins T4d (docs/specs/partd_tasks/T4d_context_carry.md): pipeline.context_carry = "on", multi-turn context
with no extra model call.

WHY THIS EXISTS. A follow-up ("And in fiscal 2022?", "Has it reported any legal battles?") needs the
conversation, and the front-door arms that do not rewrite the question have no rewriter call to supply it.
Instead the front door detects a follow-up deterministically, carries the company and year the question
leaves out (the company may be named only in the previous ANSWER), and hands the writers one context line
and the document search the carried slots. The failures that matter are quiet ones: a company taken from the
wrong turn, a context line that is not exactly the specified text, a rewritten question that also gets a
context line (the model has already resolved it), and any change at all with the flag off. No test touches the
network: the front door's evidence, Jev, the model and the MCP session are fakes.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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

import config_loader  # noqa: E402
import context_carry  # noqa: E402
import front_door  # noqa: E402
import jev_client  # noqa: E402
from config_loader import cfg  # noqa: E402
from context_carry import carry, entities, is_follow_up, search_suffix, years  # noqa: E402
from nodes import synthesizer  # noqa: E402
from nodes import unstructured_knowledge as uk  # noqa: E402
from state import _LAST_WRITE_WINS_FIELDS, StateSyncState  # noqa: E402

RUN = asyncio.run
NAMES = ["cocacola", "pepsico", "generalmills", "3m", "nike"]
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


# ── config and flag ─────────────────────────────────────────────────────────
def test_the_flag_ships_off_and_names_its_spec_and_the_config_section_is_as_specified():
    assert config_loader.snapshot_flags()["pipeline.context_carry"] == "off"
    flags = (REPO_ROOT / "config" / "flags.toml").read_text()
    line = next(row for row in flags.splitlines() if row.startswith("context_carry "))
    assert '= "off"' in line and "T4d_context_carry.md" in line.split("#", 1)[1]
    assert int(cfg.context_carry.answer_chars) == 200


def test_the_state_has_the_two_fields_and_they_are_last_write_wins():
    assert {"conversation_context", "carried"} <= _LAST_WRITE_WINS_FIELDS
    fields = StateSyncState.model_fields
    assert fields["conversation_context"].default is None and fields["carried"].default is None


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


# ── the workspace's names ───────────────────────────────────────────────────
class FakeSession:
    """Stands in for MCPSession in the front door and the documents steps: records every MCP call."""
    calls: list[tuple[str, dict]] = []
    documents: list[dict] = list(FILINGS)
    fail = False
    searches: list[dict] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def call(self, method, params, timeout=None):
        FakeSession.calls.append((method, params))
        if method == "library.manage":
            if FakeSession.fail:
                raise RuntimeError("mcp down")
            return {"ok": True, "documents": list(FakeSession.documents)}
        if method == "vector.hybrid_select":
            return {"ok": True, "rows": [{"chunk_id": "p_c1", "document_id": "p", "content_md": "x", "page": 1, "score": 0.5}]}
        assert method == "vector.semantic_search"
        FakeSession.searches.append(params)
        if "document_ids" in params:
            return {"matches": [{"chunk_id": f"{d}_c2", "document_id": d, "content_md": "t", "page": 1, "score": 0.3,
                                 "title": None} for d in params["document_ids"]]}
        return {"matches": [{"chunk_id": "d_pep_c1", "document_id": "d_pep", "content_md": "t", "page": 1, "score": 0.9,
                             "title": None}], "best_dense_score": 0.9, "best_keyword_score": None}


@pytest.fixture
def mcp(monkeypatch):
    FakeSession.calls, FakeSession.searches, FakeSession.documents, FakeSession.fail = [], [], list(FILINGS), False
    monkeypatch.setattr(front_door, "MCPSession", FakeSession)
    monkeypatch.setattr(uk, "MCPSession", FakeSession)
    front_door._NAMES_CACHE.clear()
    yield FakeSession
    front_door._NAMES_CACHE.clear()


def _state(question=Q2, **kw):
    state = {"raw_query": question, "tenant_id": "t1", "principal_id": "p1", "session_id": "s1", "query_id": "q1",
             "target_schema": "public", "workspace_id": "w1", "flags_snapshot": {"pipeline.context_carry": "on"}}
    state.update(kw)
    return state


def test_the_names_are_the_workspaces_document_title_names_cached_per_workspace(mcp):
    assert RUN(front_door.workspace_names(_state())) == NAMES
    assert mcp.calls[0] == ("library.manage", {"action": "workspace_documents", "principal_id": "p1",
                                               "workspace_id": "w1", "limit": int(cfg.library.max_documents)})
    assert RUN(front_door.workspace_names(_state())) == NAMES
    assert len(mcp.calls) == 1                                                     # the second read is the cache
    assert RUN(front_door.workspace_names(_state(workspace_id="w2"))) == NAMES
    assert len(mcp.calls) == 2                                                     # another workspace is another entry


def test_the_names_are_distinct_and_skip_titles_with_no_name(mcp):
    mcp.documents = [{"document_id": "a", "title": "NIKE 2022 10K"}, {"document_id": "b", "title": "NIKE 2023 10K"},
                     {"document_id": "c", "title": "2023 10K"}, {"document_id": "d", "title": None}]
    assert RUN(front_door.workspace_names(_state())) == ["nike"]


def test_no_workspace_is_no_names_and_no_call(mcp):
    assert RUN(front_door.workspace_names(_state(workspace_id=None))) == []
    assert mcp.calls == []


def test_an_unavailable_listing_is_no_names_and_is_not_cached(mcp):
    mcp.fail = True
    assert RUN(front_door.workspace_names(_state())) == []
    mcp.fail = False
    assert RUN(front_door.workspace_names(_state())) == NAMES


# ── the front door node ─────────────────────────────────────────────────────
def _evidence(turns, question):
    return front_door.Evidence(
        question=question, turns=list(turns), turn_lines=[], next_turn_index=len(turns) + 1, documents=[],
        document_text=front_door.NO_DOCUMENTS, card='Database "public": annual_metrics(company, metric)', card_status="ok")


class FakeJev:
    def __init__(self, choice="sql"):
        self.choice = choice

    async def __call__(self, state, questions, **kw):
        return jev_client.JevAnswer(
            probabilities={"documents_depend_on_database": 0.1},
            choices={"route": {"choice": self.choice, "confidence": 0.8,
                               "probabilities": {o: 0.25 for o in ("sql", "docs", "both", "neither")}}},
            model="jev-1.13", ms=3, usage={})


@pytest.fixture
def world(monkeypatch):
    """The front door's fake world: the evidence it gathers (turns set per test), Jev's choice, the model."""
    box = SimpleNamespace(turns=[PEPSICO_TURN], names_calls=0, llm_reply={}, llm_calls=0)

    async def gather(state_dict):
        return _evidence(box.turns, state_dict["raw_query"])

    async def names(state_dict):
        box.names_calls += 1
        return NAMES

    class FakeLLM:
        async def complete(self, system, messages, max_tokens, **kw):
            box.llm_calls += 1
            body = {"language": "en", "standalone_question": "q", "uses_history": False, "needs_database": "yes",
                    "needs_documents": "no", "out_of_scope": False, "database_question": "", "documents_question": "",
                    "documents_depend_on_database": False, **box.llm_reply}
            return SimpleNamespace(text=json.dumps(body), tokens_in=5, tokens_out=3)

    monkeypatch.setattr(front_door, "gather_evidence", gather)
    monkeypatch.setattr(front_door, "workspace_names", names)
    monkeypatch.setattr(front_door, "get_agent_llm", lambda role: FakeLLM())
    monkeypatch.setattr(jev_client, "ask", FakeJev("sql"))
    return box


def _node(arm, question=Q2, **flags):
    return RUN(front_door.front_door_node(_state(question, flags_snapshot=flags), arm))


ON = {"pipeline.context_carry": "on"}
NEW_KEYS = {"conversation_context", "carried"}


def test_a_follow_up_in_the_jev_choice_arm_sets_the_context_the_carried_slots_and_the_trace(world):
    update = _node("jev_choice", **ON)
    assert update["conversation_context"] == carry(Q2, PEPSICO_TURN, NAMES).line
    assert update["carried"] == {"company": "pepsico", "year": "2022", "reason": 'anaphor "it"'}
    assert update["front_door"]["context_carry"] == {"follow_up": True, "reason": 'anaphor "it"',
                                                     "company": "pepsico", "year": "2022"}
    assert update["normalized_query"] == Q2 and update["metrics"]["llm_calls"] == 0     # no model call, the raw question
    assert world.llm_calls == 0 and world.names_calls == 1


def test_a_question_that_is_not_a_follow_up_sets_carried_and_the_trace_but_no_context(world):
    world.turns = [MMM_TURN]
    update = _node("jev_choice", "What was Nike's revenue in fiscal 2023?", **ON)
    assert "conversation_context" not in update
    assert update["carried"]["company"] == "nike" and update["carried"]["year"] == "2023"
    assert update["front_door"]["context_carry"]["follow_up"] is False


def test_a_first_turn_sets_nothing_and_looks_up_no_names(world):
    world.turns = []
    update = _node("jev_choice", **ON)
    assert not (NEW_KEYS & set(update)) and "context_carry" not in update["front_door"] and world.names_calls == 0


def test_the_stored_turn_and_the_question_the_next_steps_read_stay_the_raw_question(world):
    update = _node("jev_choice", "  And   in fiscal 2022?  ", **ON)
    assert update["normalized_query"] == "  And   in fiscal 2022?  " and update["rewrite_applied"] is False


def test_an_arm_that_rewrote_the_question_sets_nothing(world):
    world.llm_reply = {"standalone_question": "Has PepsiCo reported any legal battles in fiscal 2022?", "uses_history": True}
    update = _node("llm", **ON)
    assert update["normalized_query"] != Q2 and update["rewrite_applied"] is True
    assert not (NEW_KEYS & set(update)) and "context_carry" not in update["front_door"] and world.names_calls == 0


def test_an_arm_whose_standalone_question_equals_the_raw_one_is_handled_like_jev_choice(world):
    world.llm_reply = {"standalone_question": f"  {Q2} "}          # equal once the whitespace is collapsed
    update = _node("llm", **ON)
    assert update["conversation_context"] == carry(Q2, PEPSICO_TURN, NAMES).line


def test_the_jev_rw_arm_sets_nothing_when_its_rewriter_changed_the_question(monkeypatch):
    from test_t4b_jev_front_door import _EARLIER, _REWRITE, RwWorld, _jev, _probs, _rw_state
    RwWorld(monkeypatch, history=[_EARLIER], rewriter=_REWRITE)
    _jev(monkeypatch, _probs(db=0.9, docs=0.1))
    state = _rw_state("And in FY2022?")
    state["flags_snapshot"]["pipeline.context_carry"] = "on"
    update = RUN(front_door.front_door_node(state, "jev_rw"))
    assert update["normalized_query"] == "What was 3M's FY2022 capex?"
    assert not (NEW_KEYS & set(update)) and "context_carry" not in update["front_door"]


def test_the_jev_rw_arm_gets_the_context_when_its_rewriter_left_the_question_alone_and_no_names_is_not_an_error(monkeypatch):
    from test_t4b_jev_front_door import _EARLIER, RwWorld, _jev, _probs, _rw_state
    RwWorld(monkeypatch, history=[_EARLIER])                  # the rewriter says needs_context false
    _jev(monkeypatch, _probs(db=0.9, docs=0.1))
    state = _rw_state("And in FY2022?")                       # no workspace: no names, the year is the question's own
    state["flags_snapshot"]["pipeline.context_carry"] = "on"
    update = RUN(front_door.front_door_node(state, "jev_rw"))
    assert update["normalized_query"] == "And in FY2022?"
    assert update["conversation_context"] == ('Earlier in this conversation: "What was 3M\'s FY2018 capex?"; '
                                              'its answer began: "$1,577 million".')
    assert update["carried"] == {"company": None, "year": "2022", "reason": 'anaphor "and in"'}


@pytest.mark.parametrize("arm", ["llm", "jev", "jev_choice"])
@pytest.mark.parametrize("flags", [{}, {"pipeline.context_carry": "off"}])
def test_with_the_flag_off_nothing_is_added_for_any_arm(world, monkeypatch, arm, flags):
    async def forbidden(state_dict):
        raise AssertionError("workspace_names ran with the flag off")

    monkeypatch.setattr(front_door, "workspace_names", forbidden)
    if arm == "jev":
        monkeypatch.setattr(jev_client, "ask", lambda state, questions, **kw: _jev_probs(questions))
    update = _node(arm, **flags)
    assert not (NEW_KEYS & set(update)) and "context_carry" not in update["front_door"]
    assert set(update) <= {"normalized_query", "detected_language", "turn_index", "rewrite_applied", "out_of_scope",
                           "docs_depend_on_sql", "front_door", "metrics", "route", "subtasks"}


async def _jev_probs(questions):
    return jev_client.JevAnswer(probabilities={k: 0.1 for k in questions}, model="jev-1.13", ms=1)


def test_the_flag_off_update_equals_the_update_of_the_same_run_with_the_flag_on_minus_the_new_parts(world):
    off, on = _node("jev_choice"), _node("jev_choice", **ON)
    for update in (off, on):
        update["metrics"].pop("latency_ms")
    on_trace = dict(on["front_door"])
    on_trace.pop("context_carry")
    assert off["front_door"] == on_trace
    assert {k: v for k, v in on.items() if k not in NEW_KEYS | {"front_door"}} == {
        k: v for k, v in off.items() if k != "front_door"}


# ── SQL writer and corrector ────────────────────────────────────────────────
pytest.importorskip("pglast")
import column_profiles as cp  # noqa: E402
from nodes import sql_agent as sa  # noqa: E402

DESCS = [{"table": "orders", "columns": [{"name": "status", "type": "text", "nullable": True, "comment": None}]}]
QUESTION = "how many shipped orders?"
LINE = carry(Q2, PEPSICO_TURN, NAMES).line


class _SqlLLM:
    def __init__(self, log):
        self.log = log

    async def complete(self, system, messages, max_tokens=None):
        self.log.append(messages[0]["content"])
        return SimpleNamespace(text="SELECT status FROM orders", tokens_in=1, tokens_out=1)


class _Retriever:
    async def retrieve(self, session, table_names, schema=None, **_context):
        return "TABLE orders:\n    status text NULL", DESCS


def _sql_prompts(mode: str | None, **state_kw) -> list[str]:
    prompts: list[str] = []

    async def list_tables(session, schema):
        return {"tables": [{"name": "orders"}], "fingerprint": "fp-t4d"}

    async def select(session, sql_text, max_rows=None):
        return {"ok": True, "rows": [{"status": "x"}], "columns": ["status"]}

    async def sample(session, table, columns=None, limit=None, schema=None):
        return {"rows": [{"status": "shipped"}]}

    flags = {"memory.enabled": False}
    if mode:
        flags["pipeline.sql_draft_mode"] = mode
    state = {"tenant_id": "t", "query_id": "q", "session_id": "s", "raw_query": QUESTION, "flags_snapshot": flags,
             "target_schema": "public", **state_kw}
    with patch.object(sa, "MCPSession", FakeSession), patch.object(sa, "schema_list_tables", list_tables), \
         patch.object(sa, "get_schema_retriever", lambda: _Retriever()), \
         patch.object(sa, "get_agent_llm", lambda role: _SqlLLM(prompts)), patch.object(sa, "data_run_select", select), \
         patch.object(sa, "data_sample_rows", sample), patch.object(cp, "data_sample_rows", sample), \
         patch.object(sa, "get_spatial_resolver", lambda: SimpleNamespace(resolve=lambda q: None)):
        cp.clear_cache()
        try:
            RUN(sa.sql_agent_node(state))
        finally:
            cp.clear_cache()
    return prompts


def test_the_sql_writers_draft_and_final_prompts_get_the_line_after_the_question():
    prompts = _sql_prompts(None, conversation_context=LINE)
    assert len(prompts) == 2
    for prompt in prompts:
        assert f"Question: {QUESTION}\n\n{LINE}\n\nWrite a single PostgreSQL SELECT query to answer this question." in prompt


def test_the_one_call_sql_writer_gets_the_line_after_the_question():
    prompts = _sql_prompts("profiles", conversation_context=LINE)
    assert len(prompts) == 1 and f"Question: {QUESTION}\n\n{LINE}\n\nWrite a single" in prompts[0]


def test_the_sql_corrector_gets_the_line_after_the_question():
    errors = [{"attempt": 0, "category": "syntax_error", "engine_code": "42601", "message": "bad",
               "offending_sql": "SELEC", "remediation_hint": "fix"}]
    prompts = _sql_prompts("profiles", conversation_context=LINE, errors=errors)
    assert len(prompts) == 1 and prompts[0].startswith(f"Question: {QUESTION}\n\n{LINE}\n\nPrevious attempt(s) failed.")


@pytest.mark.parametrize("extra", [{}, {"conversation_context": None}, {"conversation_context": ""}])
def test_without_a_context_line_the_sql_prompts_are_unchanged(extra):
    errors = [{"attempt": 0, "category": "syntax_error", "engine_code": "42601", "message": "bad",
               "offending_sql": "SELEC", "remediation_hint": "fix"}]
    writer = _sql_prompts(None, **extra)
    assert all(f"Question: {QUESTION}\n\nWrite a single PostgreSQL SELECT" in p for p in writer)
    assert "Earlier in this conversation" not in "".join(writer)
    corrector = _sql_prompts("profiles", errors=errors, **extra)
    assert corrector[0].startswith(f"Question: {QUESTION}\n\nPrevious attempt(s) failed.")


# ── the documents and hybrid writer ─────────────────────────────────────────
def _synth_prompt(monkeypatch, **state_kw) -> str:
    seen: list[str] = []

    class _LLM:
        async def complete(self, system, messages, max_tokens):
            seen.append(messages[0]["content"])
            return SimpleNamespace(text=json.dumps({"answer_text": "ok", "chart": None, "citations": []}),
                                   tokens_in=1, tokens_out=1)

    monkeypatch.setattr(synthesizer, "get_agent_llm", lambda role: _LLM())
    passage = {"content": "Legal proceedings.", "provenance": {"document_id": "d_pep", "chunk_id": "d_pep_c1", "page": 1,
                                                                 "score": 0.9, "title": "PEPSICO 2022 10K"}}
    RUN(synthesizer.synthesizer_node({"query_id": "q", "raw_query": Q2, "retrievals": [passage],
                                      "flags_snapshot": {}, **state_kw}))
    return seen[0]


@pytest.mark.parametrize("route", ["docs", "hybrid"])
def test_the_documents_and_hybrid_writers_get_the_line_after_the_question(monkeypatch, route):
    prompt = _synth_prompt(monkeypatch, route=route, conversation_context=LINE)
    assert prompt.startswith(f"Question: {Q2}\n\n{LINE}\n\nData context:\n")


def test_the_sql_routes_answer_writer_is_not_given_the_line(monkeypatch):
    assert _synth_prompt(monkeypatch, route="sql", conversation_context=LINE).startswith(f"Question: {Q2}\n\nData context:\n")


@pytest.mark.parametrize("extra", [{}, {"conversation_context": None}])
def test_without_a_context_line_the_writers_prompt_is_unchanged(monkeypatch, extra):
    for route in ("docs", "hybrid", "sql"):
        assert _synth_prompt(monkeypatch, route=route, **extra).startswith(f"Question: {Q2}\n\nData context:\n")


# ── the document search text ────────────────────────────────────────────────
def _docs_route(mcp, **state_kw) -> list[str]:
    mcp.searches = []
    state = {"route": "docs", "normalized_query": Q2, "tenant_id": "t1", "principal_id": "p1", "session_id": "s1",
             "query_id": "q1", "target_schema": "public", "workspace_id": "w1", "flags_snapshot": {}, **state_kw}
    RUN(uk.unstructured_knowledge_node(state))
    return [p["query_text"] for p in mcp.searches]


CARRIED = {"company": "pepsico", "year": "2022", "reason": 'anaphor "it"'}


def test_the_documents_route_search_text_gains_the_carried_slots_in_both_rounds(mcp):
    assert _docs_route(mcp, conversation_context=LINE, carried=CARRIED) == [f"{Q2} pepsico 2022"] * 2


@pytest.mark.parametrize("extra", [{}, {"carried": CARRIED}, {"conversation_context": LINE},
                                   {"conversation_context": None, "carried": None}])
def test_without_a_context_line_the_documents_route_search_text_is_unchanged(mcp, extra):
    assert _docs_route(mcp, **extra) == [Q2, Q2]


def _hybrid_half(mcp, **state_kw) -> list[str]:
    mcp.searches = []
    state = {"route": "hybrid", "normalized_query": Q2, "tenant_id": "t1", "principal_id": "p1", "session_id": "s1",
             "query_id": "q1", "target_schema": "public", "workspace_id": "w1", "flags_snapshot": {},
             "subtasks": ["sql part", "any legal battles?"], "sql_rows": [], "sql_columns": [], **state_kw}
    RUN(uk.hybrid_docs_node(state))
    return [p["query_text"] for p in mcp.searches]


def test_the_hybrid_documents_half_search_text_gains_the_carried_slots(mcp):
    assert _hybrid_half(mcp, conversation_context=LINE, carried=CARRIED) == ["any legal battles? pepsico 2022"] * 2
    # a documents half that already names the company is only given the missing year
    assert _hybrid_half(mcp, conversation_context=LINE, carried=CARRIED,
                        subtasks=["sql part", "any legal battles at PepsiCo?"]) == ["any legal battles at PepsiCo? 2022"] * 2


def test_the_narrowed_hybrid_documents_half_search_text_gains_the_carried_slots_too(mcp):
    mcp.documents = FILINGS
    rows, columns = [{"company": "PepsiCo"}], ["company"]
    texts = _hybrid_half(mcp, conversation_context=LINE, carried=CARRIED, flags_snapshot={"pipeline.hybrid_entities": "on"},
                         docs_depend_on_sql=True, sql_rows=rows, sql_columns=columns)
    assert texts == ["any legal battles? pepsico 2022"]
    assert mcp.searches[0]["document_ids"] == ["d_pep"]


@pytest.mark.parametrize("extra", [{}, {"carried": CARRIED}, {"conversation_context": None}])
def test_without_a_context_line_the_hybrid_half_search_text_is_unchanged(mcp, extra):
    assert _hybrid_half(mcp, **extra) == ["any legal battles?"] * 2
