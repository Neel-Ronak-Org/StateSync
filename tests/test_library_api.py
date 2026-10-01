"""
tests/test_library_api.py
The /library routes (orchestrator/library_api.py).

WHY THIS EXISTS. The routes decide what the MCP cannot: WHO is asking (the
authenticated principal, never a body field), whether a file may be read at all
(type, then size while streaming), what the isolated parser said about it, and
that ingestion starts only after the document row exists. The status mapping
must match tiles, dashboards and workspaces so the web app handles all four the
same way. The MCP and the parser are faked.
"""

from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))
os.environ.setdefault("CONFIG_DIR", str(REPO_ROOT / "config"))

pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import library_api  # noqa: E402
from library_api import register_library_routes  # noqa: E402

DOC = {"document_id": "up_1", "folder_id": "fd_1", "title": "Contract", "filename": "contract.pdf",
       "status": "processing", "chunk_count": 0}
FOLDER = {"folder_id": "fd_1", "name": "Contracts", "document_count": 0, "ready_count": 0, "workspace_ids": []}


@dataclass
class FakePrincipal:
    principal_id: str = "p_test"
    tenant_id: str = "t_test"


class FakeMCPSession:
    def __init__(self, tenant_id: str, base_url: str | None = None, **_: Any) -> None:
        self.tenant_id = tenant_id

    async def __aenter__(self) -> FakeMCPSession:
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass


class Harness:
    def __init__(self) -> None:
        self.audit_log: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []
        self.result: dict[str, Any] = {"ok": True}
        self.exception: Exception | None = None
        self.parser_response: httpx.Response | Exception = httpx.Response(
            200, json={"ok": True, "pages": [{"page": 1, "text": "a"}, {"page": 2, "text": "b"}],
                       "blocks": [{"page": 1, "kind": "paragraph", "text": "a"}]})
        self.parser_requests: list[httpx.Request] = []
        self.ingested: list[tuple[str, list, list | None, str | None]] = []
        self.ingest_calls: list[dict[str, Any]] = []

    async def audit(self, principal_id, tenant_id, route, outcome, query_id=None, session_id=None,
                    client_ip=None, detail=None) -> None:
        self.audit_log.append({"principal_id": principal_id, "route": route, "outcome": outcome, "detail": detail})

    async def library_manage(self, session, principal_id, action, **params) -> dict[str, Any]:
        self.calls.append({"principal_id": principal_id, "action": action, "params": params})
        if self.exception is not None:
            raise self.exception
        return self.result

    async def ingest(self, call, document_id, pages, blocks=None, title=None) -> None:
        self.ingested.append((document_id, pages, blocks, title))
        self.ingest_calls.append(await call("finish_document", document_id=document_id, status="ready"))

    def parser_handler(self, request: httpx.Request) -> httpx.Response:
        self.parser_requests.append(request)
        if isinstance(self.parser_response, Exception):
            raise self.parser_response
        return self.parser_response


@pytest.fixture
def setup(monkeypatch):
    harness = Harness()
    monkeypatch.setattr(library_api, "MCPSession", FakeMCPSession)
    monkeypatch.setattr(library_api, "library_manage", harness.library_manage)
    monkeypatch.setattr(library_api, "ingest", harness.ingest)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(library_api.httpx, "AsyncClient",
                        lambda **kw: real_client(transport=httpx.MockTransport(harness.parser_handler), **kw))
    app = FastAPI()

    async def require_principal():
        return FakePrincipal()

    register_library_routes(app, require_principal=require_principal, audit=harness.audit)
    return harness, TestClient(app)


def _upload(client, body: bytes = b"%PDF-1.4 hello", content_type: str = "application/pdf",
            filename: str = "Q3%20contract%20%C3%A9.pdf", folder: str = "fd_1"):
    return client.post(f"/library/folders/{folder}/documents", content=body,
                       headers={"Content-Type": content_type, "X-Filename": filename})


def test_create_folder_is_201_and_the_principal_comes_from_auth(setup):
    harness, client = setup
    harness.result = {"ok": True, "folder": FOLDER}
    response = client.post("/library/folders", json={"name": "Contracts", "principal_id": "p_attacker",
                                                     "owner_principal_id": "p_attacker"})
    assert response.status_code == 201 and response.json() == {"folder": FOLDER}
    assert harness.calls[-1] == {"principal_id": "p_test", "action": "create_folder",
                                 "params": {"name": "Contracts"}}
    assert harness.audit_log[-1]["detail"] == "library:create_folder folder=fd_1"


def test_simple_routes_forward_and_audit(setup):
    harness, client = setup
    harness.result = {"ok": True, "folders": [FOLDER]}
    assert client.get("/library/folders").json() == {"folders": [FOLDER]}
    assert harness.audit_log[-1]["detail"] == "library:folders"

    harness.result = {"ok": True, "folder": FOLDER}
    assert client.patch("/library/folders/fd_1", json={"name": "New"}).json() == {"folder": FOLDER}
    assert harness.calls[-1]["params"] == {"folder_id": "fd_1", "name": "New"}
    assert harness.audit_log[-1]["detail"] == "library:rename_folder folder=fd_1"

    harness.result = {"ok": True, "deleted": 1}
    assert client.delete("/library/folders/fd_1").json() == {"deleted": 1}
    assert harness.audit_log[-1]["detail"] == "library:delete_folder folder=fd_1"
    assert client.delete("/library/documents/up_1").json() == {"deleted": 1}
    assert harness.audit_log[-1]["detail"] == "library:delete_document document=up_1"

    harness.result = {"ok": True, "documents": [DOC]}
    assert client.get("/library/documents", params={"folder_id": "fd_1"}).json() == {"documents": [DOC]}
    assert harness.calls[-1]["params"] == {"folder_id": "fd_1"}
    assert harness.audit_log[-1]["detail"] == "library:documents folder=fd_1"


