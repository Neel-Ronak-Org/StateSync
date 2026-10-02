"""
tests/test_build_workspace.py

WHY THIS EXISTS (orchestrator/builder_api.py; 2026-10-02). "Build with AI" planned and answered
with no workspace knowledge, so a drafted dashboard could compute a term differently from the
chat, and approved SQL was never reused. Pinned here, through the real route:
- the planner's message carries the workspace's approved knowledge and the system prompt the
  owner's business context;
- every planned question is asked IN the workspace (so the SQL agent gets the knowledge and the
  approved-example fast path);
- the pin sends no workspace, so a drafted dashboard approves nothing;
- a workspace on another dataset is refused (409), someone else's is a 404.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))

pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import builder_api  # noqa: E402

PLAN = ('{"insights": [' + ",".join(
    f'{{"title": "Insight {i}", "question": "How many orders in segment {i}?", "viz": "kpi"}}' for i in range(5)) + "]}")


@dataclass
class FakePrincipal:
    principal_id: str = "p_test"
    tenant_id: str = "t_test"


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None


def _app(monkeypatch, workspaces):
    seen: dict[str, Any] = {"asked": [], "pins": [], "audit": []}

    async def dashboards_manage(session, principal_id, action, **_):
        return {"ok": True, "dashboards": [{"dashboard_id": "d1", "target_schema": "public"}]}

    async def call_workspaces(principal, action, **params):
        if action == "list":
            return {"ok": True, "workspaces": workspaces}
        assert action == "context" and params["workspace_id"] == "w1"
        return {"ok": True, "business_context": "We sell to restaurants.",
                "descriptions": [], "definitions": [{"term": "active order", "definition": "status not cancelled"}],
                "examples": []}

    async def list_tables(session, schema):
        return {"tables": [{"name": "orders", "row_estimate": 10}]}

    async def describe_table(session, name, schema):
        return {"columns": [{"name": "status", "type": "text"}]}

    class LLM:
        async def complete(self, system, messages, max_tokens):
            seen["system"], seen["user"] = system, messages[0]["content"]
            return SimpleNamespace(text=PLAN)

    async def tiles_manage(session, principal_id, action, **params):
        seen["pins"].append(params)
        return {"ok": True, "tile": {"tile_id": f"t{len(seen['pins'])}"}}

    async def answer_question(principal, question, session_id, schema, client_ip, route_path="/query",
                              workspace_id=None, **_):
        seen["asked"].append(workspace_id)
        return SimpleNamespace(model_dump=lambda: {"state_trace": {"turn_index": 1, "executed_sql": "SELECT 1",
                                                                   "sql_rows_count": 1, "route": "sql"},
                                                   "answer": "1"})

    async def audit(principal_id, tenant_id, route, outcome, client_ip=None, detail=None, **_):
        seen["audit"].append(detail)

    monkeypatch.setattr(builder_api, "_mcp", lambda principal: FakeSession())
    monkeypatch.setattr(builder_api, "dashboards_manage", dashboards_manage)
    monkeypatch.setattr(builder_api, "call_workspaces", call_workspaces)
    monkeypatch.setattr(builder_api, "schema_list_tables", list_tables)
    monkeypatch.setattr(builder_api, "schema_describe_table", describe_table)
    monkeypatch.setattr(builder_api, "get_agent_llm", lambda role: LLM())
    monkeypatch.setattr(builder_api, "tiles_manage", tiles_manage)
    app = FastAPI()
    builder_api.register_build_routes(app, require_principal=lambda: FakePrincipal(),
                                      require_principal_for_model=lambda: FakePrincipal(),
                                      audit=audit, answer_question=answer_question)
    return app, seen


def _wait_done(client):
    for _ in range(200):
        job = client.get("/dashboards/d1/build").json()["job"]
        if job and job["state"] in ("done", "failed"):
            return job
        time.sleep(0.01)
    raise AssertionError("the build did not finish")


def test_a_build_plans_and_asks_with_the_workspace_and_approves_nothing(monkeypatch):
    app, seen = _app(monkeypatch, [{"workspace_id": "w1", "target_schema": "public"}])
    with TestClient(app) as client:
        assert client.post("/dashboards/d1/build", json={"workspace_id": "w1"}).status_code == 202
        job = _wait_done(client)
    assert job["state"] == "done", job
    assert "active order: status not cancelled" in seen["user"]
    assert "We sell to restaurants." in seen["system"]
    assert seen["asked"] and set(seen["asked"]) == {"w1"}
    assert seen["pins"] and all("workspace_id" not in p for p in seen["pins"])
    assert any("workspace=w1" in (d or "") for d in seen["audit"])


def test_the_only_workspace_on_the_dataset_is_used_without_a_request(monkeypatch):
    app, seen = _app(monkeypatch, [{"workspace_id": "w1", "target_schema": "public"},
                                   {"workspace_id": "w9", "target_schema": "olist"}])
    with TestClient(app) as client:
        assert client.post("/dashboards/d1/build", json={}).status_code == 202
        _wait_done(client)
    assert set(seen["asked"]) == {"w1"}


def test_a_wrong_dataset_or_foreign_workspace_is_refused(monkeypatch):
    app, _ = _app(monkeypatch, [{"workspace_id": "w9", "target_schema": "olist"}])
    with TestClient(app) as client:
        assert client.post("/dashboards/d1/build", json={"workspace_id": "w9"}).status_code == 409
        assert client.post("/dashboards/d1/build", json={"workspace_id": "nope"}).status_code == 404


def test_a_failed_workspace_lookup_does_not_block_a_build_that_asked_for_none(monkeypatch):
    app, seen = _app(monkeypatch, [])

    async def broken(principal, action, **params):
        raise RuntimeError("mcp down")
    monkeypatch.setattr(builder_api, "call_workspaces", broken)
    with TestClient(app) as client:
        assert client.post("/dashboards/d1/build", json={}).status_code == 202
        _wait_done(client)
        assert client.post("/dashboards/d1/build", json={"workspace_id": "w1"}).status_code == 503
    assert set(seen["asked"]) == {None}
