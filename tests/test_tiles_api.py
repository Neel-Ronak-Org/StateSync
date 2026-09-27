"""
tests/test_tiles_api.py
Unit tests for dashboard tile HTTP routes (orchestrator/tiles_api.py).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))

pytest.importorskip("fastapi")
from fastapi import FastAPI
from fastapi.testclient import TestClient

import tiles_api
from tiles_api import register_tile_routes


@dataclass
class FakePrincipal:
    principal_id: str = "p_test"
    tenant_id: str = "t_test"


class FakeMCPSession:
    def __init__(self, tenant_id: str, base_url: str | None = None, session_id: str | None = None) -> None:
        self.tenant_id = tenant_id
        self.base_url = base_url
        self.session_id = session_id

    async def __aenter__(self) -> FakeMCPSession:
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass


class TileMockHarness:
    def __init__(self) -> None:
        self.audit_log: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []
        self.scripted_result: dict[str, Any] = {"ok": True}
        self.scripted_exception: Exception | None = None

    async def fake_audit(
        self,
        principal_id: str,
        tenant_id: str,
        route: str,
        outcome: str,
        query_id: str | None = None,
        session_id: str | None = None,
        client_ip: str | None = None,
        detail: str | None = None,
    ) -> None:
        self.audit_log.append({
            "principal_id": principal_id,
            "tenant_id": tenant_id,
            "route": route,
            "outcome": outcome,
            "query_id": query_id,
            "session_id": session_id,
            "client_ip": client_ip,
            "detail": detail,
        })

    async def fake_tiles_manage(
        self,
        session: Any,
        principal_id: str,
        action: str,
        **params: Any,
    ) -> dict[str, Any]:
        self.calls.append({
            "session": session,
            "principal_id": principal_id,
            "action": action,
            "params": params,
        })
        if self.scripted_exception is not None:
            raise self.scripted_exception
        return self.scripted_result


@pytest.fixture
def harness_and_client(monkeypatch):
    harness = TileMockHarness()
    monkeypatch.setattr(tiles_api, "MCPSession", FakeMCPSession)
    monkeypatch.setattr(tiles_api, "tiles_manage", harness.fake_tiles_manage)

    app = FastAPI()

    async def fake_require_principal():
        return FakePrincipal(principal_id="p_test", tenant_id="t_test")

    register_tile_routes(app, require_principal=fake_require_principal, audit=harness.fake_audit)
    client = TestClient(app)
    return harness, client


SAMPLE_VIEW = {
    "tile_id": "tile-123",
    "title": "Total orders",
    "state": "ok",
    "version": 1,
    "refresh_interval_s": 3600,
    "created_at": "2026-09-26T12:00:00Z",
    "updated_at": "2026-09-26T12:00:00Z",
    "sql": "SELECT count(*) FROM orders",
    "chart": {
        "mark": "bar",
        "data": {},
    },
    "columns": ["count"],
    "schemas": ["public"],
    "source": {"session_id": "sess-1", "turn_index": 1},
    "result": {
        "rows": [{"count": 42}],
        "row_count": 1,
        "truncated": False,
        "computed_at": "2026-09-26T12:00:00Z",
        "version": 1,
    },
    "stale": False,
    "last_error": None,
    "error_at": None,
}


# ── (a) POST /tiles ──────────────────────────────────────────────────────────

def test_post_tiles_success(harness_and_client):
    harness, client = harness_and_client
    harness.scripted_result = {"ok": True, "tile": dict(SAMPLE_VIEW)}

    res = client.post(
        "/tiles",
        json={
            "session_id": "sess-1",
            "turn_index": 1,
            "title": "Total orders",
            "refresh_interval_s": 3600,
        },
    )
    assert res.status_code == 201
    body = res.json()
    assert "tile" in body
    assert body["tile"]["chart"]["data"]["values"] == [{"count": 42}]

    assert len(harness.calls) == 1
    call = harness.calls[0]
    assert call["principal_id"] == "p_test"
    assert call["action"] == "pin"
    assert call["params"]["session_id"] == "sess-1"
    assert call["params"]["turn_index"] == 1
    assert call["params"]["title"] == "Total orders"
    assert call["params"]["refresh_interval_s"] == 3600


# ── (b) Failure code mappings ────────────────────────────────────────────────

@pytest.mark.parametrize("error_code,expected_status", [
    ("not_found", 404),
    ("not_pinnable", 422),
    ("invalid", 422),
    ("limit_reached", 409),
    ("unexpected_error", 500),
])
def test_failure_code_mappings(harness_and_client, error_code, expected_status):
    harness, client = harness_and_client
    msg = f"Failure details for {error_code}"
    harness.scripted_result = {"ok": False, "error": error_code, "message": msg}

    res = client.get("/tiles/tile-test-fail")
    assert res.status_code == expected_status
    assert res.json()["detail"] == msg


# ── (c) Internal exception -> 503 without leaking exception text ─────────────

def test_internal_exception_returns_503(harness_and_client):
    harness, client = harness_and_client
    secret = "INTERNAL_SECRET_DATABASE_PW_CRASHED"
    harness.scripted_exception = RuntimeError(f"Connection failure: {secret}")

    res = client.get("/tiles/tile-error")
    assert res.status_code == 503
    assert res.json()["detail"] == "Tile service unavailable. Try again shortly."
    assert secret not in res.text


# ── (d) Principal ID always comes from dependency ────────────────────────────

def test_principal_id_comes_from_dependency_ignoring_body(harness_and_client):
    harness, client = harness_and_client
    harness.scripted_result = {"ok": True, "tile": dict(SAMPLE_VIEW)}

    res = client.post(
        "/tiles",
        json={
            "session_id": "sess-1",
            "turn_index": 1,
            "principal_id": "attacker_id",
            "tenant_id": "attacker_tenant",
        },
    )
    assert res.status_code == 201
    assert len(harness.calls) == 1
    assert harness.calls[0]["principal_id"] == "p_test"
    assert harness.calls[0]["session"].tenant_id == "t_test"


# ── (e) GET /tiles renders all; DELETE returns {"deleted": id} ───────────────

def test_list_and_delete_tiles(harness_and_client):
    harness, client = harness_and_client

    # List
    harness.scripted_result = {
        "ok": True,
        "tiles": [
            dict(SAMPLE_VIEW, tile_id="tile-1"),
            dict(SAMPLE_VIEW, tile_id="tile-2", chart=None),
        ],
    }
    res = client.get("/tiles")
    assert res.status_code == 200
    tiles = res.json()["tiles"]
    assert len(tiles) == 2
    assert tiles[0]["chart"]["data"]["values"] == [{"count": 42}]
    assert tiles[1]["chart"] is None

    # Delete
    harness.scripted_result = {"ok": True, "tile_id": "tile-123"}
    res = client.delete("/tiles/tile-123")
    assert res.status_code == 200
    assert res.json() == {"deleted": "tile-123"}


# ── (f) Audit calls on success and failure ────────────────────────────────────

def test_audit_called_on_success_and_failure(harness_and_client):
    harness, client = harness_and_client

    # Success on POST /tiles (detail contains tile_id, session_id is recorded)
    harness.scripted_result = {"ok": True, "tile": dict(SAMPLE_VIEW, tile_id="tile-pin-1")}
    res = client.post("/tiles", json={"session_id": "sess-audit", "turn_index": 1})
    assert res.status_code == 201
    assert len(harness.audit_log) == 1
    audit_entry = harness.audit_log[0]
    assert audit_entry["outcome"] == "allowed"
    assert audit_entry["principal_id"] == "p_test"
    assert audit_entry["tenant_id"] == "t_test"
    assert audit_entry["route"] == "/tiles"
    assert audit_entry["session_id"] == "sess-audit"
    assert "tile=tile-pin-1" in audit_entry["detail"]

    # Success on GET /tiles (detail is tiles:list)
    harness.audit_log.clear()
    harness.scripted_result = {"ok": True, "tiles": []}
    res = client.get("/tiles")
    assert res.status_code == 200
    assert len(harness.audit_log) == 1
    assert harness.audit_log[0]["outcome"] == "allowed"
    assert harness.audit_log[0]["detail"] == "tiles:list"

    # Failure on GET /tiles/{tile_id} (outcome is error, detail contains code and tile_id)
    harness.audit_log.clear()
    harness.scripted_result = {"ok": False, "error": "not_found", "message": "Missing"}
    res = client.get("/tiles/tile-missing")
    assert res.status_code == 404
    assert len(harness.audit_log) == 1
    audit_entry2 = harness.audit_log[0]
    assert audit_entry2["outcome"] == "error"
    assert "not_found" in audit_entry2["detail"]
    assert "tile=tile-missing" in audit_entry2["detail"]


# ── Additional route coverage (get, refresh, replace, update, confirm) ────────

def test_get_tile_route(harness_and_client):
    harness, client = harness_and_client
    harness.scripted_result = {"ok": True, "tile": dict(SAMPLE_VIEW)}
    res = client.get("/tiles/tile-123")
    assert res.status_code == 200
    assert res.json()["tile"]["chart"]["data"]["values"] == [{"count": 42}]
    assert harness.calls[-1]["action"] == "get"
    assert harness.calls[-1]["params"]["tile_id"] == "tile-123"


def test_refresh_tile_route(harness_and_client):
    harness, client = harness_and_client
    harness.scripted_result = {"ok": True, "tile": dict(SAMPLE_VIEW)}
    res = client.post("/tiles/tile-123/refresh")
    assert res.status_code == 200
    assert harness.calls[-1]["action"] == "refresh"
    assert harness.calls[-1]["params"]["tile_id"] == "tile-123"


def test_replace_tile_route(harness_and_client):
    harness, client = harness_and_client
    harness.scripted_result = {"ok": True, "tile": dict(SAMPLE_VIEW)}
    res = client.post("/tiles/tile-123/replace", json={"session_id": "sess-new", "turn_index": 3})
    assert res.status_code == 200
    assert harness.calls[-1]["action"] == "replace"
    assert harness.calls[-1]["params"]["tile_id"] == "tile-123"
    assert harness.calls[-1]["params"]["session_id"] == "sess-new"
    assert harness.calls[-1]["params"]["turn_index"] == 3
    assert harness.audit_log[-1]["session_id"] == "sess-new"


def test_update_tile_route(harness_and_client):
    harness, client = harness_and_client
    harness.scripted_result = {"ok": True, "tile": dict(SAMPLE_VIEW)}
    res = client.patch("/tiles/tile-123", json={"title": "Updated Title", "refresh_interval_s": 7200})
    assert res.status_code == 200
    assert harness.calls[-1]["action"] == "update"
    assert harness.calls[-1]["params"]["tile_id"] == "tile-123"
    assert harness.calls[-1]["params"]["title"] == "Updated Title"
    assert harness.calls[-1]["params"]["refresh_interval_s"] == 7200


def test_confirm_tile_route(harness_and_client):
    harness, client = harness_and_client
    harness.scripted_result = {"ok": True, "tile": dict(SAMPLE_VIEW)}
    res = client.post("/tiles/tile-123/confirm")
    assert res.status_code == 200
    assert harness.calls[-1]["action"] == "confirm"
    assert harness.calls[-1]["params"]["tile_id"] == "tile-123"


# ── Phase 2b: dashboards, viz and layout pass through to the MCP ──────────────

def test_list_forwards_the_dashboard_filter_and_audits_it(harness_and_client):
    harness, client = harness_and_client
    harness.scripted_result = {"ok": True, "tiles": [SAMPLE_VIEW]}
    response = client.get("/tiles", params={"dashboard_id": "dash-1"})
    assert response.status_code == 200
    assert harness.calls[-1]["action"] == "list"
    assert harness.calls[-1]["params"]["dashboard_id"] == "dash-1"
    assert harness.audit_log[-1]["detail"] == "tiles:list dashboard=dash-1"


def test_list_without_a_filter_sends_none(harness_and_client):
    harness, client = harness_and_client
    harness.scripted_result = {"ok": True, "tiles": []}
    assert client.get("/tiles").status_code == 200
    assert harness.calls[-1]["params"].get("dashboard_id") is None
    assert harness.audit_log[-1]["detail"] == "tiles:list"


def test_pin_forwards_dashboard_viz_and_layout(harness_and_client):
    harness, client = harness_and_client
    harness.scripted_result = {"ok": True, "tile": SAMPLE_VIEW}
    response = client.post("/tiles", json={
        "session_id": "sess-1", "turn_index": 2, "dashboard_id": "dash-1", "viz": "donut",
        "layout": {"x": 0, "y": 0, "w": 4, "h": 8},
    })
    assert response.status_code == 201
    params = harness.calls[-1]["params"]
    assert params["dashboard_id"] == "dash-1" and params["viz"] == "donut"
    assert params["layout"] == {"x": 0, "y": 0, "w": 4, "h": 8}


def test_pin_without_layout_sends_none(harness_and_client):
    harness, client = harness_and_client
    harness.scripted_result = {"ok": True, "tile": SAMPLE_VIEW}
    assert client.post("/tiles", json={"session_id": "sess-1", "turn_index": 2}).status_code == 201
    assert harness.calls[-1]["params"]["layout"] is None


def test_update_forwards_viz_and_move(harness_and_client):
    harness, client = harness_and_client
    harness.scripted_result = {"ok": True, "tile": SAMPLE_VIEW}
    response = client.patch("/tiles/tile-123", json={"viz": "auto", "dashboard_id": "dash-2"})
    assert response.status_code == 200
    params = harness.calls[-1]["params"]
    assert params["viz"] == "auto" and params["dashboard_id"] == "dash-2"
