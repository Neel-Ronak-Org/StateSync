"""
tests/test_t4b_jev_front_door.py
Pins T4b (docs/specs/partd_tasks/T4b_jev_front_door.md): the Jev front door, pipeline.front_door = "jev".

WHY THIS EXISTS. In this arm Jev (a decision model that answers typed questions with probabilities and
writes no text) makes every routing decision; the LLM front door is called only when text must be
written: to resolve a follow-up, to split a two-source question, or for a non-English question. The
failure that matters is a silent one: a Jev outage must hand the whole decision to the LLM arm and say
so in the trace, a threshold off by one comparison must not move a route, and the bearer key must never
reach a log line. No test touches the network: the client runs against an httpx mock transport, and the
decider against a fake client and a fake model.
"""

from __future__ import annotations

import asyncio
import json
import logging
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
from config_loader import cfg  # noqa: E402
from front_door import (  # noqa: E402
    JEV_QUESTIONS, JEV_RECHECK_QUESTIONS, build_trace, decide_jev, group_documents, jev_reading, jev_state, need,
)

RUN = asyncio.run
SECRET = "sk-test-0123456789-not-a-real-key"


# ── helpers ─────────────────────────────────────────────────────────────────
def _turn(n, question, route="sql", answer="3M's FY2018 capex was $1,577 million", answered=True):
    return {"turn_index": n, "rewritten_query": question, "route": route, "answer_text": answer, "answered": answered}


def _evidence(turns=(), question="What was 3M's FY2018 capex?"):
    docs = group_documents([{"document_id": "d1", "title": "3M 2022 10K", "score": 0.7123, "content_md": "Capital  expenditures"},
                            {"document_id": "d2", "title": None, "score": 0.4, "content_md": "other"}], 5, 300)
    return front_door.Evidence(
        question=question, turns=list(turns), turn_lines=[], next_turn_index=len(turns) + 1, documents=docs,
        document_text=front_door.document_lines(docs), card='Database "public": annual_metrics(company, metric)',
        card_status="ok")


def _probs(history=0.1, db=0.9, docs=0.1, oos=0.1):
    return {"uses_history": history, "needs_database": db, "needs_documents": docs, "out_of_scope": oos}


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
            probabilities={k: answer[k] for k in questions}, model="jev-1.13-served", ms=11,
            usage={"input_tokens": 100, "output_tokens": 4})


class FakeLLM:
    def __init__(self, reply, log):
        self.reply, self.log = reply, log

    async def complete(self, system, messages, max_tokens, **kw):
        self.log.append(messages[0]["content"])
        return SimpleNamespace(text=self.reply, tokens_in=21, tokens_out=13)


def _llm_reply(**fields):
    body = {"language": "en", "standalone_question": "What was 3M's FY2022 capex?", "uses_history": True,
            "needs_database": "no", "needs_documents": "no", "out_of_scope": True,      # routing fields must be ignored
            "database_question": "", "documents_question": "", "documents_depend_on_database": False}
    body.update(fields)
    return json.dumps(body)


@pytest.fixture
def llm(monkeypatch):
    """The fake model behind decide_llm; .reply to change it, .log for the user messages it was sent."""
    box = SimpleNamespace(reply=_llm_reply(), log=[])
    monkeypatch.setattr(front_door, "get_agent_llm", lambda role: FakeLLM(box.reply, box.log))
    return box


def _jev(monkeypatch, *answers):
    fake = FakeJev(*answers)
    monkeypatch.setattr(jev_client, "ask", fake)
    return fake


# ── config, flags, the questions ────────────────────────────────────────────
def test_the_config_keys_and_the_flag_comment_are_as_specified():
    fd = cfg.front_door
    assert fd.jev_url == "https://opencode.ai/zen/v1/systemone" and fd.jev_model == "jev-1.13"
    assert (fd.jev_timeout_s, fd.jev_retry_wait_s) == (30, 5)
    assert (fd.jev_yes, fd.jev_no, fd.jev_history, fd.jev_out_of_scope) == (0.6, 0.4, 0.5, 0.5)
    flags = (REPO_ROOT / "config" / "flags.toml").read_text()
    line = next(row for row in flags.splitlines() if row.startswith("front_door "))
    assert '= "off"' in line and '"jev"' in line.split("#", 1)[1]
    assert config_loader.snapshot_flags()["pipeline.front_door"] == "off"      # the default stays off
    assert "jev" in front_door.DECIDERS and front_door.front_door_enabled("jev") is True


