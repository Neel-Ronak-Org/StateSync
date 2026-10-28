"""
tests/test_answer_writer.py
The answer writer (orchestrator/nodes/synthesizer.py).

Pinned here:
- "neither" gets the fixed reply with no model call;
- a failed query is explained in plain words, never with the classifier's category or the error text;
- passages reach the model labelled with their document title and page, only the first 8;
- after route recovery the model is told the answer comes from the documents;
- a chart keeps the model's shape but takes its data from the real rows, and a channel naming a column
  that is not in the result is dropped;
- program code in the answer is removed.
The model is a fake.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))

from nodes import synthesizer as syn  # noqa: E402

RUN = asyncio.run


class _LLM:
    def __init__(self, reply):
        self.reply, self.prompts = reply, []

    async def complete(self, system, messages, max_tokens):
        self.prompts.append(messages[0]["content"])
        return SimpleNamespace(text=self.reply, tokens_in=50, tokens_out=10)


def _write(monkeypatch, state, reply='{"answer_text": "ok", "chart": null, "citations": []}'):
    llm = _LLM(reply)
    monkeypatch.setattr(syn, "get_agent_llm", lambda role: llm)
    return RUN(syn.synthesizer_node({"query_id": "q", "raw_query": "question?", **state})), llm


def _passage(n, title="WALMART 2020 10K", page=12):
    return {"content": f"passage {n}", "provenance": {"document_id": "d", "chunk_id": f"c{n}", "page": page,
                                                      "score": 0.5, "title": title}}


def test_neither_gets_the_fixed_reply_with_no_model_call(monkeypatch):
    out, llm = _write(monkeypatch, {"out_of_scope": True})
    assert out["answer_text"] == syn.cfg.front_door.out_of_scope_message and llm.prompts == []


def test_a_failed_query_is_explained_in_plain_words(monkeypatch):
    error = {"attempt": 0, "category": "no_data_source", "engine_code": None,
             "message": "[Layer 1 Gate] Rejected: statement reads no table", "offending_sql": "SELECT 'x'",
             "remediation_hint": "TERMINAL."}
    out, llm = _write(monkeypatch, {"errors": [error], "attempt": 0})
    prompt = llm.prompts[0]
    assert "does not hold the data" in prompt
    assert "no_data_source" not in prompt and "Layer 1" not in prompt and "no_data_source" not in out["answer_text"]


def test_passages_are_labelled_with_title_and_page_and_only_the_window_is_shown(monkeypatch):
    _, llm = _write(monkeypatch, {"retrievals": [_passage(n) for n in range(12)]})
    prompt = llm.prompts[0]
    assert "[Document: WALMART 2020 10K · page 12 · chunk_id=c0" in prompt
    window = int(syn.cfg.retrieval.semantic_top_k)
    assert f"passage {window - 1}" in prompt and f"passage {window}" not in prompt


def test_after_route_recovery_the_writer_says_the_documents_answered(monkeypatch):
    _, llm = _write(monkeypatch, {"retrievals": [_passage(0)], "recovered_from": "sql"})
    assert "The database did not hold this" in llm.prompts[0]


def test_a_chart_takes_its_data_from_the_rows_and_drops_unknown_columns(monkeypatch):
    rows = [{"company": c, "revenue": r} for c, r in (("A", 3), ("B", 2), ("C", 1))]
    reply = json.dumps({"answer_text": "A leads.", "citations": [], "chart": {
        "mark": "bar", "encoding": {"x": {"field": "company", "type": "nominal"},
                                    "y": {"field": "revenue", "type": "quantitative"},
                                    "color": {"field": "invented", "type": "nominal"}}}})
    out, _ = _write(monkeypatch, {"executed_sql": "SELECT 1", "sql_rows": rows, "sql_columns": ["company", "revenue"]},
                    reply)
    assert out["vega_spec"]["data"]["values"] == rows
    assert set(out["vega_spec"]["encoding"]) == {"x", "y"}


def test_program_code_is_removed_from_the_answer(monkeypatch):
    reply = json.dumps({"answer_text": "Here:\n```python\nimport os\nos.system('rm -rf /')\n```", "chart": None,
                        "citations": []})
    out, _ = _write(monkeypatch, {"executed_sql": "SELECT 1", "sql_rows": [{"n": 1}], "sql_columns": ["n"]}, reply)
    assert "os.system" not in out["answer_text"] and "does not produce or execute code" in out["answer_text"]
