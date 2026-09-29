"""
orchestrator/llm/adapters/groq_adapter.py
Groq adapter implementing the ChatModel protocol.

Fifth implementation behind the D2 provider seam (BD1), after Ollama,
Anthropic, Azure and Gemini. No node changes: switching is
`flags.llm.provider = "groq"` plus a key.

WHY GROQ. Chosen by the owner on 2026-09-26 as the working provider for v3
(docs/V3_PLAN.md, Phase 0.4): the configured local model was not installed on
the development machine, and a hosted model is v3's demo default anyway. Groq
serves an OpenAI-compatible API, so this uses openai.AsyncOpenAI pointed at
Groq's base URL -- the same package the Azure adapter already depends on.

RATE LIMITS ARE PER MODEL AND TIGHT. Measured from response headers on the
owner's key (2026-09-26): 8,000 tokens per minute and 1,000 requests per day,
for EACH model. One question issues several calls and the SQL call carries the
schema, so a 429 in the middle of a question is expected, not exceptional. The
adapter waits for the server's retry-after and retries (models.groq_*), so a
rate limit never scores as a wrong answer. The SDK's own retry is switched off
because it ignores retry-after values above 60s and caps its own backoff at a
few seconds -- shorter than a per-minute token window.

REASONING MODELS. openai/gpt-oss-* spend reasoning tokens out of
max_completion_tokens, but the ChatModel contract says max_tokens means VISIBLE
output (gemini_adapter.py records what happens otherwise: an empty answer).
Models listed in flags.llm.groq.reasoning_effort get that effort level and
models.groq_reasoning_headroom_tokens added to their budget. Groq returns the
reasoning in a separate field, so .text is the answer alone.

Key: GROQ_API_KEY_FILE (Docker secret, default /run/secrets/groq_api_key),
then the GROQ_API_KEY env var -- read once at construction, and only when this
provider is selected, like every other hosted adapter.
"""

from __future__ import annotations

import asyncio
import logging
import os

from config_loader import cfg, flags
from llm.adapters.base import LLMResponse

logger = logging.getLogger(__name__)

_PLACEHOLDER_PREFIX = "REPLACE"


def _max_total_wait_s() -> float:
    """models.groq_max_total_wait_s as an accessor so tests can lower it (cfg is read-only)."""
    return float(cfg.models.groq_max_total_wait_s)


class GroqConfigError(RuntimeError):
    """Groq is selected but its key or configuration is unusable.

    A distinct type so validate_llm_config() can say 'the provider is wired,
    your setup is incomplete' rather than 'the provider does not exist'.
    """