def test_the_four_questions_are_the_specified_text():
    assert JEV_QUESTIONS == {
        "uses_history": "Does the new question depend on the earlier conversation turns to be understood?",
        "needs_database": "Does answering the new question need facts stored in the database described?",
        "needs_documents": "Does answering the new question need what the uploaded documents say?",
        "out_of_scope": "Is the new question about something neither the database nor the documents could answer?",
    }
    assert set(JEV_RECHECK_QUESTIONS) == {"needs_database", "needs_documents", "out_of_scope"}


# ── the state Jev sees, and the request body ────────────────────────────────
def test_the_state_holds_the_question_turns_card_and_documents():
    state = json.loads(jev_state(_evidence([_turn(1, "What was 3M's FY2018 capex?", answer="x" * 500)]),
                                 "And in FY2022?"))
    assert state["question"] == "And in FY2022?"
    assert state["conversation"] == [{"turn": 1, "question": "What was 3M's FY2018 capex?", "route": "sql",
                                      "answered": True, "answer_start": "x" * 200}]
    assert state["database"] == 'Database "public": annual_metrics(company, metric)'
    assert state["documents"] == {"best_matches": [
        {"title": "3M 2022 10K", "score": 0.71, "excerpt": "Capital expenditures"},
        {"title": "d2", "score": 0.4, "excerpt": "other"}]}
    assert json.loads(jev_state(_evidence(), "q"))["conversation"] == []


def test_the_request_body_has_the_four_noul_questions():
    body = jev_client.request_body("jev-1.13", "STATE", JEV_QUESTIONS)
    assert body["model"] == "jev-1.13" and body["state"] == "STATE"
    assert set(body["questions"]) == {"uses_history", "needs_database", "needs_documents", "out_of_scope"}
    assert all(q["type"] == "noul" and q["instructions"] == JEV_QUESTIONS[name] for name, q in body["questions"].items())


# ── probabilities to the routing table ──────────────────────────────────────
@pytest.mark.parametrize("p, expected", [
    (0.0, "no"), (0.4, "no"), (0.4001, "unsure"), (0.5, "unsure"), (0.5999, "unsure"), (0.6, "yes"), (1.0, "yes")])
def test_need_at_the_boundaries_both_inclusive(p, expected):
    assert need(p) == expected


def test_out_of_scope_and_history_thresholds_are_inclusive(monkeypatch, llm):
    assert jev_reading(_probs(db=0.9, oos=0.4999))[2:] == (False, "sql")
    assert jev_reading(_probs(db=0.9, oos=0.5))[2:] == (True, "neither")
    assert jev_reading(_probs(db=0.9, docs=0.9))[3] == "hybrid" and jev_reading(_probs(db=0.1, docs=0.1))[3] == "neither"
    assert jev_reading(_probs(db=0.5, docs=0.5))[3] == "hybrid"                 # route_for, unchanged: unsure/unsure
    _jev(monkeypatch, _probs(history=0.4999), _probs(history=0.5), _probs())    # the third: the re-check
    assert RUN(decide_jev(_evidence(), "q")).parsed.uses_history is False and not llm.log
    assert RUN(decide_jev(_evidence(), "q")).parsed.uses_history is True and len(llm.log) == 1


# ── the decider ─────────────────────────────────────────────────────────────
def test_a_first_turn_single_source_question_makes_no_llm_call(monkeypatch, llm):
    fake = _jev(monkeypatch, _probs(db=0.9, docs=0.1))
    decision = RUN(decide_jev(_evidence(), "What was 3M's FY2018 capex?"))
    assert llm.log == [] and decision.llm_calls == 0 and decision.tokens_in == 0
    assert (decision.arm, decision.route, decision.jev_calls) == ("jev", "sql", 1)
    assert decision.parsed.standalone_question == "What was 3M's FY2018 capex?" and decision.parsed.language == "en"
    assert len(fake.calls) == 1 and fake.calls[0][1] == JEV_QUESTIONS
    jev = build_trace(decision, _evidence())["jev"]
    assert jev == {
        "probabilities": {"first": _probs(db=0.9, docs=0.1)}, "model": "jev-1.13-served", "ms": 11, "calls": 1,
        "rechecked": False, "llm_used": False, "usage": {"first": {"input_tokens": 100, "output_tokens": 4}}}


def test_docs_and_neither_routes_come_from_jev_alone(monkeypatch, llm):
    _jev(monkeypatch, _probs(db=0.1, docs=0.9), _probs(db=0.1, docs=0.1), _probs(db=0.9, docs=0.1, oos=0.9))
    assert [RUN(decide_jev(_evidence(), "q")).route for _ in range(3)] == ["docs", "neither", "neither"]
    assert llm.log == []


