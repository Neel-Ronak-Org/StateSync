"""
mcp/session.py
MCP session management.

tenant_id is bound at session establishment from the X-Tenant-ID header
and injected server-side into every tool invocation. Agents never pass
it as a parameter — it is verified here and threaded through (A0.6).

Session context is stored in a contextvars.ContextVar so it is
request-scoped and safe under async concurrency.
"""

from __future__ import annotations

import contextvars
import uuid
from dataclasses import dataclass

from config_loader import cfg

# ── Session context (request-scoped) ─────────────────────────────────────────
@dataclass(frozen=True)
class MCPSessionContext:
    tenant_id: str
    session_id: str

_current_session: contextvars.ContextVar[MCPSessionContext | None] = (
    contextvars.ContextVar("mcp_session", default=None)
)


def set_session(tenant_id: str, session_id: str | None = None) -> MCPSessionContext:
    """
    Establish a session context for the current async task.
    Called from the FastAPI request middleware before any tool handler runs.
    """
    ctx = MCPSessionContext(
        tenant_id=tenant_id,
        session_id=session_id or str(uuid.uuid4()),
    )
    _current_session.set(ctx)
    return ctx


def get_session() -> MCPSessionContext:
    """
    Retrieve the current session context.
    Raises RuntimeError if called outside a request context.
    """
    ctx = _current_session.get()
    if ctx is None:
        raise RuntimeError(
            "No MCP session context found. "
            "Ensure set_session() is called in request middleware."
        )
    return ctx


def get_tenant_id() -> str:
    """Convenience accessor for the current tenant_id."""
    return get_session().tenant_id


class TenancyPolicyNotImplemented(RuntimeError):
    """Raised when tenancy.enforced=true is requested (Appendix 4, F-V4 seam)."""


def enforce_tenancy_policy(tenant_id: str) -> None:
    """
    Appendix 4's "MCP session policy layer" seam for multi-tenancy
    enforcement (A0.5). Called once per request, right after set_session(),
    so it is a real branch point -- not a comment -- even though the policy
    content behind it is Strategy §6 stretch scope, deliberately unbuilt.

    tenancy.enforced=false (the shipped, single-tenant default per D6) is a
    no-op: tenant_id is still threaded through every store write and every
    MCP call regardless (A0.6 -- that part is mandatory, not gated by this
    flag). Flipping the flag to true is what F-V4/the tenancy analogue of
    F-V5 checks: it must visibly ATTEMPT enforcement and fail on missing
    policy content, not silently continue as if nothing were requested --
    the same "registration succeeds, content raises" shape as scorers.py's
    VisionScorer (D11), applied here to D6's own deferred item.
    """
    if not bool(cfg.tenancy.enforced):
        return
    raise TenancyPolicyNotImplemented(
        f"tenancy.enforced=true requested for tenant_id={tenant_id!r}, but the "
        "real multi-tenant policy layer (per-tenant isolation enforcement, "
        "cross-tenant leakage prevention) is Strategy §6 stretch scope and is "
        "not built (D6: 'tested single-tenant'; Appendix 4 names this seam for "
        "Part A/B, threaded from B). Only the dispatch point exists. Set "
        "tenancy.enforced=false to run single-tenant as shipped."
    )
