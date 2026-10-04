"""
tests/test_synth_short.py
Pins the "short" synthesizer style (flag pipeline.synth_style) and that "full"
is unchanged.

WHY THIS EXISTS: the short style is an experiment to cut answer tokens. Its
guarantees live in code, not in the prompt: column totals are computed over ALL
rows, percentage hints follow a fixed rule, the preview is capped, and the
server never returns a chart (the browser draws it). "full" is the shipped
behaviour and must not move.
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

from config_loader import cfg  # noqa: E402
from nodes import synthesizer  # noqa: E402

CHART = {"mark": "bar", "encoding": {"x": {"field": "city", "type": "nominal"},
                                     "y": {"field": "n", "type": "quantitative"}}}


class _FakeLLM:
    def __init__(self, reply: dict):
        self.reply = reply
        self.system: str | None = None
        self.user: str | None = None

    async def complete(self, system, messages, max_tokens):
        self.system = system
        self.user = messages[0]["content"]
        return SimpleNamespace(text=json.dumps(self.reply), tokens_in=1, tokens_out=1)


def _run(monkeypatch, style: str | None, rows: list[dict], columns: list[str]):
    llm = _FakeLLM({"answer_text": "ok", "chart": CHART, "citations": []})
    monkeypatch.setattr(synthesizer, "get_agent_llm", lambda role: llm)
    state = {
        "query_id": "q", "raw_query": "how many?", "sql_rows": rows,
        "sql_columns": columns, "executed_sql": "SELECT 1",
        "flags_snapshot": {"pipeline.synth_style": style} if style else {},
    }
    return llm, asyncio.run(synthesizer.synthesizer_node(state))


def test_column_summary_math():
    rows = [{"c": "a", "n": 1, "f": 0.5}, {"c": "b", "n": 5, "f": None}, {"c": "c", "n": 3, "f": 0.25}]
    s = synthesizer.column_summary(rows, ["c", "n", "f"])
    assert s == {
        "n": {"count": 3, "min": 1, "max": 5, "sum": 9},
        "f": {"count": 2, "min": 0.25, "max": 0.5, "sum": 0.75},
    }


def test_column_summary_skips_text_bool_and_all_null():
    rows = [{"t": "1", "b": True, "z": None}, {"t": "2", "b": False, "z": None}]
    assert synthesizer.column_summary(rows, ["t", "b", "z"]) == {}


def test_percentage_hint_positive():
    rows = [{"conversion_rate": 0.0453}, {"conversion_rate": 1}, {"conversion_rate": 0}]
    hints = synthesizer.percentage_hints(rows, ["conversion_rate"])
    assert hints == ["Present conversion_rate as a percentage (e.g. 0.0453 → 4.5%)."]


def test_percentage_hint_negative_cases():
    assert synthesizer.percentage_hints([{"rate": 1.2}], ["rate"]) == []      # outside [0, 1]
    assert synthesizer.percentage_hints([{"rate": -0.1}], ["rate"]) == []
    assert synthesizer.percentage_hints([{"amount": 0.5}], ["amount"]) == []  # name does not say proportion
    assert synthesizer.percentage_hints([{"rate": "0.5"}], ["rate"]) == []    # not numeric
    assert synthesizer.percentage_hints([{"rate": None}], ["rate"]) == []     # no values


def test_short_prompt_has_no_chart_instructions_and_uses_sentence_cap():
    prompt = synthesizer._short_system_prompt()
    assert f"at most {cfg.pipeline.synth_short_max_sentences} sentences" in prompt
    assert "{max_sentences}" not in prompt
    assert '"chart": null' in prompt
    for word in ('"mark"', "encoding", "Typical choices", "quantitative", "data.values"):
        assert word not in prompt


def test_short_style_caps_preview_summarises_all_rows_and_drops_chart(monkeypatch):
    n = cfg.pipeline.synth_short_preview_rows + 15
    rows = [{"city": f"c{i}", "n": i, "win_rate": 0.5} for i in range(n)]
    llm, out = _run(monkeypatch, "short", rows, ["city", "n", "win_rate"])
    assert out["vega_spec"] is None
    assert llm.system == synthesizer._short_system_prompt()
    assert f"first {cfg.pipeline.synth_short_preview_rows} rows" in llm.user
    assert "c0" in llm.user and f"c{n - 1}" not in llm.user
    assert f"n: count={n} min=0 max={n - 1} sum={sum(range(n))}" in llm.user
    assert "Present win_rate as a percentage" in llm.user
    assert "A chart IS appropriate" not in llm.user


def test_full_style_unchanged(monkeypatch):
    rows = [{"city": f"c{i}", "n": i} for i in range(5)]
    for style in (None, "full"):
        llm, out = _run(monkeypatch, style, rows, ["city", "n"])
        assert llm.system == synthesizer._SYSTEM_PROMPT
        assert "A chart IS appropriate" in llm.user
        assert "Column summary" not in llm.user
        assert out["vega_spec"] is not None and out["vega_spec"]["mark"] == "bar"


class _EmptyThenAnswer:
    def __init__(self, replies: list[str]):
        self.replies = replies
        self.calls = 0

    async def complete(self, system, messages, max_tokens):
        text = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        return SimpleNamespace(text=text, tokens_in=10, tokens_out=3)


def _answer(monkeypatch, llm):
    monkeypatch.setattr(synthesizer, "get_agent_llm", lambda role: llm)
    state = {"query_id": "q", "raw_query": "how many?", "sql_rows": [{"n": 3}], "sql_columns": ["n"],
             "executed_sql": "SELECT 3 AS n", "flags_snapshot": {}}
    return asyncio.run(synthesizer.synthesizer_node(state))


def test_an_empty_answer_is_asked_for_once_more(monkeypatch):
    llm = _EmptyThenAnswer(["", json.dumps({"answer_text": "There are 3.", "chart": None, "citations": []})])
    out = _answer(monkeypatch, llm)
    assert llm.calls == 2 and out["answer_text"] == "There are 3."
    assert out["metrics"]["llm_calls"] == 2 and out["metrics"]["tokens_in"] == 20


def test_a_normal_answer_is_asked_for_once(monkeypatch):
    llm = _EmptyThenAnswer([json.dumps({"answer_text": "There are 3.", "chart": None, "citations": []})])
    out = _answer(monkeypatch, llm)
    assert llm.calls == 1 and out["metrics"]["llm_calls"] == 1
