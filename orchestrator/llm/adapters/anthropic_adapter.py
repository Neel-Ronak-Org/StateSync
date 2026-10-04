"""
orchestrator/llm/adapters/anthropic_adapter.py
Anthropic API adapter implementing the ChatModel protocol.

Key read from /run/secrets/anthropic_api_key (Docker secret) or the
ANTHROPIC_API_KEY env var. The read is done once at construction time and
only when provider = "anthropic" — local mode never instantiates this class.

Uses anthropic.AsyncAnthropic so completions are truly non-blocking.
"""

from __future__ import annotations

import logging
import os

import anthropic as _anthropic

from config_loader import cfg
from llm.adapters.base import LLMResponse

logger = logging.getLogger(__name__)


class AnthropicAdapter:
    """Async Anthropic API adapter."""

    def __init__(self, model: str) -> None:
        self.model = model
        api_key = self._read_api_key()
        self._client = _anthropic.AsyncAnthropic(api_key=api_key)
        logger.info("AnthropicAdapter ready (model=%s).", model)

    # ── Key helper (staticmethod so validate_llm_config can call it too) ──────
    @staticmethod
    def _read_api_key() -> str:
        """
        Read the Anthropic API key from the Docker secret file or env var.
        Raises RuntimeError if the key is absent.
        """
        secret_file = os.environ.get(
            "ANTHROPIC_API_KEY_FILE",
            "/run/secrets/anthropic_api_key",
        )
        try:
            key = open(secret_file).read().strip()
            if key and not key.startswith("sk-ant-REPLACE"):
                return key
        except OSError:
            pass

        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise RuntimeError(
                "Anthropic API key not found. "
                "Set ANTHROPIC_API_KEY_FILE (Docker secret) or ANTHROPIC_API_KEY env var. "
                "If you want to run without a key, set flags.llm.provider = 'local' "
                "in config/flags.toml."
            )
        return key

    # ── ChatModel.complete ────────────────────────────────────────────────────
    async def complete(
        self,
        *,
        system: str,
        messages: list[dict],
        max_tokens: int,
    ) -> LLMResponse:
        message = await self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=cfg.models.temperature,
            system=system,
            messages=messages,
        )
        text = message.content[0].text.strip()
        return LLMResponse(
            text=text,
            tokens_in=message.usage.input_tokens,
            tokens_out=message.usage.output_tokens,
        )
