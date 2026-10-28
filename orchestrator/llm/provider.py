"""
orchestrator/llm/provider.py
The one place the pipeline gets a language model from: get_agent_llm(role).

Each step asks for a ROLE, not a model, and config/flags.toml [llm.models.openai_compat] says which model
plays it, as "endpoint:model" (the endpoint's address and key file are in [llm.endpoints.<name>]):

  supervisor   the LLM router, used only when Jev cannot be reached (front_door.py)   small, fast model
  decomposer   splits a two-source question; drafts a workspace's table notes          small, fast model
  sql          writes and corrects SQL (nodes/sql_agent.py)                             large model
  synthesizer  writes the answer (nodes/synthesizer.py)                                 large model

Every endpoint speaks the OpenAI chat API (llm/adapters/openai_compat_adapter.py), so changing provider or
model is a settings change, not a code change. Each model is wrapped so its calls are recorded for the
answer's trace (llm/recording.py). One adapter per role per process.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import TYPE_CHECKING

from config_loader import flags

if TYPE_CHECKING:
    from llm.adapters.base import ChatModel

logger = logging.getLogger(__name__)

ROLES = ("supervisor", "decomposer", "sql", "synthesizer")

# What startup learned about the model settings, for /health (which never calls a model itself).
LLM_READINESS: dict = {"ready": True, "detail": None}


def _spec(role: str):
    try:
        return getattr(flags.llm.models.openai_compat, role)
    except AttributeError as exc:
        raise RuntimeError(f"config/flags.toml has no [llm.models.openai_compat] {role}.") from exc


@lru_cache(maxsize=len(ROLES))
def get_agent_llm(role: str) -> ChatModel:
    """The model for a role (see the module docstring), wrapped for the call trace."""
    if role not in ROLES:
        raise ValueError(f"Unknown model role {role!r}. Roles: {', '.join(ROLES)}")
    from llm.adapters.openai_compat_adapter import build
    from llm.recording import RecordingModel
    chain = build(_spec(role))
    logger.info("model for role=%s: %s", role, chain.model)
    return RecordingModel(chain, role, chain.model)


def validate_llm_config() -> None:
    """At startup: every role names a configured endpoint whose key file exists. Fails loudly here
    rather than at the first question."""
    from llm.adapters.openai_compat_adapter import OpenAICompatAdapter, endpoint, parse_spec
    for role in ROLES:
        spec = _spec(role)
        name, model = parse_spec(spec if isinstance(spec, str) else list(spec)[0])
        OpenAICompatAdapter._key(endpoint(name))
        logger.info("  role=%-12s endpoint=%s model=%s", role, name, model)
    logger.info("Model settings valid.")
