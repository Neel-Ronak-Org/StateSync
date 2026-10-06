"""
tests/test_t4c_jev_choice.py
Pins T4c Part 1 (docs/specs/partd_tasks/T4c_jev_choice_and_context.md): pipeline.front_door = "jev_choice".

WHY THIS EXISTS. The T4b arms ask Jev independent yes/no questions; on the routing set a hybrid question and
an "either" question get nearly the same probabilities, so no threshold separates them. This arm asks ONE
choice question (sql, docs, both, neither) and routes on the chosen option: no threshold, no LLM call, no
normaliser. The failures that matter are silent ones: a choice mapped to the wrong route, an LLM call that
sneaks in, a Jev outage that does not hand the decision to the LLM arm, a choice dict mangled on the way to
the wire, or the noul path of the existing arms changing. No test touches the network: the client runs
against an httpx mock transport, the decider against a fake client and a fake model.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
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
import front_door  # noqa: E402
import jev_client  # noqa: E402
from front_door import (  # noqa: E402
    JEV_CHOICE_QUESTIONS, JEV_QUESTIONS, build_trace, decide_jev_choice, group_documents,
)

RUN = asyncio.run
SECRET = "sk-test-0123456789-not-a-real-key"
OPTIONS = ("sql", "docs", "both", "neither")


# ── helpers ─────────────────────────────────────────────────────────────────
def _evidence(turns=(), question="What was 3M's FY2018 capex?"):
    docs = group_documents([{"document_id": "d1", "title": "3M 2022 10K", "score": 0.7123, "content_md": "Capital  expenditures"}],
                           5, 300)
    return front_door.Evidence(
        question=question, turns=list(turns), turn_lines=[], next_turn_index=len(turns) + 1, documents=docs,
        document_text=front_door.document_lines(docs), card='Database "public": annual_metrics(company, metric)',
        card_status="ok")


def _answer(choice="sql", dep=0.1, confidence=0.82, spread=None):
    """The fake client's reply: the choice question's answer and the noul P(yes)."""
    probabilities = spread or {o: (0.7 if o == choice else 0.1) for o in OPTIONS}
    return {"choice": choice, "confidence": confidence, "probabilities": probabilities, "dep": dep}


class FakeJev:
    """Stands in for jev_client.ask: replies from a queue, records every (state, questions)."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls: list[tuple[dict, dict]] = []

    async def __call__(self, state, questions, **kw):
        self.calls.append((json.loads(state), dict(questions)))
        answer = self.answers.pop(0)
        if isinstance(answer, str):
            return jev_client.JevAnswer(model="jev-1.13", ms=7, error=answer)
        return jev_client.JevAnswer(
            probabilities={"documents_depend_on_database": answer["dep"]},
            choices={"route": {"choice": answer["choice"], "confidence": answer["confidence"],
                               "probabilities": answer["probabilities"]}},
            model="jev-1.13-served", ms=11, usage={"input_tokens": 100, "output_tokens": 4})


def _jev(monkeypatch, *answers):
    fake = FakeJev(*answers)
    monkeypatch.setattr(jev_client, "ask", fake)
    return fake


class FakeLLM:
    def __init__(self, reply, log):
        self.reply, self.log = reply, log

    async def complete(self, system, messages, max_tokens, **kw):
        self.log.append(messages[0]["content"])
        return SimpleNamespace(text=self.reply, tokens_in=21, tokens_out=13)


@pytest.fixture
def llm(monkeypatch):
    """The fake model behind decide_llm; .reply to change it, .log for the user messages it was sent."""
    box = SimpleNamespace(reply=json.dumps({
        "language": "en", "standalone_question": "q", "uses_history": False, "needs_database": "yes",
        "needs_documents": "no", "out_of_scope": False, "database_question": "", "documents_question": "",
        "documents_depend_on_database": False}), log=[])
    monkeypatch.setattr(front_door, "get_agent_llm", lambda role: FakeLLM(box.reply, box.log))
    return box


# ── the questions and the flag ──────────────────────────────────────────────
def test_the_request_carries_one_choice_and_one_noul_question_with_the_fixed_text(monkeypatch, llm):
    fake = _jev(monkeypatch, _answer("sql"))
    RUN(decide_jev_choice(_evidence(), "What was 3M's FY2018 capex?"))
    assert len(fake.calls) == 1
    questions = fake.calls[0][1]
    assert list(questions) == ["route", "documents_depend_on_database"]
    assert questions["route"] == {
        "type": "choice",
        "instructions": "Which sources are needed to answer the new question?",
        "criteria": {
            "sql": "Only the database: the answer is a stored figure, or a calculation over the database's tables.",
            "docs": ("Only the documents: the answer is in what the filings say (explanations, risks, segments, "
                     "or figures the database does not hold)."),
            "both": "Both: one part of the answer comes from the database and another part from the documents.",
            "neither": ("Neither: the database and the documents do not hold it (other companies, the future, live "
                        "data, or not about this data)."),
        }}
    assert questions["documents_depend_on_database"] == JEV_QUESTIONS["documents_depend_on_database"] == (
        "Does the documents part of the new question depend on an answer from the database first, for example "
        "which company to look up?")
    body = jev_client.request_body("jev-1.13", "STATE", JEV_CHOICE_QUESTIONS)
    assert body["questions"]["route"] == questions["route"]                     # a choice dict goes on the wire as it is
    assert body["questions"]["documents_depend_on_database"] == {
        "type": "noul", "instructions": JEV_QUESTIONS["documents_depend_on_database"]}


