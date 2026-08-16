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
    """
    Kept for compatibility with callers that catch it (mcp/main.py). No longer
    raised for the planes that ARE enforced -- see enforce_tenancy_policy.
    """


class TenantNotAllowed(RuntimeError):
    """tenancy.enforced=true and the request's tenant_id is not on the allowlist."""


class TenancyAcknowledgementMissing(RuntimeError):
    """
    tenancy.enforced=true, but the operator has not acknowledged that the
    relational plane is shared. Refusing here is the point: enabling
    "enforcement" while the main data plane has no tenant discriminator would
    advertise an isolation that does not exist.
    """


@dataclass(frozen=True)
class TenancyPolicy:
    """
    What isolation is actually in force for this request. Returned rather than
    implied, so callers (and verification) can inspect it instead of assuming.
    """

    enforced: bool
    tenant_id: str
    enforced_planes: tuple[str, ...]
    unenforceable_planes: tuple[str, ...]

    def requires_tenant_predicate(self, relation: str) -> bool:
        """True when a query touching `relation` must carry a tenant_id filter."""
        return self.enforced and relation.lower() in _TENANT_SCOPED_RELATIONS


# Relations that carry a tenant_id column and can therefore be row-isolated.
# Verified against the live schema, not assumed: information_schema shows
# tenant_id on exactly these two.
_TENANT_SCOPED_RELATIONS = frozenset({"documents", "document_chunks"})


def enforce_tenancy_policy(tenant_id: str) -> TenancyPolicy:
    """
    Appendix 4's "MCP session policy layer" seam for multi-tenancy (A0.5),
    now IMPLEMENTED for every plane that can carry isolation.

    tenancy.enforced=false (the shipped default, D6) stays a no-op: tenant_id
    is threaded through every store write and MCP call regardless (A0.6 --
    mandatory, not gated by this flag).

    tenancy.enforced=true turns on three things:

      1. ADMISSION CONTROL. A tenant_id outside tenancy.allowed_tenants is
         refused outright. Without this a typo in X-Tenant-ID silently
         produces an empty result set that reads like "no data" rather than
         "wrong tenant".

      2. MANDATORY TENANT PREDICATES on the document plane. `documents` and
         `document_chunks` carry tenant_id, and semantic_search/get_passage
         already filter on it unconditionally. The hole was vector.hybrid_select,
         which executes model-authored SQL: a query reading document_chunks
         without a tenant predicate would cross tenants. Layer 1 now requires
         that predicate (see tools/vector_tools.py).

      3. AN HONEST REFUSAL about the relational plane. orders/sellers/
         fulfillments/... have NO tenant column, so they cannot be row-isolated
         at all. Enforcement therefore refuses to start unless
         tenancy.relational_plane_shared_ack is set, which records that the
         operator knows the business tables are shared. Adding tenant_id there
         would change the A5 fingerprint and invalidate every stored trajectory
         under D4 -- a blast radius that belongs to a migration decision, not
         to a flag flip.

    Returns the policy in force so callers can inspect it rather than infer it.
    """
    if not bool(cfg.tenancy.enforced):
        return TenancyPolicy(
            enforced=False,
            tenant_id=tenant_id,
            enforced_planes=(),
            unenforceable_planes=(),
        )

    allowed = [str(x) for x in cfg.tenancy.allowed_tenants]
    if tenant_id not in allowed:
        raise TenantNotAllowed(
            f"tenant_id={tenant_id!r} is not in tenancy.allowed_tenants. "
            f"Refused at the MCP boundary rather than served an empty result "
            f"set, which would be indistinguishable from 'this tenant has no "
            f"data'. Known tenants: {allowed}."
        )

    if not bool(cfg.tenancy.relational_plane_shared_ack):
        raise TenancyAcknowledgementMissing(
            "tenancy.enforced=true, but the relational business tables carry no "
            "tenant_id column and cannot be row-isolated. Enabling enforcement "
            "without saying so would advertise an isolation that does not "
            "exist. Set tenancy.relational_plane_shared_ack=true to record that "
            "the shared relational plane is understood and accepted, or migrate "
            "tenant_id onto those tables first (note: that changes the A5 "
            "fingerprint and invalidates stored trajectories under D4)."
        )

    return TenancyPolicy(
        enforced=True,
        tenant_id=tenant_id,
        enforced_planes=("documents", "document_chunks", "agentsm_trajectories"),
        unenforceable_planes=("relational business tables (no tenant_id column)",),
    )
