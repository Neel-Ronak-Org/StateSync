"""
tests/test_groq_adapter.py
Pins the Groq adapter's behaviour at the two places a hosted, rate-limited,
reasoning model breaks a pipeline silently.

WHY THIS EXISTS.

1. Rate limits. The owner's key allows 8,000 tokens per minute per model, and
   one question issues several calls, so HTTP 429 mid-question is routine. If
   the adapter gave up, a rate limit would surface as a failed question -- a
   measurement artifact this project has paid for before (see
   gemini_adapter.py). The adapter must wait for the server's retry-after and
   retry, and must NOT retry errors that retrying cannot fix.
2. Reasoning tokens. openai/gpt-oss-* spend reasoning out of the same budget
   as the answer. The ChatModel contract says max_tokens means VISIBLE output,
   so reasoning models get headroom on top -- and non-reasoning models must not
   be sent a reasoning_effort they would reject.

The OpenAI client is replaced with a recorder; no network is used.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

openai = pytest.importorskip("openai")   # declared in orchestrator/pyproject.toml; CI installs it
# The HTTP library the SDK itself builds errors from: openai 3.x moved to
# httpx2, earlier releases used httpx. The test must use the same one.
try:
    import httpx2 as httpx  # noqa: E402
except ImportError:          # pragma: no cover - older openai releases
    import httpx  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))

import llm.adapters.groq_adapter as groq_mod  # noqa: E402
from config_loader import cfg  # noqa: E402
from llm.adapters.groq_adapter import GroqAdapter, GroqConfigError  # noqa: E402

REASONING_MODEL = "openai/gpt-oss-120b"     # listed in flags.llm.groq.reasoning_effort
PLAIN_MODEL = "qwen/qwen3.8-27b"            # not listed


def _completion(text: str, finish_reason: str = "stop"):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason=finish_reason)],
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7),
    )


def _rate_limit(retry_after: str | None):
    headers = {"retry-after": retry_after} if retry_after is not None else {}
    response = httpx.Response(429, headers=headers,
                              request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions"))
    return openai.RateLimitError("rate limited", response=response, body=None)


class _Recorder:
    """Stands in for client.chat.completions; replays a scripted list of outcomes."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def key_file(tmp_path, monkeypatch):
    path = tmp_path / "groq_api_key"
    path.write_text("gsk_test_not_a_real_key\n")
    monkeypatch.setenv("GROQ_API_KEY_FILE", str(path))
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    return path


def _adapter(model: str, outcomes, monkeypatch) -> tuple[GroqAdapter, _Recorder, list[float]]:
    adapter = GroqAdapter(model=model)
    recorder = _Recorder(outcomes)
    adapter._client = SimpleNamespace(chat=SimpleNamespace(completions=recorder))
    sleeps: list[float] = []

    async def _fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(groq_mod.asyncio, "sleep", _fake_sleep)
    return adapter, recorder, sleeps


def _complete(adapter: GroqAdapter, max_tokens: int = 128):
    return asyncio.run(adapter.complete(system="sys", messages=[{"role": "user", "content": "q"}],
                                        max_tokens=max_tokens))


def test_reasoning_model_gets_effort_and_headroom(key_file, monkeypatch):
    adapter, recorder, _ = _adapter(REASONING_MODEL, [_completion('{"route":"sql"}')], monkeypatch)
    result = _complete(adapter, max_tokens=128)

    sent = recorder.calls[0]
    assert sent["reasoning_effort"] == "low"
    assert sent["max_completion_tokens"] == 128 + int(cfg.models.groq_reasoning_headroom_tokens)
    assert sent["messages"][0] == {"role": "system", "content": "sys"}
    assert result.text == '{"route":"sql"}'
    assert (result.tokens_in, result.tokens_out) == (11, 7)


def test_plain_model_gets_neither_effort_nor_headroom(key_file, monkeypatch):
    adapter, recorder, _ = _adapter(PLAIN_MODEL, [_completion("ok")], monkeypatch)
    _complete(adapter, max_tokens=128)

    sent = recorder.calls[0]
    assert "reasoning_effort" not in sent
    assert sent["max_completion_tokens"] == 128


def test_rate_limit_waits_for_retry_after_then_succeeds(key_file, monkeypatch):
    adapter, recorder, sleeps = _adapter(
        PLAIN_MODEL, [_rate_limit("3"), _rate_limit("5"), _completion("ok")], monkeypatch)
    result = _complete(adapter)

    assert result.text == "ok"
    assert len(recorder.calls) == 3
    assert sleeps == [3.0, 5.0]


def test_rate_limit_without_retry_after_falls_back_to_doubling_backoff(key_file, monkeypatch):
    adapter, _, sleeps = _adapter(
        PLAIN_MODEL, [_rate_limit(None), _rate_limit(None), _completion("ok")], monkeypatch)
    _complete(adapter)

    base = float(cfg.models.groq_retry_base_delay_s)
    assert sleeps == [base, base * 2]


def test_retry_after_is_capped(key_file, monkeypatch):
    adapter, _, sleeps = _adapter(PLAIN_MODEL, [_rate_limit("9999"), _completion("ok")], monkeypatch)
    _complete(adapter)

    assert sleeps == [float(cfg.models.groq_retry_max_wait_s)]


def test_gives_up_after_the_configured_retries(key_file, monkeypatch):
    attempts = int(cfg.models.groq_max_retries) + 1
    adapter, recorder, _ = _adapter(PLAIN_MODEL, [_rate_limit("1")] * attempts, monkeypatch)

    with pytest.raises(openai.RateLimitError):
        _complete(adapter)
    assert len(recorder.calls) == attempts


def test_bad_request_is_not_retried(key_file, monkeypatch):
    response = httpx.Response(400, request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions"))
    bad = openai.BadRequestError("bad request", response=response, body=None)
    adapter, recorder, sleeps = _adapter(PLAIN_MODEL, [bad], monkeypatch)

    with pytest.raises(openai.BadRequestError):
        _complete(adapter)
    assert len(recorder.calls) == 1
    assert sleeps == []


def test_missing_key_fails_with_an_actionable_message(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY_FILE", str(tmp_path / "does_not_exist"))
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    with pytest.raises(GroqConfigError, match="secrets/groq_api_key"):
        GroqAdapter(model=PLAIN_MODEL)


def test_placeholder_key_is_treated_as_missing(tmp_path, monkeypatch):
    path = tmp_path / "groq_api_key"
    path.write_text("REPLACE_ME")
    monkeypatch.setenv("GROQ_API_KEY_FILE", str(path))
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    with pytest.raises(GroqConfigError):
        GroqAdapter(model=PLAIN_MODEL)