def test_the_state_is_the_raw_question_with_the_conversation_in_it(monkeypatch, llm):
    turn = {"turn_index": 1, "rewritten_query": "What was 3M's FY2018 capex?", "route": "sql",
            "answer_text": "$1,577 million", "answered": True}
    fake = _jev(monkeypatch, _answer("sql"))
    decision = RUN(decide_jev_choice(_evidence([turn], question="And in FY2022?"), "And in FY2022?"))
    state = fake.calls[0][0]
    assert state["question"] == "And in FY2022?"
    assert [t["question"] for t in state["conversation"]] == ["What was 3M's FY2018 capex?"]
    assert decision.parsed.standalone_question == "And in FY2022?" and decision.parsed.uses_history is False


def test_the_flag_is_registered_without_a_text_step_and_the_default_stays_off():
    assert front_door.DECIDERS["jev_choice"] is decide_jev_choice and front_door.front_door_enabled("jev_choice") is True
    assert "jev_choice" not in front_door.PREPARERS
    flags = (REPO_ROOT / "config" / "flags.toml").read_text()
    line = next(row for row in flags.splitlines() if row.startswith("front_door "))
    assert '= "off"' in line and '"jev_choice"' in line.split("#", 1)[1]
    assert config_loader.snapshot_flags()["pipeline.front_door"] == "off"


def test_the_jev_choice_flag_builds_the_same_graph_as_the_llm_flag():
    graph = pytest.importorskip("graph")

    def drawn(compiled):
        g = compiled.get_graph()
        return sorted((e.source, e.target, bool(e.conditional)) for e in g.edges), sorted(g.nodes)

    assert drawn(graph.build_graph("jev_choice")) == drawn(graph.build_graph("llm"))


# ── each choice maps to its route ───────────────────────────────────────────
@pytest.mark.parametrize("choice, needs, route, out_of_scope", [
    ("sql", ("yes", "no"), "sql", False), ("docs", ("no", "yes"), "docs", False),
    ("both", ("yes", "yes"), "hybrid", False), ("neither", ("no", "no"), "neither", True)])
def test_each_choice_maps_to_its_route_and_needs_with_no_llm_call(monkeypatch, llm, choice, needs, route, out_of_scope):
    fake = _jev(monkeypatch, _answer(choice))
    decision = RUN(decide_jev_choice(_evidence(), "q"))
    assert (decision.arm, decision.route, decision.jev_calls, len(fake.calls)) == ("jev_choice", route, 1, 1)
    p = decision.parsed
    assert (p.needs_database, p.needs_documents, p.out_of_scope) == (*needs, out_of_scope)
    assert (p.parse_ok, p.language, p.standalone_question, p.uses_history) == (True, "en", "q", False)
    assert (p.database_question, p.documents_question) == ("", "")                # never a split
    assert llm.log == [] and decision.llm_calls == 0 and decision.tokens_in == decision.tokens_out == 0


def test_the_choice_decides_whatever_the_probabilities_say(monkeypatch, llm):
    """No threshold: the chosen option is the route even when its probability is not the largest or is low."""
    _jev(monkeypatch, _answer("both", confidence=0.3, spread={"sql": 0.4, "docs": 0.1, "both": 0.35, "neither": 0.15}))
    assert RUN(decide_jev_choice(_evidence(), "q")).route == "hybrid"


@pytest.mark.parametrize("p, expected", [(0.0, False), (0.4999, False), (0.5, True), (1.0, True)])
def test_documents_depend_on_database_follows_jev_depends_at_its_edge(monkeypatch, llm, p, expected):
    _jev(monkeypatch, _answer("both", dep=p))
    assert RUN(decide_jev_choice(_evidence(), "q")).parsed.documents_depend_on_database is expected


