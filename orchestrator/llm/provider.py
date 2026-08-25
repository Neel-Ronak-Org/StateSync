"""
orchestrator/llm/provider.py
D2 LLM provider seam — the single factory the orchestrator uses for all
LLM calls. Nodes never construct SDK clients directly.

Public API:
    get_agent_llm(role) -> ChatModel

    role is one of: "supervisor", "sql", "synthesizer", "memory_summarizer",
    "normalizer", "decomposer", "judge", "rewriter"

The factory:
  1. Reads flags.llm.provider ("local" | "anthropic")
  2. Resolves the role → model name from flags.llm.models.<provider>.<role>
  3. Returns the appropriate adapter (OllamaAdapter or AnthropicAdapter)
  4. Is cached per (provider, role) — one adapter instance per role per process

Startup behaviour:
  - provider = "local"     → Anthropic key is NEVER read. Missing key is fine.
  - provider = "anthropic" → key is read and validated at first adapter
                             construction (lazy, not at import time).
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import TYPE_CHECKING

from config_loader import flags

if TYPE_CHECKING:
    from llm.adapters.base import ChatModel

logger = logging.getLogger(__name__)

# Valid roles — the set of names nodes may request
_VALID_ROLES = frozenset({
    "supervisor", "sql", "synthesizer", "memory_summarizer", "normalizer",
    "decomposer", "judge", "rewriter",
})


def _resolve_model(provider: str, role: str) -> str:
    """
    Read flags.llm.models.<provider>.<role>.
    Raises RuntimeError on missing config so startup fails loudly.
    """
    try:
        provider_models = getattr(flags.llm.models, provider)
        model = getattr(provider_models, role)
        return str(model)
    except AttributeError as exc:
        raise RuntimeError(
            f"LLM config missing: flags.llm.models.{provider}.{role} not found. "
            "Check config/flags.toml."
        ) from exc


@lru_cache(maxsize=8)  # noqa: literals — stdlib cache size, not a domain constant
def get_agent_llm(role: str) -> "ChatModel":
    """
    Return the configured ChatModel for the given agent role.
    Result is cached — one adapter instance per role per process lifetime.

    Args:
        role: one of _VALID_ROLES (see module docstring)

    Returns:
        A ChatModel adapter whose .complete() method can be awaited.

    Raises:
        ValueError:   Unknown role.
        RuntimeError: Provider config missing, or Anthropic key absent when
                      provider = "anthropic".
    """
    if role not in _VALID_ROLES:
        raise ValueError(
            f"Unknown LLM role {role!r}. Valid roles: {sorted(_VALID_ROLES)}"
        )

    provider: str = str(flags.llm.provider)
    model: str = _resolve_model(provider, role)

    logger.info(
        "LLM provider resolved: role=%s provider=%s model=%s",
        role, provider, model,
    )

    if provider == "local":
        from llm.adapters.ollama_adapter import OllamaAdapter
        ollama_url: str = str(flags.llm.ollama_url)
        return OllamaAdapter(model=model, base_url=ollama_url)

    if provider == "anthropic":
        from llm.adapters.anthropic_adapter import AnthropicAdapter
        return AnthropicAdapter(model=model)

    if provider == "azure":
        # `model` here is an Azure DEPLOYMENT name, not a published model id.
        from llm.adapters.azure_adapter import AzureAdapter
        return AzureAdapter(model=model)

    if provider == "gemini":
        from llm.adapters.gemini_adapter import GeminiAdapter
        return GeminiAdapter(model=model)

    raise RuntimeError(
        f"Unknown LLM provider {provider!r}. "
        "Valid values: 'local', 'anthropic', 'azure', 'gemini'. "
        "Check flags.llm.provider in config/flags.toml."
    )


def validate_llm_config() -> None:
    """
    Called at process start to fail loudly on bad config rather than at
    the first query. Checks that every role resolves cleanly for the
    configured provider. For the 'anthropic' provider also verifies the
    API key is present.
    """
    provider: str = str(flags.llm.provider)
    logger.info("Validating LLM config for provider=%s ...", provider)

    for role in sorted(_VALID_ROLES):
        model = _resolve_model(provider, role)
        logger.info("  role=%-12s model=%s", role, model)

    if provider == "anthropic":
        # Eagerly validate key existence — better to fail at startup than mid-query
        from llm.adapters.anthropic_adapter import AnthropicAdapter
        AnthropicAdapter._read_api_key()   # raises RuntimeError if absent
        logger.info("  Anthropic API key: present ✓")
    elif provider == "azure":
        # Azure has THREE ways to be half-configured (key, endpoint, api
        # version) and a fourth that no startup check can catch: a deployment
        # name that does not exist in the resource. The first three are
        # validated here so they surface at boot rather than at query one.
        # The fourth is named explicitly in the log because it is the common
        # one -- flags.llm.models.azure.<role> must match the DEPLOYMENT name
        # you typed in the Foundry portal, not the base model id.
        from llm.adapters.azure_adapter import AzureAdapter
        AzureAdapter._read_api_key()       # raises AzureConfigError if absent
        endpoint = AzureAdapter._read_endpoint()
        version = AzureAdapter._read_api_version()
        logger.info("  Azure key: present ✓  endpoint=%s api_version=%s",
                    endpoint, version)
        logger.warning(
            "  Azure model names above are DEPLOYMENT names. If a call returns "
            "404 DeploymentNotFound, the deployment name in "
            "flags.llm.models.azure.* does not match the Foundry portal."
        )
    elif provider == "gemini":
        # One failure mode only (the key), unlike Azure's four -- Gemini
        # addresses models by published name, so there is no deployment-name
        # trap. A wrong model name surfaces as a 404 naming the model.
        from llm.adapters.gemini_adapter import GeminiAdapter
        GeminiAdapter._read_api_key()      # raises GeminiConfigError if absent
        logger.info("  Gemini API key: present ✓")
        logger.info(
            "  NOTE: the free tier is rate-limited PER MINUTE. A full fixture "
            "run issues ~5.2 calls/query; the adapter retries 429 with backoff "
            "(models.gemini_max_retries)."
        )
    else:
        logger.info(
            "  Provider=%s — Anthropic key NOT required. "
            "Remember: local model results must not appear in graded metrics.",
            provider,
        )

    logger.info("LLM config valid.")
