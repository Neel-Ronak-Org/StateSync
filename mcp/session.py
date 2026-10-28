"""
mcp/session.py
MCP session management.

tenant_id is bound at session establishment from the X-Tenant-ID header
and injected server-side into every tool invocation. Agents never pass
it as a parameter — it is verified here and threaded through.

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
    # The workspace the caller is acting in, from X-Workspace-ID. The
    # tools that care (workspaces, documents, external connections) validate it
    # against the tenant and principal themselves; it grants nothing on its own.
    workspace_id: str | None = None

_current_session: contextvars.ContextVar[MCPSessionContext | None] = (
    contextvars.ContextVar("mcp_session", default=None)
)


def set_session(tenant_id: str, session_id: str | None = None,
                workspace_id: str | None = None) -> MCPSessionContext:
    """
    Establish a session context for the current async task.
    Called from the FastAPI request middleware before any tool handler runs.
    """
    ctx = MCPSessionContext(
        tenant_id=tenant_id,
        session_id=session_id or str(uuid.uuid4()),
        workspace_id=workspace_id or None,
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


def current_tenant_id() -> str | None:
    """The current tenant, or None outside a request (never raises)."""
    ctx = _current_session.get()
    return ctx.tenant_id if ctx else None


def get_workspace_id() -> str | None:
    """The workspace named by the caller, if any (unvalidated here)."""
    ctx = _current_session.get()
    return ctx.workspace_id if ctx else None


def get_tenant_id() -> str:
    """Convenience accessor for the current tenant_id."""
    return get_session().tenant_id


class TenantNotAllowed(RuntimeError):
    """tenancy.enforced is on and the request's tenant_id is not on the allowlist."""


def enforce_tenancy_policy(tenant_id: str) -> None:
    """Admission control for a multi-tenant deployment. With [tenancy] enforced = false (one owner's
    deployment, the default) every request is admitted. With it on, a tenant outside [tenancy]
    allowed_tenants is refused here, rather than served an empty result that would read like "no data".
    Isolation itself does not depend on this: every StateSync table carries tenant_id, every query filters
    on it, and row-level security in Postgres enforces it again."""
    if not bool(cfg.tenancy.enforced):
        return
    allowed = [str(x) for x in cfg.tenancy.allowed_tenants]
    if tenant_id not in allowed:
        raise TenantNotAllowed(f"tenant_id={tenant_id!r} is not in tenancy.allowed_tenants.")
