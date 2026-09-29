"""
tests/test_mine_routes.py
The "find definitions in documents" routes (orchestrator/workspaces_api.py,
doc_definitions.py).

WHY THIS EXISTS. Starting a run spends model quota, so it must use the model
principal dependency, only work on the caller's own workspace, and hand the
job the caller's identity for both the workspace store and the library. The
status route must return that job. (One run at a time is the SetupRegistry's
rule, shared with the AI drafts.)
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))

pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import workspaces_api  # noqa: E402

WS = {"workspace_id": "ws-1", "name": "Shop", "target_schema": "olist", "business_context": "We sell sofas.",
      "setup_state": "created"}


@dataclass
class FakePrincipal:
    principal_id: str = "p_test"
    tenant_id: str = "t_test"


@pytest.fixture
def setup(monkeypatch):
    calls: dict[str, list] = {"workspaces": [], "library": [], "runs": []}

    async def call_workspaces(principal, action, **params):
        calls["workspaces"].append((principal.principal_id, action, params))
        if params.get("workspace_id") == "ws-other":
            return {"ok": False, "error": "not_found", "message": "No such workspace."}
        return {"ok": True, "workspace": WS}

    async def call_library(principal, action, **params):
        calls["library"].append((principal.principal_id, action, params))
        return {"ok": True, "chunks": []}

    async def fake_run_mining(job, manage, library, complete, business_context):
        calls["runs"].append((job.kind, job.workspace_id, business_context))
        await library("workspace_chunks", workspace_id=job.workspace_id)
        job.state = "done"
        job.found = 2

    monkeypatch.setattr(workspaces_api, "call_workspaces", call_workspaces)
    monkeypatch.setattr(workspaces_api, "call_library", call_library)
    monkeypatch.setattr(workspaces_api, "run_mining", fake_run_mining)

    async def audit(*args, **kwargs):
        return None

    async def require_principal():
        return FakePrincipal()

    async def answer_question(*args, **kwargs):
        raise AssertionError("not used")

    app = FastAPI()
    workspaces_api.register_workspace_routes(app, require_principal=require_principal, audit=audit,
                                             require_principal_for_model=require_principal,
                                             answer_question=answer_question)
    return calls, TestClient(app)


def test_a_run_starts_as_the_caller_and_reads_their_library(setup):
    calls, client = setup
    response = client.post("/workspaces/ws-1/definitions/mine", json={"principal_id": "p_attacker"})
    assert response.status_code == 202 and response.json()["job"]["kind"] == "mine"
    status = client.get("/workspaces/ws-1/definitions/mine").json()["job"]
    assert status["state"] == "done" and status["found"] == 2 and status["dropped"] == 0
    assert calls["runs"] == [("mine", "ws-1", "We sell sofas.")]
    assert calls["library"] == [("p_test", "workspace_chunks", {"workspace_id": "ws-1"})]
    assert all(principal == "p_test" for principal, _, _ in calls["workspaces"])


def test_someone_elses_workspace_is_404_and_starts_nothing(setup):
    calls, client = setup
    assert client.post("/workspaces/ws-other/definitions/mine").status_code == 404
    assert calls["runs"] == []


def test_no_run_yet_is_a_null_job(setup):
    _, client = setup
    assert client.get("/workspaces/ws-1/definitions/mine").json() == {"job": None}
