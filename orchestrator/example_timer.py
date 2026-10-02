"""
orchestrator/example_timer.py
The self-test timer: approved examples are re-run on a schedule, not only when someone opens
the workspace (docs/V3_PLAN.md 5.2, "noticing change"; Phase 3c slice 4).

WHY. Until 2026-10-02 a workspace re-checked its approved examples only when its page was
opened and the last check was older than [example_checks] stale_after_s. A workspace nobody
opened kept serving approved SQL through the fast path after its tables had changed, and
nobody saw it. Every timer_interval_s this sweep applies the page's rule to every workspace:
a workspace with approved examples whose last check is older than stale_after_s is re-checked.

HOW, and why here rather than in the MCP. Row-level security (migration 021) deliberately stops
the MCP from listing workspaces across tenants. The orchestrator already knows every principal
(the principals secret), so the sweep calls the ordinary workspaces.manage `list` and
`check_examples` actions as each principal in turn: the same authenticated, tenant-scoped path
a person's click takes, with no new cross-tenant power. A check runs no model; its SQL goes
through the Layer 1 gate and a read-only connection with max_rows=1 (workspace_tools.py).
One principal's failure is logged and the sweep moves on. At most timer_max_workspaces are
checked per sweep, least recently checked first, so a large account cannot monopolise it.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Iterable

from auth import Principal

logger = logging.getLogger(__name__)

WorkspacesCall = Callable[..., Awaitable[dict[str, Any]]]


def check_age_s(workspace: dict[str, Any], now: datetime) -> float | None:
    """Seconds since the workspace's last example check; None if it has no approved examples.
    A workspace whose examples were never checked is infinitely old."""
    health = workspace.get("health") or {}
    if sum(int(health.get(k) or 0) for k in ("ok", "changed", "failed", "unchecked")) == 0:
        return None
    checked_at = health.get("checked_at")
    if not checked_at:
        return float("inf")
    return (now - datetime.fromisoformat(checked_at)).total_seconds()


def due(workspaces: list[dict[str, Any]], now: datetime, stale_after_s: float) -> list[tuple[float, dict[str, Any]]]:
    """(age, workspace) for every workspace due a check, oldest first."""
    aged = [(check_age_s(w, now), w) for w in workspaces]
    return sorted(((a, w) for a, w in aged if a is not None and a > stale_after_s), key=lambda x: -x[0])


async def sweep(principals: Iterable[Principal], call: WorkspacesCall, stale_after_s: float,
                max_workspaces: int, now: datetime | None = None) -> dict[str, Any]:
    """One pass: find due workspaces across every principal, check the oldest max_workspaces."""
    now = now or datetime.now(timezone.utc)
    candidates: list[tuple[float, Principal, dict[str, Any]]] = []
    errors = 0
    for principal in {(p.tenant_id, p.principal_id): p for p in principals}.values():
        try:
            listed = await call(principal, "list")
        except Exception as exc:  # noqa: BLE001 -- one account's failure must not stop the sweep
            logger.warning("Self-test timer: listing workspaces failed for %s: %s", principal.principal_id, exc)
            errors += 1
            continue
        for age, ws in due(listed.get("workspaces") or [], now, stale_after_s):
            candidates.append((age, principal, ws))
    candidates.sort(key=lambda c: -c[0])
    checked: list[dict[str, Any]] = []
    for _age, principal, ws in candidates[:max_workspaces]:
        try:
            result = await call(principal, "check_examples", workspace_id=ws["workspace_id"])
        except Exception as exc:  # noqa: BLE001 -- recorded and skipped; the next sweep retries
            logger.warning("Self-test timer: check failed for workspace %s: %s", ws["workspace_id"], exc)
            errors += 1
            continue
        if not result.get("ok"):
            errors += 1
            continue
        checked.append({"workspace_id": ws["workspace_id"], "counts": result.get("counts")})
        counts = result.get("counts") or {}
        if counts.get("changed") or counts.get("failed"):
            logger.warning("Self-test timer: workspace %s has %s changed and %s failed approved examples.",
                           ws["workspace_id"], counts.get("changed", 0), counts.get("failed", 0))
    return {"due": len(candidates), "checked": checked, "errors": errors}


async def run_forever(get_principals: Callable[[], Iterable[Principal]], call: WorkspacesCall,
                      interval_s: float, initial_delay_s: float, stale_after_s: float,
                      max_workspaces: int) -> None:
    """The background loop started by the orchestrator's lifespan; cancelled at shutdown."""
    await asyncio.sleep(initial_delay_s)
    while True:
        try:
            outcome = await sweep(get_principals(), call, stale_after_s, max_workspaces)
            logger.info("Self-test timer: %d due, %d checked, %d errors.",
                        outcome["due"], len(outcome["checked"]), outcome["errors"])
        except Exception as exc:  # noqa: BLE001 -- the timer must outlive any one bad sweep
            logger.error("Self-test timer sweep failed: %s", exc, exc_info=True)
        await asyncio.sleep(interval_s)
