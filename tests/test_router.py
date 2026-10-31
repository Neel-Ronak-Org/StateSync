"""
tests/test_router.py
The router (orchestrator/front_door.py): which source a question needs.

Pinned here:
- Jev's one choice question decides the route ("both" is the hybrid route), and the dependence question
  turns entity narrowing on;
- if Jev fails, the LLM router decides instead and the trace says so; with pipeline.jev off the LLM router
  is used directly;
- the LLM router's reply is read conservatively: an unreadable reply is hybrid, never a silent SQL default;
- "neither" sets out_of_scope and no route; the LLM router's split becomes the two sub-questions;
- the database card carries the business description.
No test touches the network: the evidence, Jev and the model are fakes.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))

import front_door as fd  # noqa: E402
from front_door import Evidence, route_for, parse_reply  # noqa: E402

RUN = asyncio.run


def _evidence(turns=None) -> Evidence:
    return Evidence(question="q", turns=turns or [], turn_lines=[], next_turn_index=1, documents=[],
                    document_text=fd.NO_DOCUMENTS, card='Database "cn_x": facts(company, value)', card_status="ok")


def _jev(choice=None, depends=0.1, error=None):
    async def ask(state, questions):
        assert set(questions) == {"route", "documents_depend_on_database"}
        assert questions["route"]["type"] == "choice"
        return SimpleNamespace(
            ok=error is None, error=error, model="jev-1.13", ms=40, usage={},
            choices={"route": {"choice": choice, "confidence": 0.8, "probabilities": {choice: 0.8}}} if choice else {},
            probabilities={"documents_depend_on_database": depends})
    return ask


class _LLM:
    def __init__(self, reply):
        self.reply, self.calls = reply, 0

    async def complete(self, system, messages, max_tokens):
        self.calls += 1
        return SimpleNamespace(text=self.reply, tokens_in=10, tokens_out=5)


@pytest.mark.parametrize("choice,route,needs", [
    ("sql", "sql", ("yes", "no")), ("docs", "docs", ("no", "yes")),
    ("both", "hybrid", ("yes", "yes")), ("neither", "neither", ("no", "no")),
])
def test_jevs_choice_is_the_route(monkeypatch, choice, route, needs):
    monkeypatch.setattr(fd.jev_client, "ask", _jev(choice))
    decision = RUN(fd.decide_jev(_evidence(), "q"))
    assert decision.route == route and decision.arm == "jev_choice" and decision.llm_calls == 0
    assert (decision.parsed.needs_database, decision.parsed.needs_documents) == needs
    assert decision.jev["choice"] == choice


def test_the_dependence_question_turns_narrowing_on_at_its_threshold(monkeypatch):
    threshold = float(fd.cfg.front_door.jev_depends)
    monkeypatch.setattr(fd.jev_client, "ask", _jev("both", depends=threshold))
    assert RUN(fd.decide_jev(_evidence(), "q")).parsed.documents_depend_on_database is True
    monkeypatch.setattr(fd.jev_client, "ask", _jev("both", depends=threshold - 0.01))
    assert RUN(fd.decide_jev(_evidence(), "q")).parsed.documents_depend_on_database is False


def test_a_jev_failure_falls_back_to_the_llm_router_and_says_so(monkeypatch):
    llm = _LLM(json.dumps({"needs_database": "yes", "needs_documents": "no", "out_of_scope": False}))
    monkeypatch.setattr(fd.jev_client, "ask", _jev(error="HTTP 503"))
    monkeypatch.setattr(fd, "get_agent_llm", lambda role: llm)
    decision = RUN(fd.decide_jev(_evidence(), "q"))
    assert decision.route == "sql" and llm.calls == 1
    assert decision.jev["fallback"] == "llm" and decision.jev["jev_error"] == "HTTP 503"


@pytest.mark.parametrize("db,docs,out,route", [
    ("yes", "yes", False, "hybrid"), ("yes", "no", False, "sql"), ("yes", "unsure", False, "sql"),
    ("no", "yes", False, "docs"), ("unsure", "yes", False, "docs"), ("unsure", "unsure", False, "hybrid"),
    ("unsure", "no", False, "sql"), ("no", "unsure", False, "docs"), ("no", "no", False, "neither"),
    ("yes", "yes", True, "neither"),
])
def test_the_llm_routers_table(db, docs, out, route):
    assert route_for(db, docs, out) == route


def test_an_unreadable_llm_reply_is_hybrid_and_flagged():
    parsed = parse_reply("I think it needs the database.", "original")
    assert parsed.parse_ok is False and parsed.standalone_question == "original"
    assert route_for(parsed.needs_database, parsed.needs_documents, parsed.out_of_scope) == "hybrid"


def test_a_failed_llm_call_is_hybrid_never_sql(monkeypatch):
    class _Down:
        async def complete(self, **kwargs):
            raise RuntimeError("endpoint down")
    monkeypatch.setattr(fd, "get_agent_llm", lambda role: _Down())
    decision = RUN(fd.decide_llm(_evidence(), "q"))
    assert decision.route == "hybrid" and decision.llm_error == "endpoint down"


def _node(monkeypatch, decision, flags=None):
    async def gather(state):
        return _evidence()

    async def decide(evidence, question):
        return decision

    monkeypatch.setattr(fd, "gather_evidence", gather)
    monkeypatch.setattr(fd, "decide_jev", decide)
    monkeypatch.setattr(fd, "decide_llm", decide)
    state = {"raw_query": "q", "tenant_id": "t", "principal_id": "p", "flags_snapshot": flags or {}}
    return RUN(fd.front_door_node(state))


def test_neither_sets_out_of_scope_and_no_route(monkeypatch):
    parsed = fd.Parsed(True, "en", "q", False, "no", "no", True, "", "", False)
    update = _node(monkeypatch, fd.Decision("jev_choice", parsed, "neither"))
    assert update["out_of_scope"] is True and "route" not in update
    assert update["front_door"]["route"] == "neither" and "card" in update["front_door"]


def test_the_llm_routers_split_becomes_the_two_sub_questions(monkeypatch):
    parsed = fd.Parsed(True, "en", "q", False, "yes", "yes", False, "db part", "docs part", True)
    update = _node(monkeypatch, fd.Decision("llm", parsed, "hybrid"), {"pipeline.jev": "off"})
    assert update["route"] == "hybrid" and update["subtasks"] == ["db part", "docs part"]
    assert update["docs_depend_on_sql"] is True


def test_with_jev_off_the_llm_router_decides(monkeypatch):
    used = []

    async def gather(state):
        return _evidence()

    async def jev(evidence, question):
        used.append("jev")
        return fd.Decision("jev_choice", parse_reply("{}", "q"), "sql")

    async def llm(evidence, question):
        used.append("llm")
        return fd.Decision("llm", parse_reply("{}", "q"), "sql")

    monkeypatch.setattr(fd, "gather_evidence", gather)
    monkeypatch.setattr(fd, "decide_jev", jev)
    monkeypatch.setattr(fd, "decide_llm", llm)
    base = {"raw_query": "q", "tenant_id": "t", "principal_id": "p"}
    RUN(fd.front_door_node({**base, "flags_snapshot": {"pipeline.jev": "off"}}))
    RUN(fd.front_door_node({**base, "flags_snapshot": {"pipeline.jev": "on"}}))
    assert used == ["llm", "jev"]


def test_the_card_carries_the_business_description():
    card = fd.with_business_context('Database "cn_x": facts(company)', "Annual SEC figures.")
    assert card == 'Workspace description: Annual SEC figures.\nDatabase "cn_x": facts(company)'
    assert fd.with_business_context("card", "  ") == "card"


def test_documents_are_grouped_by_their_best_passage():
    matches = [{"document_id": "a", "title": "A", "score": 0.5, "content_md": "x"},
               {"document_id": "b", "title": "B", "score": 0.7, "content_md": "y"},
               {"document_id": "a", "title": "A", "score": 0.9, "content_md": "best of a"}]
    docs = fd.group_documents(matches, 5, 100)
    assert [(d["document_id"], d["score"], d["excerpt"]) for d in docs] == [("a", 0.9, "best of a"), ("b", 0.7, "y")]
