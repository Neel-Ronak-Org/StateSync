"""
tests/test_workspaces_api.py
The /workspaces routes (docs/V3_PLAN.md Phase 3, orchestrator/workspaces_api.py).

WHY THIS EXISTS. The routes decide two things the MCP cannot: WHO is asking
(the authenticated principal, never a body field) and WHOSE WORDS a
description or definition is (always "owner" through the API; a client cannot
store text labelled as an AI draft, or pass an AI draft off as approved). The
status mapping must match tiles and dashboards so the web app handles all
three the same way.
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

import workspaces_api  # noqa: E402
from workspaces_api import register_workspace_routes  # noqa: E402


@dataclass
class FakePrincipal:
    principal_id: str = "p_test"
    tenant_id: str = "t_test"


class FakeMCPSession:
    def __init__(self, tenant_id: str, base_url: str | None = None, **_: Any) -> None:
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

    async def workspaces_manage(self, session, principal_id, action, **params) -> dict[str, Any]:
        self.calls.append({"principal_id": principal_id, "action": action, "params": params})
        if self.exception is not None:
            raise self.exception
        return self.result


WS = {"workspace_id": "ws-1", "name": "Shop", "target_schema": "olist", "business_context": "",
      "context_version": 0, "setup_state": "created", "setup_score": None, "setup_total": None,
      "counts": {"tables": 0, "approved": 0, "definitions": 0, "examples": 0}}


@pytest.fixture
def setup(monkeypatch):
    harness = Harness()
    monkeypatch.setattr(workspaces_api, "MCPSession", FakeMCPSession)
    monkeypatch.setattr(workspaces_api, "workspaces_manage", harness.workspaces_manage)
    app = FastAPI()

    async def require_principal():
        return FakePrincipal()

    register_workspace_routes(app, require_principal=require_principal, audit=harness.audit)
    return harness, TestClient(app)


def test_create_is_201_and_uses_the_authenticated_principal(setup):
    harness, client = setup
    harness.result = {"ok": True, "workspace": WS}
    response = client.post("/workspaces", json={"name": "Shop", "target_schema": "olist",
                                                "business_context": "We sell furniture.",
                                                "principal_id": "p_attacker"})
    assert response.status_code == 201 and response.json() == {"workspace": WS}
    call = harness.calls[-1]
    assert call["principal_id"] == "p_test" and call["action"] == "create"
    assert call["params"] == {"name": "Shop", "target_schema": "olist", "business_context": "We sell furniture."}
    assert harness.audit_log[-1]["detail"] == "workspaces:create workspace=ws-1"


def test_descriptions_and_definitions_are_always_the_owners_words(setup):
    harness, client = setup
    harness.result = {"ok": True, "updated": 1}
    body = {"items": [{"table_name": "orders", "column_name": "status", "description": "Order state",
                       "source": "ai", "approved": True}]}
    assert client.put("/workspaces/ws-1/descriptions", json=body).json() == {"updated": 1}
    assert harness.calls[-1]["params"]["items"] == [
        {"table_name": "orders", "column_name": "status", "description": "Order state", "source": "owner"}]

    harness.result = {"ok": True, "definitions": []}
    body = {"items": [{"term": "GMV", "definition": "Sum of price", "source": "ai", "approved": True}]}
    client.put("/workspaces/ws-1/definitions", json=body)
    assert harness.calls[-1]["params"]["items"] == [{"term": "GMV", "definition": "Sum of price", "source": "owner"}]


def test_table_level_descriptions_default_to_the_empty_column(setup):
    harness, client = setup
    harness.result = {"ok": True, "updated": 1}
    client.put("/workspaces/ws-1/descriptions", json={"items": [{"table_name": "orders", "description": "x"}]})
    assert harness.calls[-1]["params"]["items"][0]["column_name"] == ""


def test_verdicts_are_scoped_to_the_workspace_in_the_url(setup):
    harness, client = setup
    harness.result = {"ok": True, "run": {"run_id": "r1", "workspace_id": "ws-1"}}
    assert client.put("/workspaces/ws-1/setup/r1/results/3", json={"verdict": "right"}).status_code == 200
    assert harness.calls[-1] == {"principal_id": "p_test", "action": "set_verdict",
                                 "params": {"workspace_id": "ws-1", "run_id": "r1", "position": 3, "verdict": "right"}}
    assert client.put("/workspaces/ws-1/setup/r1/results/3", json={"verdict": "pending"}).status_code == 422


@pytest.mark.parametrize("code,status", [("not_found", 404), ("invalid", 422), ("limit_reached", 409)])
def test_mcp_failures_map_to_statuses_and_are_audited(setup, code, status):
    harness, client = setup
    harness.result = {"ok": False, "error": code, "message": "nope"}
    response = client.get("/workspaces/ws-1")
    assert response.status_code == status and response.json()["detail"] == "nope"
    assert harness.audit_log[-1]["outcome"] == "error"
    assert harness.audit_log[-1]["detail"] == f"workspaces:get workspace=ws-1 {code}"


def test_an_unreachable_mcp_is_503(setup):
    harness, client = setup
    harness.exception = ConnectionError("down")
    assert client.get("/workspaces").status_code == 503
