"""
tests/test_example_timer.py

WHY THIS EXISTS (orchestrator/example_timer.py). Approved examples were re-checked only when
someone opened the workspace, so an unopened workspace kept serving approved SQL after its tables
changed. The timer applies the same staleness rule on a schedule. Pinned here:
- a workspace is due only if it has approved examples and its last check is older than
  stale_after_s (never checked = infinitely old);
- the sweep checks the oldest first, at most max_workspaces, each as ITS OWN principal;
- one principal's failure is counted and the sweep continues;
- a principal listed twice is swept once.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))

import example_timer as et  # noqa: E402
from auth import Principal  # noqa: E402

NOW = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
DAY = 86400


def _ws(wid, hours_ago=None, examples=1):
    checked = None if hours_ago is None else (NOW - timedelta(hours=hours_ago)).isoformat()
    return {"workspace_id": wid, "health": {"ok": examples, "changed": 0, "failed": 0, "unchecked": 0,
                                            "checked_at": checked}}


def test_due_needs_examples_and_an_old_check():
    rows = [_ws("fresh", 2), _ws("old", 30), _ws("never"), _ws("empty", 99, examples=0)]
    assert [w["workspace_id"] for _, w in et.due(rows, NOW, DAY)] == ["never", "old"]


def test_sweep_checks_oldest_first_as_each_owner_under_the_cap():
    a, b = Principal("p_a", "t_1"), Principal("p_b", "t_2")
    lists = {"p_a": [_ws("a_old", 40), _ws("a_fresh", 1)], "p_b": [_ws("b_older", 90), _ws("b_never")]}
    calls = []

    async def call(principal, action, **params):
        calls.append((principal.principal_id, action, params.get("workspace_id")))
        if action == "list":
            return {"ok": True, "workspaces": lists[principal.principal_id]}
        return {"ok": True, "counts": {"ok": 1, "changed": 0, "failed": 0}}

    out = asyncio.run(et.sweep([a, b, a], call, DAY, max_workspaces=2, now=NOW))
    checks = [c for c in calls if c[1] == "check_examples"]
    assert checks == [("p_b", "check_examples", "b_never"), ("p_b", "check_examples", "b_older")]
    assert out["due"] == 3 and len(out["checked"]) == 2 and out["errors"] == 0
    assert sum(1 for c in calls if c[1] == "list") == 2   # the duplicate principal is swept once


def test_one_failing_principal_does_not_stop_the_sweep():
    a, b = Principal("p_a", "t_1"), Principal("p_b", "t_2")

    async def call(principal, action, **params):
        if principal.principal_id == "p_a":
            raise RuntimeError("mcp unavailable")
        if action == "list":
            return {"ok": True, "workspaces": [_ws("b1", 48)]}
        return {"ok": True, "counts": {"ok": 0, "changed": 1, "failed": 0}}

    out = asyncio.run(et.sweep([a, b], call, DAY, max_workspaces=5, now=NOW))
    assert out["errors"] == 1 and [c["workspace_id"] for c in out["checked"]] == ["b1"]
