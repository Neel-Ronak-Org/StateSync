"""
orchestrator/llm/adapters/gemini_adapter.py
Google Gemini adapter implementing the ChatModel protocol.

Fourth implementation behind the D2 provider seam (BD1), after Ollama,
Anthropic and Azure. No node changes: switching is
`flags.llm.provider = "gemini"` plus a key.

WHY THIS EXISTS AT ALL. Azure was the intended graded provider and the
subscription turned out to be unable to provision AI resources -- an
Azure-managed region policy on Azure for Students that blocks Cognitive
Services and AI Foundry accounts while permitting resource groups. That is a
provider being unavailable, not an architecture problem, and the seam is what
makes it a config-and-one-class problem instead of a rewrite.

THE FREE TIER IS RATE-LIMITED, WHICH IS THE THING THAT WILL ACTUALLY BITE.
A 25-query diagnose_m4 run issues roughly 5.2 LLM calls per query -- about 130
calls. Free-tier Gemini is limited per MINUTE, not per month, so a naive loop
gets a burst of 429s partway through and reports them as model failures. This
adapter therefore retries 429/resource-exhausted with exponential backoff, and
the retry budget is config, not a literal (A0.2). A benchmark that silently
scores rate limiting as incorrect SQL would be a measurement artifact of
exactly the kind this project keeps finding.

Key read from /run/secrets/gemini_api_key (Docker secret) or GEMINI_API_KEY /
GOOGLE_API_KEY, once at construction, and only when this provider is selected.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

from config_loader import cfg, flags
from llm.adapters.base import ChatModel, LLMResponse

logger = logging.getLogger(__name__)

_PLACEHOLDER_PREFIX = "REPLACE"

# Substrings that mark a rate-limit / quota rejection across the SDK's error
# shapes. Matched case-insensitively against the exception text because the
# SDK raises several types for the same underlying condition.
# 429 (per-minute free-tier limit) AND 503 (model temporarily overloaded) are
# both TRANSIENT and both retryable. Observed live while choosing a model:
# gemini-3.7-flash returned 503 UNAVAILABLE while 3.6-flash served fine, and
# every "pro" model returned 429 -- the free tier has no pro quota at all.
# Without 503 here, one overloaded moment mid-run scores as a wrong answer.
_RATE_LIMIT_MARKERS = ("429", "resource_exhausted", "resource exhausted",
                       "rate limit", "quota", "too many requests",
                       "503", "unavailable", "overloaded")

# How much of a retry exception to put in the log line. Presentation, not
# behaviour: no caller reads it and no result depends on it, so it is not a
# configurable constant under A0.2.
_LOG_EXCERPT_CHARS = 120  # noqa: literals — log truncation, not a domain constant


# Pacing state is MODULE-level and keyed by MODEL, not per adapter instance.
# provider.get_agent_llm is lru_cached per ROLE, so two roles sharing one model
# hold two adapter objects -- a per-instance limiter would let them each spend
# the model's full RPM budget and burst to double the limit.
# Unit conversion, not a domain constant: RPM is defined per minute.
_SECONDS_PER_MINUTE = 60.0  # noqa: literals — unit conversion, not configurable

_LAST_CALL: dict[str, float] = {}
_PACE_LOCK = asyncio.Lock()


async def _pace(model: str, rpm: int) -> None:
    """Sleep until this MODEL may legally be called again.

    Prevents the 429 rather than reacting to it. The free tier's binding limit
    is per-minute, and the pipeline issues ~4 calls per query with no natural
    gap between them.
    """
    if rpm <= 0:
        return
    min_gap = _SECONDS_PER_MINUTE / rpm
    async with _PACE_LOCK:
        now = time.monotonic()
        last = _LAST_CALL.get(model)
        # `None` rather than a large negative sentinel: a sentinel is a magic
        # number the config lint correctly objects to, and "never called" is a
        # different state from "called long ago", not a very old timestamp.
        wait = min_gap if last is None else min_gap - (now - last)
        if wait > 0 and last is not None:
            logger.debug("Pacing %s: sleeping %.1fs to stay under %d RPM", model, wait, rpm)
            await asyncio.sleep(wait)
        _LAST_CALL[model] = time.monotonic()


class GeminiConfigError(RuntimeError):
    """Gemini is selected but its configuration or credentials are unusable."""


class GeminiAdapter:
    """Async Gemini adapter with free-tier-aware retry."""

    def __init__(self, model: str) -> None:
        self.model = model
        api_key = self._read_api_key()

        try:
            from google import genai
        except ImportError as exc:      # pragma: no cover - environment-dependent
            raise GeminiConfigError(
                "flags.llm.provider = 'gemini' but the `google-genai` package "
                "is not installed in this image. It is declared in "
                "orchestrator/pyproject.toml; rebuild the orchestrator image "
                "(`docker compose build orchestrator`) and restart. Until then, "
                "set flags.llm.provider = 'local'."
            ) from exc

        self._genai = genai
        self._client = genai.Client(api_key=api_key)
        logger.info("GeminiAdapter ready (model=%s).", model)

    # ── Credential helper (static so validate_llm_config can call it) ─────────
    @staticmethod
    def _read_api_key() -> str:
        secret_file = os.environ.get(
            "GEMINI_API_KEY_FILE", "/run/secrets/gemini_api_key",
        )
        try:
            key = open(secret_file).read().strip()
            if key and not key.startswith(_PLACEHOLDER_PREFIX):
                return key
        except OSError:
            pass

        for env_name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
            key = os.environ.get(env_name, "")
            if key:
                return key

        raise GeminiConfigError(
            "Gemini API key not found. Set GEMINI_API_KEY_FILE (Docker secret, "
            "default /run/secrets/gemini_api_key), or the GEMINI_API_KEY / "
            "GOOGLE_API_KEY env var. Get a free key from Google AI Studio "
            "(aistudio.google.com/apikey). To run without one, set "
            "flags.llm.provider = 'local' in config/flags.toml."
        )

    # ── Message mapping ───────────────────────────────────────────────────────
    @staticmethod
    def _to_contents(messages: list[dict]) -> list[dict]:
        """
        ChatModel messages -> Gemini `contents`.

        Gemini names the assistant turn "model", not "assistant", and wraps
        text in a parts list. A silently mis-mapped role would make multi-turn
        correction-loop prompts read as if the model had never spoken.
        """
        contents = []
        for m in messages:
            role = "model" if m.get("role") == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": str(m.get("content", ""))}]})
        return contents

    @staticmethod
    def _is_rate_limit(exc: Exception) -> bool:
        text = f"{type(exc).__name__} {exc}".lower()
        return any(marker in text for marker in _RATE_LIMIT_MARKERS)

    # ── ChatModel.complete ────────────────────────────────────────────────────
    async def complete(
        self,
        *,
        system: str,
        messages: list[dict],
        max_tokens: int,
    ) -> LLMResponse:
        """
        Gemini takes the system prompt as `system_instruction` in the request
        config rather than as a message, closer to Anthropic's shape than
        OpenAI's. The ChatModel contract is unchanged for callers.
        """
        from google.genai import types

        # THINKING TOKENS COUNT AGAINST max_output_tokens, which breaks the
        # ChatModel contract every other adapter honours: max_tokens means
        # VISIBLE output. Measured on gemini-3.6-flash at this project's own
        # budgets: max_output_tokens=128 (max_tokens_supervisor) spent 102-119
        # tokens thinking and returned '' -- the Supervisor would have emitted
        # nothing and routing would fail. thinking_level="high" returned
        # truncated garbage ('", so no extra spaces') from the same budget.
        #
        # thinking_level="minimal" reports thoughts_token_count=None and
        # returns correct output, so max_tokens means what the locked constants
        # in Appendix 1 assume. thinking_budget=N does NOT constrain (110-121
        # thoughts regardless) and thinking_budget=0 is rejected outright (400),
        # so this is the only control that works on this model.
        #
        # Raising the level is a deliberate, separately-labelled experiment that
        # also needs the token constants raised (a BD under A0.3) -- not a
        # default, because a truncated answer would score as a model failure
        # when it is a budget artifact.
        level = str(getattr(cfg.models, "gemini_thinking_level", "") or "").strip()
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=cfg.models.temperature,
            max_output_tokens=max_tokens,
            **({"thinking_config": types.ThinkingConfig(thinking_level=level)}
               if level else {}),
        )
        contents = self._to_contents(messages)

        attempts = int(cfg.models.gemini_max_retries) + 1
        delay = float(cfg.models.gemini_retry_base_delay_s)
        last_exc: Exception | None = None

        for attempt in range(attempts):
            try:
                await _pace(self.model, int(getattr(cfg.models, "gemini_rpm", 0) or 0))
                response = await self._client.aio.models.generate_content(
                    model=self.model, contents=contents, config=config,
                )
                break
            except Exception as exc:                       # noqa: BLE001
                last_exc = exc
                if not self._is_rate_limit(exc) or attempt == attempts - 1:
                    raise
                # Free-tier limits are per-minute, so backing off is the
                # correct response rather than failing the query.
                logger.warning(
                    "Gemini rate-limited (attempt %d/%d), backing off %.1fs: %s",
                    attempt + 1, attempts, delay,
                    str(exc)[:_LOG_EXCERPT_CHARS],
                )
                await asyncio.sleep(delay)
                delay *= 2
        else:                                              # pragma: no cover
            raise last_exc if last_exc else RuntimeError("Gemini call failed")

        text = (response.text or "").strip()
        usage = getattr(response, "usage_metadata", None)
        # Thinking tokens are BILLED as output and are invisible in
        # candidates_token_count. Excluding them would understate real cost and
        # make token-budget accounting wrong exactly where it matters.
        thoughts = getattr(usage, "thoughts_token_count", 0) or 0
        visible = getattr(usage, "candidates_token_count", 0) or 0
        if thoughts and not text:
            logger.warning(
                "Gemini returned EMPTY text after %d thinking tokens against a "
                "max_tokens of %d. The budget was consumed by reasoning. Lower "
                "models.gemini_thinking_level or raise the role's token cap.",
                thoughts, max_tokens,
            )
        return LLMResponse(
            text=text,
            # Counts are optional in the response schema; a missing count must
            # not crash a completed query, and 0 undercounts rather than
            # inventing a number.
            tokens_in=getattr(usage, "prompt_token_count", 0) or 0,
            tokens_out=visible + thoughts,
        )