def test_workspace_folder_links(setup):
    harness, client = setup
    harness.result = {"ok": True, "folder_ids": ["fd_1"]}
    assert client.get("/workspaces/ws-1/folders").json() == {"folder_ids": ["fd_1"]}
    assert harness.audit_log[-1]["detail"] == "library:workspace_folders workspace=ws-1"
    assert client.put("/workspaces/ws-1/folders", json={"folder_ids": ["fd_1"]}).json() == {"folder_ids": ["fd_1"]}
    assert harness.calls[-1] == {"principal_id": "p_test", "action": "set_workspace_folders",
                                 "params": {"workspace_id": "ws-1", "folder_ids": ["fd_1"]}}
    assert harness.audit_log[-1]["detail"] == "library:set_workspace_folders workspace=ws-1"
    assert client.put("/workspaces/ws-1/folders", json={}).status_code == 422


def test_upload_happy_path_is_202_and_starts_ingest(setup):
    harness, client = setup
    harness.result = {"ok": True, "document": DOC}
    body = b"%PDF-1.4 hello"
    response = _upload(client, body)
    assert response.status_code == 202 and response.json() == {"document": DOC}
    call = harness.calls[0]
    assert call["principal_id"] == "p_test" and call["action"] == "start_document"
    assert call["params"] == {"folder_id": "fd_1", "title": "Q3 contract é", "filename": "Q3 contract é.pdf",
                              "content_type": "application/pdf", "pages": 2, "bytes": len(body),
                              "sha256": hashlib.sha256(body).hexdigest()}
    assert harness.parser_requests[0].content == body
    assert harness.parser_requests[0].headers["content-type"] == "application/pdf"
    # the parser's layout blocks and the document's title go to ingest with the pages (rag_v2 Phase C)
    assert harness.ingested == [("up_1", [{"page": 1, "text": "a"}, {"page": 2, "text": "b"}],
                                 [{"page": 1, "kind": "paragraph", "text": "a"}], "Q3 contract é")]
    assert harness.ingest_calls == [{"ok": True, "document": DOC}]  # the job's call reaches library_manage
    assert harness.calls[-1]["action"] == "finish_document" and harness.calls[-1]["principal_id"] == "p_test"
    assert harness.audit_log[0]["detail"] == "library:start_document folder=fd_1 document=up_1"


def test_unsupported_type_is_415_before_anything_else(setup):
    harness, client = setup
    response = _upload(client, b"GIF89a", content_type="image/gif")
    assert response.status_code == 415
    assert response.json()["detail"] == "Only PDF, plain-text and Markdown files are supported."
    assert not harness.parser_requests and not harness.calls
    assert harness.audit_log[-1]["outcome"] == "error"


def test_content_type_parameters_are_ignored(setup):
    harness, client = setup
    harness.result = {"ok": True, "document": DOC}
    assert _upload(client, b"hello", content_type="text/plain; charset=utf-8").status_code == 202


def test_oversized_upload_is_413_and_never_reaches_the_parser(setup, monkeypatch):
    harness, client = setup
    monkeypatch.setattr(library_api, "upload_limit", lambda: 1024 * 1024)
    response = _upload(client, b"x" * (1024 * 1024 + 1))
    assert response.status_code == 413 and response.json()["detail"] == "Files can be at most 1 MB."
    assert not harness.parser_requests and not harness.calls


def test_empty_upload_is_422(setup):
    harness, client = setup
    assert _upload(client, b"").status_code == 422
    assert not harness.parser_requests


def test_parser_rejection_passes_its_message_through(setup):
    harness, client = setup
    harness.parser_response = httpx.Response(422, json={"ok": False, "error": "encrypted",
                                                        "message": "The PDF is password-protected."})
    response = _upload(client)
    assert response.status_code == 422 and response.json()["detail"] == "The PDF is password-protected."
    assert not harness.calls and not harness.ingested
    assert harness.audit_log[-1]["detail"] == "library:upload folder=fd_1 encrypted"


def test_parser_unreachable_is_503(setup):
    harness, client = setup
    harness.parser_response = httpx.ConnectError("refused")
    response = _upload(client)
    assert response.status_code == 503 and response.json()["detail"] == "The document parser is not available."
    harness.parser_response = httpx.ReadTimeout("slow")
    assert _upload(client).status_code == 503
    assert not harness.calls and not harness.ingested


@pytest.mark.parametrize("code,status", [("not_found", 404), ("invalid", 422), ("limit_reached", 409)])
def test_mcp_failures_map_to_statuses_and_are_audited(setup, code, status):
    harness, client = setup
    harness.result = {"ok": False, "error": code, "message": "nope"}
    for response in (client.get("/library/folders"), _upload(client)):
        assert response.status_code == status and response.json()["detail"] == "nope"
    assert harness.audit_log[-1]["outcome"] == "error"
    assert harness.audit_log[-1]["detail"] == f"library:start_document folder=fd_1 {code}"
    assert not harness.ingested


def test_mcp_unreachable_is_503(setup):
    harness, client = setup
    harness.exception = RuntimeError("down")
    assert client.get("/library/folders").status_code == 503
