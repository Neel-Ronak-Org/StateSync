"""
orchestrator/llm/adapters/openai_compat_adapter.py
One adapter for every OpenAI-compatible endpoint (Groq, OpenRouter, Google AI Studio's OpenAI
endpoint, a local router such as OmniRoute), configured by NAME in config/flags.toml
[llm.endpoints.<name>], plus a chain that may fall back between endpoints under a policy.

WHY (owner's question, 2026-10-03: free tiers and routers before paying for credits). The
pipeline's numbers are only comparable when every call comes from the model the experiment
names. Routers fall back to other models, providers rename models, and temperature 0 is not
determinism across models. So:
  1. Each role names "endpoint:model" (or a list of them, in order).
  2. [llm.openai_compat] fallback is "off" (measurements), "same_model" (another endpoint
     serving the same model id) or "any". Off by default.
  3. Every response carries the model the SERVER says answered (`served_model`) and the
     endpoint; the call trace records both. With require_served_match = true, an answer from
     a different model is refused (ServedModelMismatch) instead of silently used.
  4. Keys are Docker secret files named per endpoint (key_secret), never environment values
     in config; a missing key fails when that endpoint is first used, naming the file.
  5. Only https base URLs, except a local router on localhost / host.docker.internal.
  6. A router that forwards to several upstream providers (OpenRouter) is pinned per model with
     [llm.endpoints.<name>.extra_body."<model>"] provider = {order = [...], allow_fallbacks =
     false}: the serving stack is then a known one. The provider the router reports is recorded
     per call, and with require_served_match = true an answer from another provider is refused.
Rate-limit handling (retry-after, waiting, reasoning-token headroom) is GroqAdapter's,
inherited unchanged.
"""

from __future__ import annotations

import re

import logging
import os
from dataclasses import dataclass, replace
from urllib.parse import urlparse

from config_loader import flags
from llm.adapters.base import LLMResponse
from llm.adapters.groq_adapter import GroqAdapter, GroqConfigError

logger = logging.getLogger(__name__)

POLICIES = frozenset({"off", "same_model", "any"})
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "host.docker.internal"})
SECRETS_DIR_ENV = "STATESYNC_SECRETS_DIR"
SECRETS_DIR_DEFAULT = "/run/secrets"


class OpenAICompatConfigError(GroqConfigError):
    """An endpoint, key or role spec is unusable."""


class ServedModelMismatch(RuntimeError):
    """The server answered with a different model than the one requested."""


@dataclass(frozen=True)
class Endpoint:
    name: str
    base_url: str
    key_secret: str
    reasoning_effort: dict
    omit_temperature: tuple = ()     # models that reject a temperature (reasoning models)
    max_retries: int | None = None   # fewer retries for a router that retries itself
    extra_body: dict | None = None   # {model: request fields the router reads}, e.g. provider pinning
    served_aliases: dict | None = None   # {requested model: [served labels accepted as that model]}


def check_base_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme == "https" or (parsed.scheme == "http" and parsed.hostname in _LOCAL_HOSTS):
        return url
    raise OpenAICompatConfigError(f"base_url must be https:// (or http:// on a local router), got {url!r}.")


def endpoint(name: str) -> Endpoint:
    table = flags.llm.get("endpoints", {}).get(name)
    if table is None:
        raise OpenAICompatConfigError(f"No [llm.endpoints.{name}] in config/flags.toml.")
    effort = table.get("reasoning_effort", {}) or {}
    retries = table.get("max_retries")
    extra = table.get("extra_body", {}) or {}
    aliases = _plain(table.get("served_aliases", {}) or {})
    return Endpoint(name=name, base_url=check_base_url(str(table.get("base_url", ""))),
                    key_secret=str(table.get("key_secret", "")),
                    reasoning_effort=effort.as_dict() if hasattr(effort, "as_dict") else dict(effort),
                    omit_temperature=tuple(table.get("omit_temperature", []) or []),
                    max_retries=int(retries) if retries is not None else None,
                    extra_body=_plain(extra),
                    served_aliases={str(k): [str(v) for v in vs] for k, vs in aliases.items()})


def _plain(value: object) -> object:
    """A config table (or nested tables) as plain dicts and lists, ready for a JSON request body."""
    if hasattr(value, "as_dict"):
        value = value.as_dict()
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def pinned_provider(extra_body: dict | None) -> str | None:
    """The one provider a request is pinned to (OpenRouter provider.order with fallbacks off)."""
    provider = (extra_body or {}).get("provider") or {}
    order = provider.get("order") or []
    return str(order[0]) if len(order) == 1 and provider.get("allow_fallbacks") is False else None