class GroqAdapter:
    """Async Groq adapter. `model` is a published Groq model id."""

    def __init__(self, model: str) -> None:
        self.model = model
        api_key = self._read_api_key()
        base_url = self._read_base_url()

        try:
            from openai import AsyncOpenAI
        except ImportError as exc:      # pragma: no cover - environment-dependent
            raise GroqConfigError(
                "flags.llm.provider = 'groq' but the `openai` package is not "
                "installed in this image. It is declared in "
                "orchestrator/pyproject.toml; rebuild the orchestrator image "
                "(`docker compose build orchestrator`) and restart."
            ) from exc

        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=float(cfg.models.groq_timeout_s),
            max_retries=0,              # retries are handled in complete(); see module docstring
        )
        self._reasoning_effort = self._effort_for(model)
        logger.info(
            "GroqAdapter ready (model=%s reasoning_effort=%s).",
            model, self._reasoning_effort or "not sent",
        )

    # ── Config/credential helpers (static so validate_llm_config can call them) ──
    @staticmethod
    def _read_api_key() -> str:
        secret_file = os.environ.get("GROQ_API_KEY_FILE", "/run/secrets/groq_api_key")
        try:
            key = open(secret_file).read().strip()
            if key and not key.startswith(_PLACEHOLDER_PREFIX):
                return key
        except OSError:
            pass

        key = os.environ.get("GROQ_API_KEY", "").strip()
        if not key:
            raise GroqConfigError(
                "Groq API key not found. Put it in secrets/groq_api_key (mounted "
                "as GROQ_API_KEY_FILE) or set the GROQ_API_KEY env var. Keys are "
                "free at console.groq.com. To run without one, set "
                "flags.llm.provider = 'local' in config/flags.toml."
            )
        return key

    @staticmethod
    def _read_base_url() -> str:
        base_url = str(getattr(flags.llm.groq, "base_url", "") or "").strip()
        if not base_url.startswith("https://"):
            raise GroqConfigError(
                f"flags.llm.groq.base_url must be an https:// URL, got {base_url!r}."
            )
        return base_url

    @staticmethod
    def _effort_for(model: str) -> str:
        """Reasoning effort for this model, or '' to omit the parameter.

        Only models listed in flags.llm.groq.reasoning_effort receive it: the
        accepted values differ by model family, so sending one blindly to a
        model that does not reason would be rejected.
        """
        table = flags.llm.groq.get("reasoning_effort")
        if table is None:
            return ""
        return str(table.get(model, "") or "").strip()

    @staticmethod
    def _retry_delay(exc: Exception, fallback: float) -> float:
        """The server's retry-after if it sent one, else the backoff delay."""
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None) or {}
        try:
            delay = float(headers.get("retry-after", ""))
        except (TypeError, ValueError):
            delay = fallback
        return min(max(delay, 0.0), float(cfg.models.groq_retry_max_wait_s))

    # ── ChatModel.complete ────────────────────────────────────────────────────
    async def complete(
        self,
        *,
        system: str,
        messages: list[dict],
        max_tokens: int,
    ) -> LLMResponse:
        """
        OpenAI-shaped: the system prompt is the first message. Rate limits,
        server errors and dropped connections are retried; anything else
        (a bad request, a bad key) is raised at once.
        """
        from openai import APIConnectionError, InternalServerError, RateLimitError

        budget = max_tokens
        extra: dict = {}
        if self._reasoning_effort:
            budget += int(cfg.models.groq_reasoning_headroom_tokens)
            extra["reasoning_effort"] = self._reasoning_effort

        attempts = int(cfg.models.groq_max_retries) + 1
        backoff = float(cfg.models.groq_retry_base_delay_s)
        waited_s = 0.0
        retries = 0
        headers: dict = {}
        for attempt in range(attempts):
            try:
                raw = await self._client.chat.completions.with_raw_response.create(
                    model=self.model,
                    max_completion_tokens=budget,
                    temperature=cfg.models.temperature,
                    messages=[{"role": "system", "content": system}] + messages,
                    **extra,
                )
                headers = raw.headers
                completion = raw.parse()
                break
            except (RateLimitError, InternalServerError, APIConnectionError) as exc:
                if attempt == attempts - 1:
                    raise
                delay = self._retry_delay(exc, backoff)
                if waited_s + delay > _max_total_wait_s():
                    logger.warning(
                        "Groq gave up after %.1fs of waiting (models.groq_max_total_wait_s=%.1f, "
                        "model=%s); raising %s.",
                        waited_s, _max_total_wait_s(), self.model, type(exc).__name__,
                    )
                    raise
                logger.warning(
                    "Groq call failed transiently (%s, attempt %d/%d, model=%s); retrying in %.1fs.",
                    type(exc).__name__, attempt + 1, attempts, self.model, delay,
                )
                await asyncio.sleep(delay)
                waited_s += delay
                retries += 1
                backoff *= 2

        choice = completion.choices[0]
        text = (choice.message.content or "").strip()
        if not text and choice.finish_reason == "length":
            logger.warning(
                "Groq returned no visible text: the %d-token budget ran out first "
                "(model=%s). Raise models.groq_reasoning_headroom_tokens or the "
                "role's max_tokens cap.",
                budget, self.model,
            )
        usage = completion.usage
        details = getattr(usage, "prompt_tokens_details", None)
        try:
            remaining: int | None = int(headers.get("x-ratelimit-remaining-tokens", ""))
        except (TypeError, ValueError):
            remaining = None
        return LLMResponse(
            text=text,
            # completion_tokens includes reasoning tokens: they are billed and
            # they count against the per-minute token limit, so they belong in
            # the accounting. A missing usage block undercounts rather than crashes.
            tokens_in=getattr(usage, "prompt_tokens", 0) or 0,
            tokens_out=getattr(usage, "completion_tokens", 0) or 0,
            cached_tokens=getattr(details, "cached_tokens", 0) or 0,
            retries=retries,
            waited_ms=int(waited_s * 1000),  # noqa: literals - seconds to milliseconds
            rate_remaining_tokens=remaining,
        )
