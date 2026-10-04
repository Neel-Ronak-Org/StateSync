"""
orchestrator/llm/provider.py
D2 LLM provider seam — the single factory the orchestrator uses for all
LLM calls. Nodes never construct SDK clients directly.

Public API:
    get_agent_llm(role) -> ChatModel

    role is one of: "supervisor", "sql", "synthesizer", "memory_summarizer",
    "normalizer", "decomposer", "judge", "rewriter"

The factory:
  1. Reads flags.llm.provider ("local" | "anthropic" | "azure" | "gemini" | "groq")
  2. Resolves the role → model name from flags.llm.models.<provider>.<role>
  3. Returns the matching adapter (Ollama, Anthropic, Azure, Gemini or Groq),
     wrapped in RecordingModel so each call can be shown in the answer's trace
  4. Is cached per (provider, role) — one adapter instance per role per process

Startup behaviour:
  - provider = "local"     → Anthropic key is NEVER read. Missing key is fine.
  - provider = "anthropic" → key is read and validated at first adapter
                             construction (lazy, not at import time).
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import TYPE_CHECKING

from config_loader import cfg, flags

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
def get_agent_llm(role: str) -> ChatModel:
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

    from llm.recording import RecordingModel

    if provider == "local":
        from llm.adapters.ollama_adapter import OllamaAdapter
        ollama_url: str = str(flags.llm.ollama_url)
        return RecordingModel(OllamaAdapter(model=model, base_url=ollama_url), role, model)

    if provider == "anthropic":
        from llm.adapters.anthropic_adapter import AnthropicAdapter
        return RecordingModel(AnthropicAdapter(model=model), role, model)

    if provider == "azure":
        # `model` here is an Azure DEPLOYMENT name, not a published model id.
        from llm.adapters.azure_adapter import AzureAdapter
        return RecordingModel(AzureAdapter(model=model), role, model)

    if provider == "gemini":
        from llm.adapters.gemini_adapter import GeminiAdapter
        return RecordingModel(GeminiAdapter(model=model), role, model)

    if provider == "groq":
        from llm.adapters.groq_adapter import GroqAdapter
        return RecordingModel(GroqAdapter(model=model), role, model)

    if provider == "openai_compat":
        # Named OpenAI-compatible endpoints with an explicit fallback policy
        # (llm/adapters/openai_compat_adapter.py). The role spec is "endpoint:model" or a list.
        from llm.adapters.openai_compat_adapter import build
        chain = build(getattr(flags.llm.models.openai_compat, role))
        return RecordingModel(chain, role, chain.model)

    raise RuntimeError(
        f"Unknown LLM provider {provider!r}. "
        "Valid values: 'local', 'anthropic', 'azure', 'gemini', 'groq', 'openai_compat'. "
        "Check flags.llm.provider in config/flags.toml."
    )


# What startup learned about whether the provider can answer, for /health
# (docs/V3_PLAN.md 0.4). Filled once by validate_llm_config(); /health reads it
# and never calls the model itself, so health probes cost nothing.
LLM_READINESS: dict = {"ready": True, "detail": None}


def _check_local_models(ollama_url: str) -> None:
    """Record whether the Ollama host actually has every configured model.

    Config resolving is not the same as the model existing: the shipped
    qwen2.5:7b was absent from this machine's Ollama and every question failed
    at the first model call. One cheap /api/tags read at startup turns that into
    a readable log line and a /health field. It warns rather than refusing to
    start, so the UI can still come up and explain what is wrong.
    """
    import httpx

    wanted = sorted({_resolve_model("local", role) for role in _VALID_ROLES})
    try:
        response = httpx.get(f"{ollama_url}/api/tags", timeout=float(cfg.models.ollama_tags_timeout_s))
        response.raise_for_status()
        present = {m.get("name", "") for m in response.json().get("models", [])}
    except Exception as exc:                                   # noqa: BLE001
        LLM_READINESS.update(ready=False, detail=f"Ollama at {ollama_url} is unreachable")
        logger.warning("  Ollama at %s is unreachable (%s): every question will fail.", ollama_url, exc)
        return
    missing = [m for m in wanted if m not in present and f"{m}:latest" not in present]
    if missing:
        LLM_READINESS.update(ready=False, detail="Ollama is missing: " + ", ".join(missing))
        logger.warning(
            "  Ollama at %s does not have %s. Every question will fail until you run "
            "`ollama pull %s` there, or set flags.llm.provider to a hosted provider.",
            ollama_url, ", ".join(missing), " && ollama pull ".join(missing),
        )
    else:
        logger.info("  Ollama models present ✓ (%s)", ", ".join(wanted))


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
    elif provider == "groq":
        # The key and the base URL are the two things a startup check can
        # catch. Limits are per MODEL (8K tokens/min, 1K requests/day on the
        # owner's key, measured 2026-09-26), which is why flags.toml spreads
        # the roles across two models; the adapter waits out 429s.
        from llm.adapters.groq_adapter import GroqAdapter
        GroqAdapter._read_api_key()        # raises GroqConfigError if absent
        GroqAdapter._read_base_url()       # raises GroqConfigError if malformed
        logger.info("  Groq API key: present ✓")
    elif provider == "openai_compat":
        # Every role's first spec must name a configured endpoint whose key file exists.
        from llm.adapters.openai_compat_adapter import OpenAICompatAdapter, endpoint, parse_spec
        for role in sorted(_VALID_ROLES):
            spec = getattr(flags.llm.models.openai_compat, role)
            name, _model = parse_spec(spec if isinstance(spec, str) else list(spec)[0])
            OpenAICompatAdapter._key(endpoint(name))
    elif provider == "local":
        _check_local_models(str(flags.llm.ollama_url))
    else:
        logger.info(
            "  Provider=%s — Anthropic key NOT required. "
            "Remember: local model results must not appear in graded metrics.",
            provider,
        )

    logger.info("LLM config valid.")
