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
