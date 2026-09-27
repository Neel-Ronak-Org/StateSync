"""
tests/test_rate_limits.py
Pins the two per-principal rate-limit budgets (orchestrator/auth.py,
docs/V3_PLAN.md Phase 2b).

WHY THIS EXISTS. One budget used to cover every route, so a dashboard polling
itself every 15 s could spend a principal's 30 requests a minute and lock them
out of asking a question. Now routes that run the model share the tight
budget, and everything else has its own. These tests pin that the two are
independent, that each enforces its own configured ceiling, and that one
principal's use never counts against another's.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))

from auth import RateLimited, RateLimiter, cfg, rate_limiter, read_rate_limiter  # noqa: E402

MODEL_MAX = int(cfg.api.rate_limit_max_requests)
READ_MAX = int(cfg.api.read_rate_limit_max_requests)


def _spend(limiter: RateLimiter, principal: str, times: int) -> None:
    for _ in range(times):
        limiter.check(principal)


def test_the_read_budget_is_larger_than_the_model_budget():
    assert READ_MAX > MODEL_MAX


def test_each_limiter_enforces_its_own_ceiling():
    model, read = RateLimiter("rate_limit_max_requests"), RateLimiter("read_rate_limit_max_requests")
    _spend(model, "p", MODEL_MAX)
    with pytest.raises(RateLimited) as exc:
        model.check("p")
    assert exc.value.retry_after >= 1
    _spend(read, "p", READ_MAX)
    with pytest.raises(RateLimited):
        read.check("p")


def test_spending_the_read_budget_does_not_touch_the_model_budget():
    model, read = RateLimiter("rate_limit_max_requests"), RateLimiter("read_rate_limit_max_requests")
    _spend(read, "p", READ_MAX)
    _spend(model, "p", MODEL_MAX)          # still all available


def test_principals_do_not_share_a_window():
    model = RateLimiter("rate_limit_max_requests")
    _spend(model, "a", MODEL_MAX)
    model.check("b")                       # b is untouched by a's use


def test_the_shipped_instances_use_the_two_budgets():
    assert rate_limiter._max_requests_key == "rate_limit_max_requests"
    assert read_rate_limiter._max_requests_key == "read_rate_limit_max_requests"
