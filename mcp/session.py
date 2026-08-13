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
