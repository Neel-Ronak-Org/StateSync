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
    # Per-call accounting a provider may report; adapters that cannot keep the defaults.
    cached_tokens: int = 0
    retries: int = 0
    waited_ms: int = 0
    rate_remaining_tokens: int | None = None
    # Which model actually answered, as the server reports it, and through which endpoint
    # (llm/adapters/openai_compat_adapter.py): a router may substitute another model.
    served_model: str | None = None
    endpoint: str | None = None
    # The upstream provider a router reports having served the call (OpenRouter's "provider"
    # field, e.g. "Groq"); None when the endpoint is the provider itself.
    served_provider: str | None = None


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
