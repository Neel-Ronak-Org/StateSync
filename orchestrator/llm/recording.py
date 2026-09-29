"""
orchestrator/llm/recording.py
Per-question record of every model call.

WHY THIS EXISTS. One question issues several model calls (normalise, route,
SQL, retries, synthesis) and the answer's trace only carried their sum. A slow
or rate-limited answer could not be explained without the server log. Every
ChatModel handed out by get_agent_llm is wrapped in RecordingModel, which
appends one entry per call (tokens, cached tokens, time, retries, rate-limit
headroom) to a list held in a ContextVar. answer_question starts a list before
running the graph; LangGraph nodes run in child tasks that copy the context,
so they append to that same list, while concurrent questions keep their own.
With no list active (scripts, evals) nothing is recorded.
"""

from __future__ import annotations

import time
from contextvars import ContextVar

from llm.adapters.base import ChatModel, LLMResponse

_CALLS: ContextVar[list[dict] | None] = ContextVar("model_calls", default=None)

_MS_PER_S = 1000  # noqa: literals - seconds to milliseconds


def start_recording() -> list[dict]:
    """Begin recording model calls in this context and return the live list."""
    calls: list[dict] = []
    _CALLS.set(calls)
    return calls


def stop_recording() -> None:
    """End recording in this context: later calls in the same task (a builder's
    next plan, a setup job) must not append to an answer already returned."""
    _CALLS.set(None)


class RecordingModel:
    """A ChatModel that notes each call of the model it wraps."""

    def __init__(self, inner: ChatModel, role: str, model: str) -> None:
        self._inner = inner
        self.role = role
        self.model = model

    async def complete(
        self,
        *,
        system: str,
        messages: list[dict],
        max_tokens: int,
    ) -> LLMResponse:
        t0 = time.monotonic()
        try:
            response = await self._inner.complete(
                system=system, messages=messages, max_tokens=max_tokens
            )
        except Exception as exc:
            self._record({
                "role": self.role,
                "model": self.model,
                "ms": int((time.monotonic() - t0) * _MS_PER_S),
                "error": type(exc).__name__,
            })
            raise
        self._record({
            "role": self.role,
            "model": self.model,
            "tokens_in": response.tokens_in,
            "tokens_out": response.tokens_out,
            "cached_tokens": response.cached_tokens,
            "retries": response.retries,
            "waited_ms": response.waited_ms,
            "ms": int((time.monotonic() - t0) * _MS_PER_S),
            "rate_remaining_tokens": response.rate_remaining_tokens,
        })
        return response

    @staticmethod
    def _record(entry: dict) -> None:
        calls = _CALLS.get()
        if calls is not None:
            calls.append(entry)