def test_uses_history_calls_the_llm_once_keeps_jevs_route_and_rechecks_the_standalone_question(monkeypatch, llm):
    fake = _jev(monkeypatch, _probs(history=0.9, db=0.9, docs=0.1), _probs(db=0.1, docs=0.9, oos=0.1))
    evidence = _evidence([_turn(1, "What was 3M's FY2018 capex?")], question="And in FY2022?")
    decision = RUN(decide_jev(evidence, "And in FY2022?"))
    assert len(llm.log) == 1 and decision.llm_calls == 1 and decision.tokens_in == 21
    assert "And in FY2022?" in llm.log[0]
    assert len(fake.calls) == 2 and fake.calls[1][1] == JEV_RECHECK_QUESTIONS
    assert fake.calls[1][0]["question"] == "What was 3M's FY2022 capex?"            # the standalone question
    assert [d["title"] for d in fake.calls[1][0]["documents"]["best_matches"]] == ["3M 2022 10K", "d2"]   # no new search
    # The LLM's routing fields (docs: no / no, out_of_scope true) were ignored; the route is the re-check's.
    assert decision.route == "docs" and decision.parsed.out_of_scope is False
    assert (decision.parsed.needs_database, decision.parsed.needs_documents) == ("no", "yes")
    assert decision.parsed.uses_history is True and decision.parsed.standalone_question == "What was 3M's FY2022 capex?"
    jev = build_trace(decision, evidence)["jev"]
    assert jev["rechecked"] is True and jev["llm_used"] is True and jev["calls"] == 2 and jev["ms"] == 22
    assert set(jev["probabilities"]) == {"first", "recheck"} and set(jev["usage"]) == {"first", "recheck"}
    assert jev["probabilities"]["first"]["uses_history"] == 0.9 and "uses_history" not in jev["probabilities"]["recheck"]
    assert "fallback" not in jev and "jev_error" not in jev
    assert decision.jev_calls == 2


def test_uses_history_with_an_unchanged_question_is_not_rechecked(monkeypatch, llm):
    llm.reply = _llm_reply(standalone_question="  And in FY2022?  ")
    fake = _jev(monkeypatch, _probs(history=0.9, db=0.9, docs=0.1))
    decision = RUN(decide_jev(_evidence(), "And in FY2022?"))
    assert len(fake.calls) == 1 and decision.route == "sql" and decision.jev["rechecked"] is False


def test_hybrid_calls_the_llm_for_the_split_and_the_route_stays_hybrid(monkeypatch, llm):
    llm.reply = _llm_reply(standalone_question="q", database_question="the sql part", documents_question="the docs part",
                           documents_depend_on_database=True)
    fake = _jev(monkeypatch, _probs(db=0.9, docs=0.9))
    decision = RUN(decide_jev(_evidence(), "q"))
    assert len(llm.log) == 1 and len(fake.calls) == 1                          # same question: no re-check
    assert decision.route == "hybrid" and decision.parsed.out_of_scope is False
    assert (decision.parsed.database_question, decision.parsed.documents_question) == ("the sql part", "the docs part")
    assert decision.parsed.documents_depend_on_database is True


def test_a_non_english_question_calls_the_llm_and_the_gate_is_the_script_test(monkeypatch, llm):
    llm.reply = _llm_reply(language="hi", standalone_question="What were the sales?")
    fake = _jev(monkeypatch, _probs(db=0.9, docs=0.1), _probs(db=0.9, docs=0.1))
    decision = RUN(decide_jev(_evidence(), "बिक्री कितनी थी?"))
    assert len(llm.log) == 1 and decision.parsed.language == "hi" and decision.route == "sql"
    assert len(fake.calls) == 2 and fake.calls[1][0]["question"] == "What were the sales?"


def test_an_llm_that_cannot_be_read_leaves_jevs_route_and_the_original_question(monkeypatch, llm):
    llm.reply = "not json"
    _jev(monkeypatch, _probs(db=0.9, docs=0.9))
    decision = RUN(decide_jev(_evidence(), "q"))
    assert decision.route == "hybrid" and decision.parsed.standalone_question == "q" and decision.parsed.parse_ok is False


def test_a_jev_error_falls_back_to_the_llm_arm_entirely(monkeypatch, llm):
    llm.reply = _llm_reply(standalone_question="q", needs_database="yes", needs_documents="no", out_of_scope=False)
    fake = _jev(monkeypatch, "HTTP 503: down")
    evidence = _evidence()
    decision = RUN(decide_jev(evidence, "q"))
    assert len(llm.log) == 1 and decision.route == "sql" and decision.llm_calls == 1 and len(fake.calls) == 1
    trace = build_trace(decision, evidence)
    assert trace["arm"] == "jev" and trace["route"] == "sql"
    assert trace["jev"] == {"probabilities": {}, "model": "jev-1.13", "ms": 7, "calls": 1, "rechecked": False,
                            "llm_used": True, "fallback": "llm", "jev_error": "HTTP 503: down", "usage": {}}


