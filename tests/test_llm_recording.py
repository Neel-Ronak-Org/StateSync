"""
tests/test_llm_recording.py
Pins per-question model-call recording (orchestrator/llm/recording.py).

WHY THIS EXISTS. The answer's trace lists every model call so a slow or
rate-limited answer can be explained. That only works if calls land in the list
of THEIR question: two questions in flight at once must not mix entries, a
failed call must be recorded and still raise, and code that never started a
recording (evals, scripts) must be unaffected.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))

from llm import provider  # noqa: E402
from llm.adapters.base import LLMResponse  # noqa: E402
from llm import recording  # noqa: E402
from llm.recording import RecordingModel, start_recording  # noqa: E402


class _FakeModel:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    async def complete(self, *, system, messages, max_tokens):
        await asyncio.sleep(0)
        if self.error:
            raise self.error
        return LLMResponse(text="ok", tokens_in=10, tokens_out=4, cached_tokens=2,
                           retries=1, waited_ms=300, rate_remaining_tokens=900)


async def _call(model: RecordingModel):
    return await model.complete(system="s", messages=[], max_tokens=8)


def test_records_nothing_without_an_active_recording():
    async def run():
        # A fresh task so no other test's recording leaks in through the context.
        return await asyncio.create_task(_call(RecordingModel(_FakeModel(), "sql", "m")))

    assert asyncio.run(run()).text == "ok"


def test_records_the_call_in_order_while_active():
    async def run():
        calls = start_recording()
        await _call(RecordingModel(_FakeModel(), "normalizer", "m1"))
        await _call(RecordingModel(_FakeModel(), "sql", "m2"))
        return calls

    calls = asyncio.run(run())
    assert [c["role"] for c in calls] == ["normalizer", "sql"]
    first = calls[0]
    assert first["model"] == "m1"
    assert (first["tokens_in"], first["tokens_out"], first["cached_tokens"]) == (10, 4, 2)
    assert (first["retries"], first["waited_ms"], first["rate_remaining_tokens"]) == (1, 300, 900)
    assert first["ms"] >= 0


def test_concurrent_recordings_stay_separate():
    async def question(role: str):
        calls = start_recording()
        model = RecordingModel(_FakeModel(), role, "m")
        await _call(model)
        await _call(model)
        return calls

    async def run():
        return await asyncio.gather(question("a"), question("b"))

    a, b = asyncio.run(run())
    assert [c["role"] for c in a] == ["a", "a"]
    assert [c["role"] for c in b] == ["b", "b"]


def test_child_tasks_share_the_recording():
    async def run():
        calls = start_recording()
        model = RecordingModel(_FakeModel(), "sql", "m")
        await asyncio.gather(asyncio.create_task(_call(model)), asyncio.create_task(_call(model)))
        return calls

    assert len(asyncio.run(run())) == 2


def test_exception_is_recorded_and_reraised():
    async def run():
        calls = start_recording()
        with pytest.raises(ValueError):
            await _call(RecordingModel(_FakeModel(ValueError("boom")), "sql", "m"))
        return calls

    calls = asyncio.run(run())
    assert len(calls) == 1
    assert calls[0]["error"] == "ValueError"
    assert calls[0]["role"] == "sql" and "tokens_in" not in calls[0]


def test_get_agent_llm_wraps_the_providers_adapter(monkeypatch):
    import llm.adapters.ollama_adapter as ollama

    made = []

    class _Adapter:
        def __init__(self, model, base_url):
            made.append((model, base_url))

    monkeypatch.setattr(ollama, "OllamaAdapter", _Adapter)
    # Config is read-only, so swap the module's view of it rather than mutate it.
    monkeypatch.setattr(provider, "flags", SimpleNamespace(
        llm=SimpleNamespace(provider="local", ollama_url="http://ollama.test")))
    monkeypatch.setattr(provider, "_resolve_model", lambda prov, role: f"{prov}-{role}-model")
    provider.get_agent_llm.cache_clear()
    try:
        llm = provider.get_agent_llm("sql")
    finally:
        provider.get_agent_llm.cache_clear()

    assert isinstance(llm, RecordingModel)
    assert isinstance(llm._inner, _Adapter)
    assert made == [("local-sql-model", "http://ollama.test")]
    assert llm.role == "sql" and llm.model == "local-sql-model"


def test_stop_recording_ends_the_list():
    """After an answer is returned, later calls in the same task are not appended to it."""
    async def run():
        calls = recording.start_recording()
        model = recording.RecordingModel(_FakeModel(), "sql", "m")
        await model.complete(system="s", messages=[], max_tokens=1)
        recording.stop_recording()
        await model.complete(system="s", messages=[], max_tokens=1)
        return calls
    assert len(asyncio.run(run())) == 1
