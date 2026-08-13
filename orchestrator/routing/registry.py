"""
orchestrator/routing/registry.py
SpecialistNode routing table registry (A8, Appendix 4).

Every route in routing.table (flags.toml) must resolve to a callable node
at process start. An unresolvable name is a startup failure — not a runtime
error. This is what makes the seam real per A0.5:
  "a registry with a registration function that is actually called."

No `if provider == "x"` branches anywhere in agent logic (A8 rule).
"""

from __future__ import annotations

import logging
from typing import Callable

from config_loader import flags

logger = logging.getLogger(__name__)

# ── Type alias ────────────────────────────────────────────────────────────────
# A SpecialistNode is any async callable that accepts a state dict and returns
# a state update dict. The graph calls these via the routing table.
SpecialistNode = Callable[[dict], dict]

# ── Internal registry ─────────────────────────────────────────────────────────
_registry: dict[str, SpecialistNode] = {}


def register(route_name: str, node: SpecialistNode) -> None:
    """Register a specialist node under a route name."""
    if route_name in _registry:
        raise RuntimeError(
            f"Route '{route_name}' is already registered. "
            "Each route name must be unique in the registry."
        )
    _registry[route_name] = node
    logger.info("Registered specialist node for route '%s'.", route_name)


def resolve(route_name: str) -> SpecialistNode:
    """
    Resolve a route name to its registered node.
    Raises RuntimeError (startup failure) if the name is not registered.
    """
    if route_name not in _registry:
        raise RuntimeError(
            f"Route '{route_name}' is not registered in the routing table. "
            f"Registered routes: {list(_registry.keys())}. "
            "Check routing.table in config/flags.toml and ensure all routes "
            "are registered via routing.registry.register() at startup."
        )
    return _registry[route_name]


def validate_routing_table() -> None:
    """
    Called at process start. Verifies that every route in flags.routing.table
    is registered. Raises RuntimeError on any missing registration (A8 rule:
    unresolvable name is a startup failure).
    """
    configured_routes = flags.routing.table
    if isinstance(configured_routes, str):
        configured_routes = [configured_routes]

    missing = [r for r in configured_routes if r not in _registry]
    if missing:
        raise RuntimeError(
            f"Startup failure: routes {missing} appear in flags.toml "
            f"[routing.table] but are not registered. "
            "Register them before calling validate_routing_table()."
        )
    logger.info(
        "Routing table validated. Registered routes: %s.", list(_registry.keys())
    )