def test_a_failed_recheck_keeps_the_first_reading_and_says_so(monkeypatch, llm):
    _jev(monkeypatch, _probs(history=0.9, db=0.9, docs=0.1), "HTTP 429: slow down")
    decision = RUN(decide_jev(_evidence(), "And in FY2022?"))
    assert decision.route == "sql"
    jev = decision.jev
    assert jev["rechecked"] is False and jev["jev_error"] == "HTTP 429: slow down" and jev["calls"] == 2
    assert "fallback" not in jev and set(jev["probabilities"]) == {"first"}


def test_jev_answers_are_never_cached(monkeypatch, llm):
    fake = _jev(monkeypatch, _probs(db=0.58), _probs(db=0.63))      # the same state gave 0.58 then 0.63
    assert [RUN(decide_jev(_evidence(), "q")).route for _ in range(2)] == ["sql", "sql"]
    assert len(fake.calls) == 2 and fake.answers == []


def test_the_llm_arms_trace_is_unchanged_and_has_no_jev_block():
    parsed = front_door.parse_reply('{"needs_database": "yes", "needs_documents": "no"}', "q")
    decision = front_door.Decision("llm", parsed, "sql", llm_calls=1)
    assert "jev" not in build_trace(decision, _evidence())


def test_the_node_runs_the_jev_arm_and_counts_jev_calls_apart_from_llm_calls(monkeypatch, llm):
    _jev(monkeypatch, _probs(history=0.9, db=0.9, docs=0.1), _probs(db=0.9, docs=0.1))

    async def gather(state_dict):
        return _evidence([_turn(1, "What was 3M's FY2018 capex?")], question=state_dict["raw_query"])

    monkeypatch.setattr(front_door, "gather_evidence", gather)
    update = RUN(front_door.front_door_node({"raw_query": "And in FY2022?", "query_id": "q1"}, "jev"))
    assert update["route"] == "sql" and update["normalized_query"] == "What was 3M's FY2022 capex?"
    assert update["metrics"]["llm_calls"] == 1 and update["metrics"]["jev_calls"] == 2
    assert update["front_door"]["jev"]["llm_used"] is True and update["front_door"]["arm"] == "jev"
    assert update["out_of_scope"] is False and "subtasks" not in update


# ── the client ──────────────────────────────────────────────────────────────
@pytest.fixture
def key_file(tmp_path, monkeypatch):
    path = tmp_path / "opencode_api_key"
    path.write_text(SECRET + "\n")
    monkeypatch.setenv(jev_client.JEV_KEY_FILE_ENV, str(path))
    return path


@pytest.fixture
def sleeps(monkeypatch):
    waited: list[float] = []

    async def fake_sleep(seconds):
        waited.append(seconds)

    monkeypatch.setattr(jev_client.asyncio, "sleep", fake_sleep)
    return waited


def _transport(*responses):
    """A mock transport answering with each response in turn; .requests records what was sent."""
    queue = list(responses)
    requests: list[httpx.Request] = []

    def handler(request):
        requests.append(request)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    transport = httpx.MockTransport(handler)
    transport.requests = requests
    return transport


def _ok(**extra):
    body = {"model": "jev-1.13-2026", "usage": {"input_tokens": 321, "output_tokens": 5},
            "answers": {name: {"noul": 0.5} for name in JEV_QUESTIONS}}
    body.update(extra)
    return httpx.Response(200, json=body)


def test_the_client_sends_the_request_and_reads_the_answers(key_file, sleeps):
    reply = _ok()
    body = reply.json()
    body["answers"]["needs_database"]["noul"] = 0.87
    transport = _transport(httpx.Response(200, json=body))
    answer = RUN(jev_client.ask("STATE", JEV_QUESTIONS, transport=transport))
    assert answer.ok and answer.probabilities["needs_database"] == 0.87 and set(answer.probabilities) == set(JEV_QUESTIONS)
    assert answer.model == "jev-1.13-2026" and answer.usage == {"input_tokens": 321, "output_tokens": 5}
    assert answer.ms >= 0 and sleeps == []
    sent = transport.requests[0]
    assert str(sent.url) == "https://opencode.ai/zen/v1/systemone" and sent.method == "POST"
    assert sent.headers["authorization"] == f"Bearer {SECRET}"
    assert json.loads(sent.content) == jev_client.request_body("jev-1.13", "STATE", JEV_QUESTIONS)


