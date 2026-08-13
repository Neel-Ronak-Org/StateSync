"""
orchestrator/llm/adapters/ollama_adapter.py
Local Ollama adapter implementing the ChatModel protocol.

Talks to the Ollama container via its /api/chat HTTP endpoint using httpx async.
No Anthropic key required — the dev default path is fully offline-capable.

Model to pull before first use:
    docker compose exec ollama ollama pull qwen2.5:7b

Token counts: Ollama returns prompt_eval_count and eval_count.
When absent (some model variants), we fall back to 0 — metrics will
undercount in local mode, which is acceptable since local results must
never appear in graded reports anyway.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from config_loader import cfg
from llm.adapters.base import ChatModel, LLMResponse

logger = logging.getLogger(__name__)


class OllamaAdapter:
    """Async Ollama HTTP adapter."""

    def __init__(self, model: str, base_url: str) -> None:
        self.model = model
        self._chat_url = base_url.rstrip("/") + "/api/chat"
        timeout_s: float = float(cfg.models.ollama_timeout_s)
        self._client = httpx.AsyncClient(timeout=timeout_s)
        logger.info(
            "OllamaAdapter ready (model=%s url=%s timeout=%.0fs).",
            model, self._chat_url, timeout_s,
        )

    async def complete(
        self,
        *,
        system: str,
        messages: list[dict],
        max_tokens: int,
    ) -> LLMResponse:
        """
        Send a chat request to Ollama.
        System prompt is prepended as a {"role": "system"} message.
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "stream": False,
            "messages": [{"role": "system", "content": system}] + messages,
            "options": {
                "temperature": cfg.models.temperature,
                "num_predict": max_tokens,
            },
        }

        logger.debug("Ollama request: model=%s max_tokens=%d", self.model, max_tokens)

        response = await self._client.post(self._chat_url, json=payload)
        response.raise_for_status()

        body = response.json()

        text: str = body.get("message", {}).get("content", "").strip()
        tokens_in: int = body.get("prompt_eval_count", 0)
        tokens_out: int = body.get("eval_count", 0)

        logger.debug(
            "Ollama response: len=%d tokens_in=%d tokens_out=%d",
            len(text), tokens_in, tokens_out,
        )

        return LLMResponse(text=text, tokens_in=tokens_in, tokens_out=tokens_out)
