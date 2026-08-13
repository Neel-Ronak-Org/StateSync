"""
orchestrator/llm/adapters/base.py
ChatModel protocol — the interface every adapter must satisfy.

Nodes depend on ChatModel, not on any concrete SDK.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class LLMResponse:
    """Normalised response from any LLM adapter."""
    text: str
    tokens_in: int
    tokens_out: int


@runtime_checkable
class ChatModel(Protocol):
    """
    Minimal chat interface. Adapters implement this; nodes call it.
    All completions are async to avoid blocking the event loop.
    """

    async def complete(
        self,
        *,
        system: str,
        messages: list[dict],
        max_tokens: int,
    ) -> LLMResponse:
        """
        Send a chat completion request and return a normalised LLMResponse.

        Args:
            system:     System prompt string.
            messages:   List of {"role": ..., "content": ...} dicts.
            max_tokens: Upper bound on output tokens.

        Returns:
            LLMResponse with .text, .tokens_in, .tokens_out.
        """
        ...