def test_the_served_model_defaults_to_the_requested_one(key_file):
    reply = _ok().json()
    del reply["model"], reply["usage"]
    answer = RUN(jev_client.ask("s", JEV_QUESTIONS, transport=_transport(httpx.Response(200, json=reply))))
    assert answer.ok and answer.model == "jev-1.13" and answer.usage == {}


@pytest.mark.parametrize("first", [httpx.Response(429, text="slow down"), httpx.Response(503, text="busy")])
def test_a_429_or_5xx_then_200_succeeds_after_one_wait(key_file, sleeps, first):
    transport = _transport(first, _ok())
    answer = RUN(jev_client.ask("s", JEV_QUESTIONS, transport=transport))
    assert answer.ok and len(transport.requests) == 2 and sleeps == [5.0]


def test_two_429s_is_an_error_value_not_an_exception(key_file, sleeps):
    transport = _transport(httpx.Response(429, text="slow"), httpx.Response(429, text="slower"))
    answer = RUN(jev_client.ask("s", JEV_QUESTIONS, transport=transport))
    assert not answer.ok and answer.error.startswith("HTTP 429") and answer.probabilities == {}
    assert len(transport.requests) == 2 and sleeps == [5.0]                  # one retry, no more


def test_other_failures_are_error_values_without_a_retry(key_file, sleeps):
    for response, text in [(httpx.Response(401, text="no"), "HTTP 401"), (httpx.Response(200, text="<html>"), "not JSON"),
                           (httpx.Response(200, json={"answers": {}}), "no probability"),
                           (httpx.Response(200, json={"nope": 1}), "no answers")]:
        transport = _transport(response)
        answer = RUN(jev_client.ask("s", JEV_QUESTIONS, transport=transport))
        assert not answer.ok and text in answer.error and len(transport.requests) == 1
    bad = _ok().json()
    bad["answers"]["out_of_scope"]["noul"] = 1.5
    assert "out_of_scope" in RUN(jev_client.ask("s", JEV_QUESTIONS, transport=_transport(httpx.Response(200, json=bad)))).error
    transport = _transport(httpx.ConnectTimeout("timed out"))
    answer = RUN(jev_client.ask("s", JEV_QUESTIONS, transport=transport))
    assert not answer.ok and "ConnectTimeout" in answer.error and sleeps == []


def test_a_missing_or_empty_key_file_is_an_error_value(tmp_path, monkeypatch):
    monkeypatch.setenv(jev_client.JEV_KEY_FILE_ENV, str(tmp_path / "absent"))
    answer = RUN(jev_client.ask("s", JEV_QUESTIONS, transport=_transport()))
    assert not answer.ok and "no Jev API key file" in answer.error
    (tmp_path / "empty").write_text("\n")
    monkeypatch.setenv(jev_client.JEV_KEY_FILE_ENV, str(tmp_path / "empty"))
    assert "empty" in RUN(jev_client.ask("s", JEV_QUESTIONS, transport=_transport())).error


def test_the_key_is_never_in_a_log_line_or_an_error(key_file, sleeps, caplog):
    caplog.set_level(logging.DEBUG)
    echo = httpx.Response(429, text=f"rate limited for Bearer {SECRET}")
    answer = RUN(jev_client.ask("s", JEV_QUESTIONS, transport=_transport(echo, echo)))
    RUN(jev_client.ask("s", JEV_QUESTIONS, transport=_transport(httpx.ConnectError(f"cannot reach, token {SECRET}"))))
    RUN(jev_client.ask("s", JEV_QUESTIONS, transport=_transport(httpx.Response(401, text=SECRET))))
    assert not answer.ok and "[redacted]" in answer.error and SECRET not in answer.error
    assert caplog.records and all(SECRET not in record.getMessage() for record in caplog.records)
    assert SECRET not in caplog.text


# ── the graph ───────────────────────────────────────────────────────────────
def test_the_jev_flag_builds_the_same_graph_as_the_llm_flag():
    graph = pytest.importorskip("graph")

    def drawn(compiled):
        g = compiled.get_graph()
        return sorted((e.source, e.target, bool(e.conditional)) for e in g.edges), sorted(g.nodes)

    assert drawn(graph.build_graph("jev")) == drawn(graph.build_graph("llm"))
    assert drawn(graph.build_route_graph("jev")) == drawn(graph.build_route_graph("llm"))
    with pytest.raises(ValueError):
        graph.build_graph("nonsense")
