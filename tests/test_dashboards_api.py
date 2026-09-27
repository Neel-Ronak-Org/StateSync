"""
tests/test_dashboards_api.py
Unit tests for the /dashboards routes (orchestrator/dashboards_api.py,
docs/V3_PLAN.md Phase 2b).

WHY THIS EXISTS. The HTTP layer must forward the AUTHENTICATED principal (never
one from the body), map the MCP's failure codes to the statuses the web app
relies on, audit both outcomes, and turn an unreachable MCP into a 503 rather
than a 500 with internals.
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
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import dashboards_api  # noqa: E402
from dashboards_api import register_dashboard_routes  # noqa: E402


@dataclass
class FakePrincipal:
    principal_id: str = "p_test"
    tenant_id: str = "t_test"


class FakeMCPSession:
    def __init__(self, tenant_id: str, base_url: str | None = None, session_id: str | None = None) -> None:
        self.tenant_id = tenant_id

    async def __aenter__(self) -> "FakeMCPSession":
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass


class Harness:
    def __init__(self) -> None:
        self.audit_log: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []
        self.result: dict[str, Any] = {"ok": True}
        self.exception: Exception | None = None

    async def audit(self, principal_id, tenant_id, route, outcome, query_id=None, session_id=None,
                    client_ip=None, detail=None) -> None:
        self.audit_log.append({"principal_id": principal_id, "route": route, "outcome": outcome, "detail": detail})

    async def dashboards_manage(self, session, principal_id, action, **params) -> dict[str, Any]:
        self.calls.append({"principal_id": principal_id, "action": action, "params": params})
        if self.exception is not None:
            raise self.exception
        return self.result


DASH = {"dashboard_id": "dash-1", "name": "Overview", "target_schema": "live", "position": 0,
        "tile_count": 3, "created_at": "2026-09-27T10:00:00+00:00", "updated_at": "2026-09-27T10:00:00+00:00"}


@pytest.fixture
def setup(monkeypatch):
    harness = Harness()
    monkeypatch.setattr(dashboards_api, "MCPSession", FakeMCPSession)
    monkeypatch.setattr(dashboards_api, "dashboards_manage", harness.dashboards_manage)
    app = FastAPI()

    async def require_principal():
        return FakePrincipal()

    register_dashboard_routes(app, require_principal=require_principal, audit=harness.audit)
    return harness, TestClient(app)


def test_list_forwards_the_dataset_filter(setup):
    harness, client = setup
    harness.result = {"ok": True, "dashboards": [DASH]}
    response = client.get("/dashboards", params={"schema": "live"})
    assert response.status_code == 200 and response.json() == {"dashboards": [DASH]}
    assert harness.calls[-1] == {"principal_id": "p_test", "action": "list",
                                 "params": {"dashboard_id": None, "target_schema": "live"}}
    assert harness.audit_log[-1]["outcome"] == "allowed"


def test_create_is_201_and_uses_the_authenticated_principal(setup):
    harness, client = setup
    harness.result = {"ok": True, "dashboard": DASH}
    response = client.post("/dashboards", json={"name": "Overview", "target_schema": "live",
                                                "principal_id": "p_attacker"})
    assert response.status_code == 201 and response.json() == {"dashboard": DASH}
    call = harness.calls[-1]
    assert call["principal_id"] == "p_test" and call["action"] == "create"
    assert call["params"]["name"] == "Overview" and call["params"]["target_schema"] == "live"
    assert harness.audit_log[-1]["detail"] == "dashboards:create dashboard=dash-1"


def test_rename_delete_and_layout_forward_the_dashboard_id(setup):
    harness, client = setup
    harness.result = {"ok": True, "dashboard": DASH}
    assert client.patch("/dashboards/dash-1", json={"name": "Sales"}).status_code == 200
    assert harness.calls[-1]["action"] == "rename" and harness.calls[-1]["params"]["dashboard_id"] == "dash-1"

    harness.result = {"ok": True, "dashboard_id": "dash-1"}
    assert client.delete("/dashboards/dash-1").json() == {"deleted": "dash-1"}
    assert harness.calls[-1]["action"] == "delete"

    harness.result = {"ok": True, "dashboard_id": "dash-1", "updated": 2}
    items = [{"tile_id": "a", "x": 0, "y": 0, "w": 6, "h": 8}, {"tile_id": "b", "x": 6, "y": 0, "w": 6, "h": 8}]
    response = client.put("/dashboards/dash-1/layout", json={"items": items})
    assert response.status_code == 200 and response.json() == {"dashboard_id": "dash-1", "updated": 2}
    assert harness.calls[-1]["action"] == "layout" and harness.calls[-1]["params"]["items"] == items


@pytest.mark.parametrize("code, status", [("not_found", 404), ("invalid", 422), ("limit_reached", 409),
                                          ("something_new", 500)])
def test_failures_map_to_statuses_and_are_audited(setup, code, status):
    harness, client = setup
    harness.result = {"ok": False, "error": code, "message": "Nope."}
    response = client.patch("/dashboards/dash-9", json={"name": "x"})
    assert response.status_code == status and response.json()["detail"] == "Nope."
    assert harness.audit_log[-1]["outcome"] == "error"
    assert code in harness.audit_log[-1]["detail"]


def test_an_unreachable_mcp_is_a_503_without_internals(setup):
    harness, client = setup
    harness.exception = RuntimeError("connection refused to mcp:8080")
    response = client.get("/dashboards")
    assert response.status_code == 503
    assert "mcp:8080" not in response.text


def test_a_layout_item_missing_a_field_is_rejected_before_the_mcp(setup):
    harness, client = setup
    response = client.put("/dashboards/dash-1/layout", json={"items": [{"tile_id": "a", "x": 0, "y": 0, "w": 6}]})
    assert response.status_code == 422
    assert harness.calls == []
