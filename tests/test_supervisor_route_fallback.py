"""
tests/test_supervisor_route_fallback.py
Pins the supervisor's route-parse fallback (docs/V3_PLAN.md 0.6, audit 007
F-007-7).

WHY THIS EXISTS. When the model's reply is not valid JSON, _extract_route
falls back to searching the text for a route name. It used to take the first
match while iterating a frozenset, and a frozenset of strings iterates in an
order that changes with each process's hash seed -- so one unparseable reply
could route to sql in one orchestrator run and docs in the next. The fallback
now picks the route mentioned EARLIEST in the text, which does not depend on
any iteration order. The subprocess test proves it across hash seeds rather
than trusting the reasoning.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))

from nodes.supervisor import _extract_route  # noqa: E402


@pytest.mark.parametrize("reply, route", [
    ('{"route": "docs"}', "docs"),                          # JSON path unchanged
    ("hybrid: it needs both sql and docs", "hybrid"),
    ("docs, not sql", "docs"),
    ("sql -- the docs are irrelevant here", "sql"),
    ('{"route": "sql"', "sql"),                             # truncated JSON
    ("I cannot decide.", "sql"),                            # nothing named: default
])
def test_earliest_named_route_wins(reply, route):
    assert _extract_route(reply) == route


def test_same_reply_routes_the_same_under_every_hash_seed():
    reply = "Needs docs and sql, so hybrid."
    code = (
        "import sys; sys.path.insert(0, %r); "
        "from nodes.supervisor import _extract_route; print(_extract_route(%r))"
        % (str(REPO_ROOT / "orchestrator"), reply)
    )
    seen = set()
    for seed in ("0", "1", "2", "3", "42", "1234"):
        env = {**os.environ, "PYTHONHASHSEED": seed, "CONFIG_DIR": str(REPO_ROOT / "config")}
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, check=True)
        seen.add(out.stdout.strip())
    assert seen == {"docs"}
