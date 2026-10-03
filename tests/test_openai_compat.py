"""
tests/test_openai_compat.py

WHY THIS EXISTS (llm/adapters/openai_compat_adapter.py; owner's question about free tiers and
routers, 2026-10-03). Results are comparable only when every call comes from the model named.
Pinned here:
- endpoints need https (http only for a router on this machine); specs are "endpoint:model";
- fallback "off" never leaves the first spec (and never even needs the other keys);
  "same_model" moves only to the same model under another provider; "any" moves anywhere;
- an answer from a different model is refused when require_served_match is on;
- a missing key names its secret file.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))

openai = pytest.importorskip("openai")
from llm.adapters import openai_compat_adapter as oc  # noqa: E402
from llm.adapters.base import LLMResponse  # noqa: E402


def test_base_urls_and_specs():
    assert oc.check_base_url("https://openrouter.ai/api/v1")
    assert oc.check_base_url("http://host.docker.internal:20128/v1")
    with pytest.raises(oc.OpenAICompatConfigError):
        oc.check_base_url("http://evil.example.com/v1")
    assert oc.parse_spec("groq:openai/gpt-oss-120b") == ("groq", "openai/gpt-oss-120b")
    with pytest.raises(oc.OpenAICompatConfigError):
        oc.parse_spec("gpt-oss-120b")


def test_same_model_across_provider_names():
    assert oc.same_model("openai/gpt-oss-120b", "gpt-oss-120b")
    assert oc.same_model("gpt-oss-120b-2026-08", "openai/gpt-oss-120b")
    assert not oc.same_model("qwen/qwen3.8-27b", "openai/gpt-oss-120b")


class _Fake:
    def __init__(self, outcome):
        self.outcome, self.calls = outcome, 0

    async def complete(self, **_):
        self.calls += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return LLMResponse(text=self.outcome, tokens_in=1, tokens_out=1)


def _rate_limited():
    import httpx
    req = httpx.Request("POST", "https://x/v1/chat/completions")
    return openai.RateLimitError("429", response=httpx.Response(429, request=req), body=None)


def _run(chain):
    return asyncio.run(chain.complete(system="s", messages=[], max_tokens=10))


def test_fallback_off_never_moves():
    a, b = _Fake(_rate_limited()), _Fake("b")
    with pytest.raises(openai.RateLimitError):
        _run(oc.FallbackChain([("groq", "openai/gpt-oss-120b", a), ("openrouter", "openai/gpt-oss-120b", b)], "off"))
    assert b.calls == 0


def test_same_model_moves_only_to_the_same_model():
    a, other, same = _Fake(_rate_limited()), _Fake("wrong model"), _Fake("same model")
    chain = oc.FallbackChain([("groq", "openai/gpt-oss-120b", a), ("x", "qwen/qwen3.8-27b", other),
                              ("openrouter", "openai/gpt-oss-120b", same)], "same_model")
    assert _run(chain).text == "same model" and other.calls == 0


def test_any_moves_to_the_next_and_a_bad_request_never_moves():
    assert _run(oc.FallbackChain([("a", "m1", _Fake(_rate_limited())), ("b", "m2", _Fake("ok"))], "any")).text == "ok"
    later = _Fake("never")
    with pytest.raises(ValueError):
        _run(oc.FallbackChain([("a", "m1", _Fake(ValueError("bad request"))), ("b", "m2", later)], "any"))
    assert later.calls == 0


def test_a_different_served_model_is_refused_when_strict(monkeypatch, tmp_path):
    (tmp_path / "k").write_text("sk-test")
    monkeypatch.setenv(oc.SECRETS_DIR_ENV, str(tmp_path))
    ep = oc.Endpoint("router", "http://localhost:1/v1", "k", {})
    adapter = oc.OpenAICompatAdapter("openai/gpt-oss-120b", ep, require_served_match=True)

    async def served_other(self, **_):
        return LLMResponse(text="x", tokens_in=1, tokens_out=1, served_model="meta/llama-3-8b")
    monkeypatch.setattr(oc.GroqAdapter, "complete", served_other)
    with pytest.raises(oc.ServedModelMismatch):
        asyncio.run(adapter.complete(system="s", messages=[], max_tokens=5))
    adapter.require_served_match = False
    out = asyncio.run(adapter.complete(system="s", messages=[], max_tokens=5))
    assert out.endpoint == "router" and out.served_model == "meta/llama-3-8b"


def test_a_missing_key_names_its_file(monkeypatch, tmp_path):
    monkeypatch.setenv(oc.SECRETS_DIR_ENV, str(tmp_path))
    with pytest.raises(oc.OpenAICompatConfigError, match="openrouter_api_key"):
        oc.OpenAICompatAdapter._key(oc.Endpoint("openrouter", "https://openrouter.ai/api/v1", "openrouter_api_key", {}))


def test_the_shipped_flags_define_every_role_and_keep_fallback_off():
    from config_loader import flags
    assert str(flags.llm.provider) == "groq"                       # shipped provider unchanged
    assert str(flags.llm.openai_compat.fallback) == "off"
    for role in ("supervisor", "sql", "synthesizer", "memory_summarizer", "normalizer", "decomposer", "judge", "rewriter"):
        oc.parse_spec(getattr(flags.llm.models.openai_compat, role))
