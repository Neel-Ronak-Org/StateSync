#!/usr/bin/env python3
"""
scripts/verify_tenancy.py
Verification for the multi-tenancy policy layer (Appendix 4 seam, D6).

The seam used to raise NotImplementedError. It is now implemented for every
plane that can actually carry isolation, so it needs a test that tries to
BREAK the isolation rather than one that confirms a flag is read.

Six checks, all deterministic (no model call):

  T-1  enforced=false is a no-op          -- the shipped single-tenant default
  T-2  unknown tenant is REFUSED          -- admission control, 403 not "empty"
  T-3  known tenant is admitted           -- the control for T-2
  T-4  hybrid_select without a tenant predicate is REJECTED when enforced
  T-5  hybrid_select WITH the predicate is accepted   -- control for T-4
  T-6  enforcement refuses to start unless the shared relational plane is
       acknowledged                       -- no false advertising of isolation

T-4 is the one that matters. semantic_search and get_passage always filtered
on tenant_id; hybrid_select executes model-authored SQL and did not, which is
where a real cross-tenant read was available.

Flags are varied IN MEMORY only; config/statesync.toml is never written.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# Runs either on the host or inside the mcp container. The Layer 1 gate
# checks (T-4/T-5) import vector_tools, which pulls in psycopg_pool -- present
# in the container, not necessarily on the host -- so the container is the
# supported way to run the full suite:
#   docker compose exec -T mcp python3 - < scripts/verify_tenancy.py
REPO_ROOT = Path(__file__).resolve().parent.parent if "__file__" in dir() else Path("/app")
if Path("/app/session.py").exists():          # inside the mcp container
    MCP_DIR, CONFIG_DIR = Path("/app"), Path("/app/config")
else:                                          # on the host
    MCP_DIR, CONFIG_DIR = REPO_ROOT / "mcp", REPO_ROOT / "config"
sys.path.insert(0, str(MCP_DIR))
os.environ.setdefault("CONFIG_DIR", str(CONFIG_DIR))

CONFIG = CONFIG_DIR / "statesync.toml"

passed = 0
failed = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  ✓ PASS: {label}" + (f" — {detail}" if detail else ""))
    else:
        failed += 1
        print(f"  ✗ FAIL: {label}" + (f" — {detail}" if detail else ""))


def set_flags(enforced: bool, ack: bool) -> None:
    """
    Vary the two tenancy toggles IN MEMORY for the duration of this check.

    Deliberately not by rewriting config/statesync.toml: a verification that
    edits tracked config can leave the repo dirty if it dies mid-run, and this
    project has already been bitten once by a bench script leaking flag state
    into the next measurement (see verify_fv3.sh's reset_flags_to_defaults).
    `_Namespace` wraps a plain dict, so the committed file is never touched.
    """
    import config_loader
    data = object.__getattribute__(config_loader.cfg, "_data")
    data["tenancy"]["enforced"] = enforced
    data["tenancy"]["relational_plane_shared_ack"] = ack


def main() -> int:
    try:
        import config_loader  # noqa: F401
        import session
        from session import (enforce_tenancy_policy, TenantNotAllowed,
                             TenancyAcknowledgementMissing)

        default_tenant = "t_capstone_default"
        other_tenant = "t_someone_else"

        # ── T-1 ───────────────────────────────────────────────────────────
        print("\n── T-1 — enforced=false is a no-op (shipped default) ──")
        set_flags(enforced=False, ack=False)
        pol = enforce_tenancy_policy(other_tenant)
        check("unknown tenant is NOT refused when enforcement is off", pol.enforced is False)
        check("policy reports nothing enforced", pol.enforced_planes == ())

        # ── T-6 ───────────────────────────────────────────────────────────
        print("\n── T-6 — enforcement refuses without the shared-plane ack ──")
        set_flags(enforced=True, ack=False)
        try:
            enforce_tenancy_policy(default_tenant)
            check("startup refuses unacknowledged enforcement", False, "no exception raised")
        except TenancyAcknowledgementMissing as exc:
            check("startup refuses unacknowledged enforcement", True)
            check("the refusal explains WHY (relational plane has no tenant_id)",
                  "tenant_id column" in str(exc))

        # ── T-2 / T-3 ─────────────────────────────────────────────────────
        set_flags(enforced=True, ack=True)
        print("\n── T-2 — unknown tenant refused (admission control) ──")
        try:
            enforce_tenancy_policy(other_tenant)
            check("unknown tenant refused", False, "it was admitted")
        except TenantNotAllowed as exc:
            check("unknown tenant refused", True)
            check("refusal is explicit, not an empty result set",
                  "not in tenancy.allowed_tenants" in str(exc))

        print("\n── T-3 — known tenant admitted (control for T-2) ──")
        pol = enforce_tenancy_policy(default_tenant)
        check("known tenant admitted", pol.enforced is True)
        check("document plane reported as enforced", "document_chunks" in pol.enforced_planes)
        check("relational plane reported as UNENFORCEABLE, not silently omitted",
              any("relational" in p for p in pol.unenforceable_planes))

        # ── T-4 / T-5 ─────────────────────────────────────────────────────
        # Exercise the Layer 1 gate directly with a bound session.
        from tools import vector_tools
        session.set_session(tenant_id=default_tenant, session_id="verify-tenancy")

        leaky = ("SELECT chunk_id, content FROM document_chunks "
                 "ORDER BY embedding <=> ss_embed('sla') LIMIT 5")
        scoped = ("SELECT chunk_id, content FROM document_chunks "
                  f"WHERE tenant_id = '{default_tenant}' "
                  "ORDER BY embedding <=> ss_embed('sla') LIMIT 5")

        print("\n── T-4 — cross-tenant hybrid_select REJECTED ──")
        rejection, _ = vector_tools._check_hybrid_gate(leaky)
        check("query over document_chunks without a tenant predicate is rejected",
              rejection is not None, (rejection or "")[:80])

        print("\n── T-5 — correctly scoped hybrid_select accepted (control) ──")
        rejection2, _ = vector_tools._check_hybrid_gate(scoped)
        check("same query WITH the tenant predicate is accepted",
              rejection2 is None, (rejection2 or "")[:80])

        # A query touching only non-tenant-scoped relations must not be
        # burdened with a predicate that has no column to bind to.
        relational = "SELECT seller_id FROM sellers LIMIT 5"
        rejection3, _ = vector_tools._check_hybrid_gate(relational)
        check("query over non-tenant-scoped tables needs no predicate",
              rejection3 is None, (rejection3 or "")[:80])

    finally:
        # Restore the shipped defaults in memory; the file was never written.
        try:
            set_flags(enforced=False, ack=False)
        except Exception:
            pass
        print("\n  (flags varied in memory only — config/statesync.toml untouched)")

    print(f"\n{'─'*20} Summary {'─'*20}")
    print(f"  PASSED: {passed}")
    print(f"  FAILED: {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