def test_the_trace_has_the_choice_probabilities_confidence_depends_model_ms_usage_and_one_call(monkeypatch, llm):
    spread = {"sql": 0.1, "docs": 0.05, "both": 0.8, "neither": 0.05}
    _jev(monkeypatch, _answer("both", dep=0.7, confidence=0.8, spread=spread))
    evidence = _evidence()
    decision = RUN(decide_jev_choice(evidence, "q"))
    trace = build_trace(decision, evidence)
    assert trace["arm"] == "jev_choice" and trace["route"] == "hybrid" and trace["documents_depend_on_database"] is True
    assert trace["jev"] == {"choice": "both", "probabilities": spread, "confidence": 0.8, "depends": True,
                            "model": "jev-1.13-served", "ms": 11, "usage": {"input_tokens": 100, "output_tokens": 4},
                            "calls": 1, "criteria": "default"}


def test_the_node_makes_no_llm_call_and_counts_one_jev_call(monkeypatch, llm):
    _jev(monkeypatch, _answer("docs"))

    async def gather(state_dict):
        return _evidence(question=state_dict["raw_query"])

    monkeypatch.setattr(front_door, "gather_evidence", gather)
    update = RUN(front_door.front_door_node({"raw_query": "Why did margins fall?", "query_id": "q1"}, "jev_choice"))
    assert update["route"] == "docs" and update["normalized_query"] == "Why did margins fall?"
    assert update["metrics"]["llm_calls"] == 0 and update["metrics"]["jev_calls"] == 1 and llm.log == []
    assert update["front_door"]["arm"] == "jev_choice" and "subtasks" not in update
    _jev(monkeypatch, _answer("neither"))
    second = RUN(front_door.front_door_node({"raw_query": "Weather?", "query_id": "q2"}, "jev_choice"))
    assert second["out_of_scope"] is True and "route" not in second


# ── a Jev error falls back to the LLM arm ───────────────────────────────────
def test_a_jev_error_falls_back_to_the_llm_arm_entirely(monkeypatch, llm):
    fake = _jev(monkeypatch, "HTTP 503: down")
    evidence = _evidence()
    decision = RUN(decide_jev_choice(evidence, "q"))
    assert len(llm.log) == 1 and decision.llm_calls == 1 and decision.route == "sql" and len(fake.calls) == 1
    trace = build_trace(decision, evidence)
    assert trace["arm"] == "jev_choice" and trace["route"] == "sql"
    assert trace["jev"] == {"choice": None, "probabilities": {}, "confidence": None, "depends": None,
                            "model": "jev-1.13", "ms": 7, "usage": {}, "calls": 1, "llm_used": True,
                            "fallback": "llm", "jev_error": "HTTP 503: down"}


# ── the other arms are unchanged ────────────────────────────────────────────
def test_jev_jev_rw_llm_and_off_are_unchanged(monkeypatch, llm):
    assert set(front_door.DECIDERS) == {"llm", "jev", "jev_rw", "jev_choice"} and set(front_door.PREPARERS) == {"jev_rw"}
    assert front_door.DECIDERS["jev"] is front_door.decide_jev and front_door.DECIDERS["jev_rw"] is front_door.decide_jev_rw
    assert front_door.DECIDERS["llm"] is front_door.decide_llm and front_door.front_door_enabled("off") is False
    assert JEV_QUESTIONS["needs_database"] == "Does answering the new question need facts stored in the database described?"
    assert all(isinstance(text, str) for text in JEV_QUESTIONS.values())          # the noul arms still send plain text
    # "jev": the same first request (five noul questions), no choice anywhere.
    probs = {"uses_history": 0.1, "needs_database": 0.9, "needs_documents": 0.1, "out_of_scope": 0.1,
             "documents_depend_on_database": 0.1}
    calls = []

    async def noul_ask(state, questions, **kw):
        calls.append(dict(questions))
        return jev_client.JevAnswer(probabilities={k: probs[k] for k in questions}, model="m", ms=1)

    monkeypatch.setattr(jev_client, "ask", noul_ask)
    decision = RUN(front_door.decide_jev(_evidence(), "q"))
    assert decision.route == "sql" and decision.arm == "jev" and calls == [JEV_QUESTIONS] and llm.log == []
    decision = RUN(front_door.decide_jev_rw(_evidence(), "q"))
    assert decision.route == "sql" and decision.arm == "jev_rw" and calls[1] == front_door.JEV_RW_QUESTIONS


# ── the client: choice questions ────────────────────────────────────────────
@pytest.fixture
def key_file(tmp_path, monkeypatch):
    path = tmp_path / "opencode_api_key"
    path.write_text(SECRET + "\n")
    monkeypatch.setenv(jev_client.JEV_KEY_FILE_ENV, str(path))
    return path


def _transport(*responses):
    queue = list(responses)
    requests: list[httpx.Request] = []

    def handler(request):
        requests.append(request)
        return queue.pop(0)

    transport = httpx.MockTransport(handler)
    transport.requests = requests
    return transport