def _slug(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def same_provider(served: str, pinned: str) -> bool:
    """OpenRouter reports a display name ('DeepInfra'); a pin names a slug, maybe with a variant
    ('deepinfra/bf16')."""
    return _slug(served) == _slug(pinned.split("/", 1)[0])


def parse_spec(spec: str) -> tuple[str, str]:
    """'groq:openai/gpt-oss-120b' -> ('groq', 'openai/gpt-oss-120b')."""
    name, sep, model = str(spec).partition(":")
    if not sep or not name or not model:
        raise OpenAICompatConfigError(f"A role spec is 'endpoint:model', got {spec!r}.")
    return name, model


def same_model(a: str, b: str) -> bool:
    """The same model under two providers' names ('openai/gpt-oss-120b' vs 'gpt-oss-120b'),
    or a dated variant of it."""
    x, y = a.lower().rsplit("/", 1)[-1], b.lower().rsplit("/", 1)[-1]
    return x == y or x.startswith(y + "-") or y.startswith(x + "-")


def served_matches(served: str, requested: str, aliases: dict | None) -> bool:
    """Whether a served label is the requested model: same_model, or a label the endpoint's
    `served_aliases` lists for it. Fails closed: no rule guesses at renamed labels (separators cannot
    tell 'Qwen3 8B' from 'Qwen 3.8 base'), so a provider's new label is accepted only once it is
    written into [llm.endpoints.<name>].served_aliases (reviews by Paper A's session, 2026-10-06)."""
    return same_model(served, requested) or served in (aliases or {}).get(requested, [])


class OpenAICompatAdapter(GroqAdapter):
    """GroqAdapter's behaviour against any named OpenAI-compatible endpoint."""

    def __init__(self, model: str, ep: Endpoint, require_served_match: bool = False) -> None:
        self.model = model
        self.endpoint = ep
        self.require_served_match = require_served_match
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover - environment-dependent
            raise OpenAICompatConfigError("the `openai` package is not installed in this image") from exc
        from config_loader import cfg
        self._client = AsyncOpenAI(api_key=self._key(ep), base_url=ep.base_url,
                                   timeout=float(cfg.models.groq_timeout_s), max_retries=0)
        self._reasoning_effort = str(ep.reasoning_effort.get(model, "") or "").strip()
        self._omit_temperature = model in ep.omit_temperature
        self._extra_body = (ep.extra_body or {}).get(model) or None
        self._pinned_provider = pinned_provider(self._extra_body)
        if ep.max_retries is not None:
            self._max_retries = ep.max_retries
        logger.info("OpenAICompatAdapter ready (endpoint=%s model=%s).", ep.name, model)

    @staticmethod
    def _key(ep: Endpoint) -> str:
        path = os.path.join(os.environ.get(SECRETS_DIR_ENV, SECRETS_DIR_DEFAULT), ep.key_secret)
        try:
            key = open(path).read().strip()
        except OSError:
            key = ""
        if not key or key.startswith("REPLACE"):
            raise OpenAICompatConfigError(f"No API key for endpoint {ep.name!r}: put it in the secret file {path}.")
        return key

    async def complete(self, *, system: str, messages: list[dict], max_tokens: int) -> LLMResponse:
        response = replace(await super().complete(system=system, messages=messages, max_tokens=max_tokens),
                           endpoint=self.endpoint.name)
        served = response.served_model
        if served and not served_matches(served, self.model, self.endpoint.served_aliases):
            logger.warning("endpoint %s answered with %s for %s", self.endpoint.name, served, self.model)
            if self.require_served_match:
                raise ServedModelMismatch(f"{self.endpoint.name} answered with {served!r}, not {self.model!r}.")
        if self._pinned_provider:
            provider = response.served_provider
            if provider is None:
                logger.warning("endpoint %s reported no provider for %s (pinned to %s)",
                               self.endpoint.name, self.model, self._pinned_provider)
            elif not same_provider(provider, self._pinned_provider):
                logger.warning("endpoint %s served %s from %s, not %s", self.endpoint.name, self.model, provider,
                               self._pinned_provider)
                if self.require_served_match:
                    raise ServedModelMismatch(f"{self.endpoint.name} served {self.model!r} from {provider!r}, "
                                              f"not the pinned {self._pinned_provider!r}.")
        return response


class FallbackChain:
    """Try role specs in order. A failure that survived the adapter's own retries (rate limit,
    server error, lost connection, a model mismatch) moves to the next spec only if the policy
    allows it: "off" never, "same_model" only to the same model, "any" always."""

    def __init__(self, links: list[tuple[str, str, object]], policy: str) -> None:
        if policy not in POLICIES:
            raise OpenAICompatConfigError(f"[llm.openai_compat] fallback must be one of {sorted(POLICIES)}.")
        if not links:
            raise OpenAICompatConfigError("A role needs at least one 'endpoint:model'.")
        self.links, self.policy = links, policy
        self.model = links[0][1]

    def allowed(self) -> list[tuple[str, str, object]]:
        first = self.links[0]
        if self.policy == "off":
            return [first]
        if self.policy == "same_model":
            return [link for link in self.links if same_model(link[1], first[1])]
        return list(self.links)

    async def complete(self, *, system: str, messages: list[dict], max_tokens: int) -> LLMResponse:
        from openai import APIConnectionError, InternalServerError, RateLimitError
        movable = (RateLimitError, InternalServerError, APIConnectionError, ServedModelMismatch)
        chain = self.allowed()
        for i, (name, model, adapter) in enumerate(chain):
            try:
                return await adapter.complete(system=system, messages=messages, max_tokens=max_tokens)
            except movable as exc:
                if i == len(chain) - 1:
                    raise
                logger.warning("fallback (%s): %s:%s failed with %s; trying %s:%s", self.policy, name, model,
                               type(exc).__name__, chain[i + 1][0], chain[i + 1][1])
        raise AssertionError("unreachable")  # pragma: no cover


def build(role_spec: object) -> FallbackChain:
    """The chain for one role from flags: a spec string or a list of them."""
    settings = flags.llm.get("openai_compat", {})
    policy = str(settings.get("fallback", "off"))
    strict = bool(settings.get("require_served_match", True))
    specs = [role_spec] if isinstance(role_spec, str) else list(role_spec)
    links = []
    for spec in specs:
        name, model = parse_spec(spec)
        links.append((name, model, OpenAICompatAdapter(model, endpoint(name), require_served_match=strict)))
        if policy == "off":
            break   # never construct (or need keys for) links that cannot be used
    return FallbackChain(links, policy)
