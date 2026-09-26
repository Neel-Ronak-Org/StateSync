"""
tests/test_llm_readiness.py
Pins the startup check that the configured local models actually exist
(docs/V3_PLAN.md 0.4).

WHY THIS EXISTS. Startup validation only checked that every role resolved to a
model NAME. The shipped qwen2.5:7b was not installed on the machine's Ollama,
so the stack came up healthy and then failed every question. The check reads
Ollama's /api/tags once at startup and records the result for /health, which
reports it without ever calling the model.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))

import llm.provider as provider  # noqa: E402


def _fake_httpx(monkeypatch, *, tags=None, fail=False):
    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"models": [{"name": n} for n in tags or []]}

    def _get(url, timeout):
        if fail:
            raise OSError("connection refused")
        return _Response()

    monkeypatch.setitem(sys.modules, "httpx", types.SimpleNamespace(get=_get))


@pytest.fixture(autouse=True)
def _reset_readiness(monkeypatch):
    monkeypatch.setattr(provider, "LLM_READINESS", {"ready": True, "detail": None})


def _configured_local_models():
    return sorted({provider._resolve_model("local", role) for role in provider._VALID_ROLES})


def test_missing_model_is_reported_not_hidden(monkeypatch):
    _fake_httpx(monkeypatch, tags=["qwen2.5:14b", "mxbai-embed-large:latest"])

    provider._check_local_models("http://ollama.test:11434")

    assert provider.LLM_READINESS["ready"] is False
    for model in _configured_local_models():
        assert model in provider.LLM_READINESS["detail"]


def test_all_models_present_is_ready(monkeypatch):
    _fake_httpx(monkeypatch, tags=_configured_local_models())

    provider._check_local_models("http://ollama.test:11434")

    assert provider.LLM_READINESS == {"ready": True, "detail": None}


def test_unreachable_ollama_is_reported(monkeypatch):
    _fake_httpx(monkeypatch, fail=True)

    provider._check_local_models("http://ollama.test:11434")

    assert provider.LLM_READINESS["ready"] is False
    assert "unreachable" in provider.LLM_READINESS["detail"]
