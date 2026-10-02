"""
tests/test_service_auth.py

WHY THIS EXISTS (mcp/service_auth.py). The MCP takes tenant and principal from its caller, so
until 2026-10-02 any container on app_net could act as anyone. Every /rpc call must now carry a
shared secret. Pinned here:
- the MCP fails closed: no token file, or a short one, is a startup error;
- only "Bearer <the token>" passes; wrong, missing, malformed or empty-token cases all fail;
- the orchestrator's client sends the token on every call, read from the secret file.
The refusal of a real call without the token is checked live after deployment (see the commit).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))
sys.path.insert(0, str(REPO_ROOT / "mcp"))
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))

import service_auth as sa  # noqa: E402

TOKEN = "a" * 64


def test_a_missing_or_short_token_stops_the_mcp(tmp_path):
    with pytest.raises(sa.ServiceTokenMissing):
        sa.load_token(str(tmp_path / "absent"))
    short = tmp_path / "short"
    short.write_text("x" * (sa.MIN_TOKEN_CHARS - 1) + "\n")
    with pytest.raises(sa.ServiceTokenMissing):
        sa.load_token(str(short))
    good = tmp_path / "good"
    good.write_text(TOKEN + "\n")
    assert sa.load_token(str(good)) == TOKEN


def test_only_the_exact_bearer_token_is_authorized():
    assert sa.authorized(f"Bearer {TOKEN}", TOKEN)
    assert not sa.authorized(f"Bearer {TOKEN[:-1]}b", TOKEN)
    assert not sa.authorized(TOKEN, TOKEN)                 # no scheme
    assert not sa.authorized(f"Basic {TOKEN}", TOKEN)
    assert not sa.authorized(None, TOKEN)
    assert not sa.authorized("Bearer ", TOKEN)
    assert not sa.authorized("Bearer ", "")                # an unset server token never matches


def test_the_orchestrator_sends_the_token_on_every_call(tmp_path, monkeypatch):
    pytest.importorskip("httpx")
    import mcp_client
    secret = tmp_path / "mcp_token"
    secret.write_text(TOKEN + "\n")
    monkeypatch.setenv(mcp_client.MCP_TOKEN_FILE_ENV, str(secret))
    mcp_client.service_token.cache_clear()
    try:
        session = mcp_client.MCPSession(tenant_id="t1", base_url="http://mcp:8080")
        assert session._client.headers["Authorization"] == f"Bearer {TOKEN}"
        assert sa.authorized(session._client.headers["Authorization"], TOKEN)
    finally:
        mcp_client.service_token.cache_clear()


def test_the_orchestrator_refuses_to_call_without_a_token(tmp_path, monkeypatch):
    pytest.importorskip("httpx")
    import mcp_client
    monkeypatch.setenv(mcp_client.MCP_TOKEN_FILE_ENV, str(tmp_path / "absent"))
    mcp_client.service_token.cache_clear()
    try:
        with pytest.raises(RuntimeError):
            mcp_client.MCPSession(tenant_id="t1")
    finally:
        mcp_client.service_token.cache_clear()


# ── Per-caller privileges (rule 5) ────────────────────────────────────────────

def _callers(tmp_path, monkeypatch, evaluator_token=None):
    main = tmp_path / "mcp_token"
    main.write_text(TOKEN)
    monkeypatch.setenv(sa.TOKEN_FILE_ENV, str(main))
    ev = tmp_path / "mcp_token_evaluator"
    if evaluator_token is not None:
        ev.write_text(evaluator_token)
    monkeypatch.setenv(sa.EVALUATOR_TOKEN_FILE_ENV, str(ev))
    return sa.load_callers(["data.run_select"])


def test_without_its_secret_there_is_no_evaluator_caller(tmp_path, monkeypatch):
    assert [c.name for c in _callers(tmp_path, monkeypatch)] == ["orchestrator"]


def test_the_evaluator_may_only_run_gold_selects_on_built_in_data(tmp_path, monkeypatch):
    callers = _callers(tmp_path, monkeypatch, "e" * 64)
    evaluator = sa.identify("Bearer " + "e" * 64, callers)
    orchestrator = sa.identify(f"Bearer {TOKEN}", callers)
    assert evaluator.name == "evaluator" and orchestrator.name == "orchestrator"
    assert evaluator.permits("data.run_select", {"sql": "SELECT 1"})
    assert not evaluator.permits("data.run_select", {"sql": "SELECT 1", "dataset": "cn_someone"})
    assert not evaluator.permits("history.get_recent_turns", {})
    assert not evaluator.permits("workspaces.manage", {"action": "list"})
    assert orchestrator.permits("workspaces.manage", {"action": "list", "dataset": "x"})
    assert sa.identify("Bearer " + "x" * 64, callers) is None


def test_the_evaluator_token_must_differ_from_the_orchestrators(tmp_path, monkeypatch):
    with pytest.raises(sa.ServiceTokenMissing):
        _callers(tmp_path, monkeypatch, TOKEN)