def _reply(**route):
    answer = {"type": "choice", "choice": "both", "confidence": 0.81,
              "probabilities": {"sql": 0.1, "docs": 0.07, "both": 0.81, "neither": 0.02}}
    answer.update(route)
    return httpx.Response(200, json={"model": "jev-1.13-2026", "usage": {"input_tokens": 321, "output_tokens": 5},
                                     "answers": {"route": answer,
                                                 "documents_depend_on_database": {"type": "noul", "noul": 0.66}}})


def test_the_client_sends_the_choice_dict_and_keeps_choice_probabilities_and_confidence(key_file):
    transport = _transport(_reply())
    answer = RUN(jev_client.ask("STATE", JEV_CHOICE_QUESTIONS, transport=transport))
    assert answer.ok and answer.model == "jev-1.13-2026" and answer.usage == {"input_tokens": 321, "output_tokens": 5}
    assert answer.choices == {"route": {"choice": "both", "confidence": 0.81,
                                        "probabilities": {"sql": 0.1, "docs": 0.07, "both": 0.81, "neither": 0.02}}}
    assert answer.probabilities == {"documents_depend_on_database": 0.66}      # noul answers stay in probabilities
    sent = json.loads(transport.requests[0].content)
    assert sent == jev_client.request_body("jev-1.13", "STATE", JEV_CHOICE_QUESTIONS)
    assert sent["questions"]["route"]["type"] == "choice" and set(sent["questions"]["route"]["criteria"]) == set(OPTIONS)
    assert sent["questions"]["documents_depend_on_database"]["type"] == "noul"


def test_a_noul_only_request_has_no_choices_and_is_unchanged(key_file):
    reply = httpx.Response(200, json={"answers": {name: {"noul": 0.5} for name in JEV_QUESTIONS}})
    transport = _transport(reply)
    answer = RUN(jev_client.ask("s", JEV_QUESTIONS, transport=transport))
    assert answer.ok and answer.choices == {} and set(answer.probabilities) == set(JEV_QUESTIONS)
    assert json.loads(transport.requests[0].content)["questions"]["needs_database"] == {
        "type": "noul", "instructions": JEV_QUESTIONS["needs_database"]}


def test_a_choice_outside_the_criteria_or_missing_is_an_error_value(key_file):
    for route in ({"choice": "maybe"}, {"choice": None}, {"choice": 3}):
        answer = RUN(jev_client.ask("s", JEV_CHOICE_QUESTIONS, transport=_transport(_reply(**route))))
        assert not answer.ok and "route" in answer.error and answer.choices == {}
    no_route = httpx.Response(200, json={"answers": {"documents_depend_on_database": {"noul": 0.5}}})
    assert "route" in RUN(jev_client.ask("s", JEV_CHOICE_QUESTIONS, transport=_transport(no_route))).error
    bad_noul = _reply().json()
    bad_noul["answers"]["documents_depend_on_database"]["noul"] = 1.5
    answer = RUN(jev_client.ask("s", JEV_CHOICE_QUESTIONS, transport=_transport(httpx.Response(200, json=bad_noul))))
    assert "documents_depend_on_database" in answer.error


def test_a_choice_answer_without_probabilities_or_confidence_keeps_the_choice(key_file):
    body = _reply().json()
    del body["answers"]["route"]["probabilities"], body["answers"]["route"]["confidence"]
    answer = RUN(jev_client.ask("s", JEV_CHOICE_QUESTIONS, transport=_transport(httpx.Response(200, json=body))))
    assert answer.ok and answer.choices["route"] == {"choice": "both", "confidence": None, "probabilities": {}}
    body["answers"]["route"].update(confidence=7, probabilities={"sql": 0.2, "docs": "x", "both": 2})
    answer = RUN(jev_client.ask("s", JEV_CHOICE_QUESTIONS, transport=_transport(httpx.Response(200, json=body))))
    assert answer.choices["route"] == {"choice": "both", "confidence": None, "probabilities": {"sql": 0.2}}


def test_choice_wording_can_be_overridden_from_settings(monkeypatch):
    """[front_door] jev_choice_<option> replaces that option's description; unset options keep the fixed text."""
    data = object.__getattribute__(front_door.cfg.front_door, "_data")
    assert front_door.jev_choice_questions() is front_door.JEV_CHOICE_QUESTIONS
    monkeypatch.setitem(data, "jev_choice_sql", "Only the database, at the granularity its tables hold.")
    q = front_door.jev_choice_questions()
    assert q["route"]["criteria"]["sql"] == "Only the database, at the granularity its tables hold."
    assert q["route"]["criteria"]["docs"] == front_door.JEV_ROUTE_CHOICE["criteria"]["docs"]
    assert front_door.JEV_ROUTE_CHOICE["criteria"]["sql"].startswith("Only the database: the answer")   # not mutated
