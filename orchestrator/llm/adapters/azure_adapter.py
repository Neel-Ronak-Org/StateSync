"""
orchestrator/llm/adapters/azure_adapter.py
Azure OpenAI / Azure AI Foundry adapter implementing the ChatModel protocol.

Third implementation behind the D2 provider seam (BD1), alongside
OllamaAdapter and AnthropicAdapter. Nodes are untouched: switching to it is
`flags.llm.provider = "azure"` plus a key, exactly as the seam promises.

WHAT "MODEL" MEANS HERE, because it differs from the other two adapters.
Azure OpenAI does not address models by published name — it addresses
DEPLOYMENTS you create in the Foundry portal, and the deployment name is
whatever you typed. So `flags.llm.models.azure.<role>` holds a DEPLOYMENT
name, not a model id. Two deployments of the same base model with different
names are two different values here. This is the single most common way an
Azure setup fails at the first call, so it fails at STARTUP instead: see
validate_llm_config().

Credentials, following AnthropicAdapter's precedent exactly:
    AZURE_OPENAI_API_KEY_FILE  (Docker secret) → default /run/secrets/azure_openai_api_key
    AZURE_OPENAI_API_KEY       (env var, fallback)
The endpoint and API version are CONFIG, not secrets, and live in
flags.llm.azure.* — an endpoint is not sensitive and putting it in a secret
file makes it invisible to the config discipline checks.

Uses openai.AsyncAzureOpenAI so completions are non-blocking, matching the
other two adapters. The import is lazy (inside __init__) so that a stack
running provider="local" never needs the openai package installed — the same
property that lets local mode run with no Anthropic key.
"""

from __future__ import annotations

import logging
import os

from config_loader import cfg, flags
from llm.adapters.base import LLMResponse

logger = logging.getLogger(__name__)

_PLACEHOLDER_PREFIX = "REPLACE"


class AzureConfigError(RuntimeError):
    """Azure is selected but its configuration or credentials are unusable.

    A distinct type so validate_llm_config() can report 'the provider is
    wired, your setup is incomplete' separately from 'the provider does not
    exist' -- the same distinction VisionScorer draws between a missing
    implementation and a missing registration.
    """


class AzureAdapter:
    """Async Azure OpenAI adapter. `model` is a DEPLOYMENT name."""

    def __init__(self, model: str) -> None:
        self.model = model
        api_key = self._read_api_key()
        endpoint = self._read_endpoint()
        api_version = self._read_api_version()

        try:
            from openai import AsyncAzureOpenAI
        except ImportError as exc:      # pragma: no cover - environment-dependent
            raise AzureConfigError(
                "flags.llm.provider = 'azure' but the `openai` package is not "
                "installed in this image. It is declared in "
                "orchestrator/pyproject.toml; rebuild the orchestrator image "
                "(`docker compose build orchestrator`) and restart. Until then, "
                "set flags.llm.provider = 'local'."
            ) from exc

        self._client = AsyncAzureOpenAI(
            api_key=api_key,
            azure_endpoint=endpoint,
            api_version=api_version,
            timeout=float(cfg.models.azure_timeout_s),
            max_retries=int(cfg.models.azure_max_retries),
        )
        logger.info(
            "AzureAdapter ready (deployment=%s endpoint=%s api_version=%s).",
            model, endpoint, api_version,
        )

    # ── Config/credential helpers (static so validate_llm_config can call them) ──
    @staticmethod
    def _read_api_key() -> str:
        secret_file = os.environ.get(
            "AZURE_OPENAI_API_KEY_FILE",
            "/run/secrets/azure_openai_api_key",
        )
        try:
            key = open(secret_file).read().strip()
            if key and not key.startswith(_PLACEHOLDER_PREFIX):
                return key
        except OSError:
            pass

        key = os.environ.get("AZURE_OPENAI_API_KEY", "")
        if not key:
            raise AzureConfigError(
                "Azure OpenAI API key not found. Set AZURE_OPENAI_API_KEY_FILE "
                "(Docker secret, default /run/secrets/azure_openai_api_key) or "
                "the AZURE_OPENAI_API_KEY env var. To run without one, set "
                "flags.llm.provider = 'local' in config/flags.toml."
            )
        return key

    @staticmethod
    def _read_endpoint() -> str:
        endpoint = str(getattr(flags.llm.azure, "endpoint", "") or "").strip()
        if not endpoint or _PLACEHOLDER_PREFIX in endpoint:
            raise AzureConfigError(
                "flags.llm.azure.endpoint is unset or still the placeholder. "
                "Set it to your resource endpoint, e.g. "
                "'https://<your-resource>.openai.azure.com/' -- copy it from the "
                "Azure AI Foundry portal, Deployments → your deployment → Endpoint."
            )
        if not endpoint.startswith("https://"):
            raise AzureConfigError(
                f"flags.llm.azure.endpoint must be an https:// URL, got {endpoint!r}."
            )
        return endpoint

    @staticmethod
    def _read_api_version() -> str:
        version = str(getattr(flags.llm.azure, "api_version", "") or "").strip()
        if not version:
            raise AzureConfigError(
                "flags.llm.azure.api_version is unset. Azure OpenAI requires an "
                "explicit API version string; the portal shows the current one "
                "next to each deployment."
            )
        return version

    # ── ChatModel.complete ────────────────────────────────────────────────────
    async def complete(
        self,
        *,
        system: str,
        messages: list[dict],
        max_tokens: int,
    ) -> LLMResponse:
        """
        Azure OpenAI takes the system prompt as the first message rather than a
        separate parameter (the Anthropic shape), so it is prepended here. The
        ChatModel contract is unchanged for callers.
        """
        completion = await self._client.chat.completions.create(
            model=self.model,                      # deployment name
            max_completion_tokens=max_tokens,
            temperature=cfg.models.temperature,
            messages=[{"role": "system", "content": system}] + messages,
        )
        text = (completion.choices[0].message.content or "").strip()
        usage = completion.usage
        return LLMResponse(
            text=text,
            # usage is optional in the response schema; a missing count must not
            # crash a completed query, and 0 undercounts rather than inventing.
            tokens_in=getattr(usage, "prompt_tokens", 0) or 0,
            tokens_out=getattr(usage, "completion_tokens", 0) or 0,
        )
