"""
orchestrator/library_api.py
FastAPI routes for the account document library (docs/V3_PLAN.md Phase 3c
slice 5, decision D-v3-17): folders of uploaded documents, the upload itself,
and which folders a workspace searches.

WHY THIS EXISTS. Ownership, quotas and storage are enforced in the MCP's
library.manage (mcp/tools/library_tools.py), which is the authority. This layer
authenticates the caller, forwards their identity (the principal always comes
from `require_principal`, never from a body), and runs the one step that must
not live in the MCP: taking the raw upload, handing it to the isolated parser
service, and starting the background ingest.

THE UPLOAD is the most hostile input StateSync accepts, so it is checked in
layers: the declared type, then the size while the bytes stream in (the read
stops the moment the limit is passed), then the parser (which repeats both
checks and parses in a sandboxed child process). Only text the parser returns
reaches the chunker. Ingestion continues after the response (202): the document
is `processing` until the background job marks it `ready` or `failed`.

Statuses, as for tiles, dashboards and workspaces (tile_render.error_status):
   - 404 (not_found) for a folder, document or workspace that does not exist or is someone else's;
   - 422 (invalid) for a blank or duplicate name, or a file the parser rejects;
   - 409 (limit_reached) when a quota is used up;
   - 413 for a file over the size limit; 415 for a type that is not accepted;
   - 503 when the MCP service or the parser cannot be reached.
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any
from urllib.parse import unquote

import httpx
from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from auth import Principal
from config_loader import cfg
from datasets import is_connection_dataset  # noqa: F401 - re-exported for main.py
from library_ingest import IngestTasks, ingest, safe_filename, title_from_filename
from mcp_client import MCPSession, library_manage
from tile_render import error_status

logger = logging.getLogger(__name__)

HTTP_ACCEPTED = 202   # noqa: literals - HTTP status code
HTTP_TOO_LARGE = 413  # noqa: literals - HTTP status code
HTTP_UNSUPPORTED = 415  # noqa: literals - HTTP status code
HTTP_UNPROCESSABLE = 422  # noqa: literals - HTTP status code
_BYTES_PER_MB = 1024 * 1024  # noqa: literals - unit conversion for the size message (MiB, as the browser)
_UNSUPPORTED_MESSAGE = "Only PDF, plain-text and Markdown files are supported."
_PARSER_DOWN_MESSAGE = "The document parser is not available."


class FolderNameRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str


class ConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    host: str
    port: int = 5432  # noqa: literals - Postgres's standard port, the form's default
    dbname: str
    username: str
    password: str
    sslmode: str = "prefer"


class ConnectionSchemaRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_name: str


class WorkspaceFoldersRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    folder_ids: list[str]


def upload_limit() -> int:
    """The per-file byte limit (a function so tests can lower it; the config is read-only)."""
    return int(cfg.library.max_upload_bytes)


def _mcp_url() -> str:
    return os.environ.get("MCP_URL", "http://mcp:8080")


async def call_library(principal: Principal, action: str, **params: Any) -> dict[str, Any]:
    """One library.manage call as this principal."""
    async with MCPSession(tenant_id=principal.tenant_id, base_url=_mcp_url()) as session:
        return await library_manage(session, principal_id=principal.principal_id, action=action, **params)


def register_library_routes(app: Any, require_principal: Any, audit: Any) -> None:
    """Register the /library routes and the workspace folder links on the FastAPI application."""

    tasks = IngestTasks()

    async def _audit_error(principal: Principal, request: Request, route_path: str, detail: str) -> None:
        client_ip = request.client.host if request.client else None
        await audit(principal.principal_id, principal.tenant_id, route_path, "error",
                    client_ip=client_ip, detail=detail)

    async def _execute(principal: Principal, request: Request, action: str, route_path: str,
                       **params: Any) -> dict[str, Any]:
        client_ip = request.client.host if request.client else None
        try:
            result = await call_library(principal, action, **params)
        except Exception as exc:
            logger.error("Library action %s failed: %s", action, exc, exc_info=True)
            raise HTTPException(status_code=503, detail="Library service unavailable. Try again shortly.") from None

        detail = f"library:{action}"
        # Ids only: never a password or any other request field.
        for label, value in (("workspace", params.get("workspace_id")), ("folder", params.get("folder_id")),
                             ("document", params.get("document_id")), ("connection", params.get("connection_id"))):
            if value:
                detail += f" {label}={value}"
        made = result.get("folder") or result.get("document") or result.get("connection") or {}
        for label, key in (("folder", "folder_id"), ("document", "document_id"), ("connection", "connection_id")):
            if made.get(key) and f" {label}=" not in detail:
                detail += f" {label}={made[key]}"
        if not result.get("ok"):
            await audit(principal.principal_id, principal.tenant_id, route_path, "error",
                        client_ip=client_ip, detail=f"{detail} {result.get('error')}")
            raise HTTPException(status_code=error_status(result.get("error")),
                                detail=result.get("message") or "Request failed.")
        await audit(principal.principal_id, principal.tenant_id, route_path, "allowed",
                    client_ip=client_ip, detail=detail)
        return result

    @app.get("/library/folders", tags=["library"])
    async def list_folders(request: Request, principal: Principal = Depends(require_principal)) -> dict[str, Any]:
        result = await _execute(principal, request, "folders", "/library/folders")
        return {"folders": result.get("folders", [])}

    @app.post("/library/folders", status_code=201, tags=["library"])
    async def create_folder(body: FolderNameRequest, request: Request,
                            principal: Principal = Depends(require_principal)) -> dict[str, Any]:
        result = await _execute(principal, request, "create_folder", "/library/folders", name=body.name)
        return {"folder": result["folder"]}

    @app.patch("/library/folders/{folder_id}", tags=["library"])
    async def rename_folder(folder_id: str, body: FolderNameRequest, request: Request,
                            principal: Principal = Depends(require_principal)) -> dict[str, Any]:
        result = await _execute(principal, request, "rename_folder", f"/library/folders/{folder_id}",
                                folder_id=folder_id, name=body.name)
        return {"folder": result["folder"]}

    @app.delete("/library/folders/{folder_id}", tags=["library"])
    async def delete_folder(folder_id: str, request: Request,
                            principal: Principal = Depends(require_principal)) -> dict[str, Any]:
        result = await _execute(principal, request, "delete_folder", f"/library/folders/{folder_id}",
                                folder_id=folder_id)
        return {"deleted": result.get("deleted", 1)}

    @app.get("/library/documents", tags=["library"])
    async def list_documents(request: Request, folder_id: str | None = None,
                             principal: Principal = Depends(require_principal)) -> dict[str, Any]:
        result = await _execute(principal, request, "documents", "/library/documents", folder_id=folder_id)
        return {"documents": result.get("documents", [])}

    @app.post("/library/folders/{folder_id}/documents", status_code=HTTP_ACCEPTED, tags=["library"])
    async def upload_document(folder_id: str, request: Request,
                              principal: Principal = Depends(require_principal)) -> dict[str, Any]:
        route_path = f"/library/folders/{folder_id}/documents"
        content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if content_type not in list(cfg.library.content_types):
            await _audit_error(principal, request, route_path,
                               f"library:upload folder={folder_id} unsupported_type")
            raise HTTPException(status_code=HTTP_UNSUPPORTED, detail=_UNSUPPORTED_MESSAGE)

        limit = upload_limit()
        received = bytearray()
        async for piece in request.stream():
            received.extend(piece)
            if len(received) > limit:
                await _audit_error(principal, request, route_path, f"library:upload folder={folder_id} too_large")
                raise HTTPException(status_code=HTTP_TOO_LARGE,
                                    detail=f"Files can be at most {limit // _BYTES_PER_MB} MB.")
        data = bytes(received)
        if not data:
            await _audit_error(principal, request, route_path, f"library:upload folder={folder_id} empty")
            raise HTTPException(status_code=HTTP_UNPROCESSABLE, detail="The file is empty.")
        digest = hashlib.sha256(data).hexdigest()
        filename = safe_filename(unquote(request.headers.get("x-filename", "")))

        try:
            async with httpx.AsyncClient(timeout=float(cfg.library.parser_request_timeout_s)) as client:
                parsed = await client.post(os.environ.get("PARSER_URL", "http://parser:8095").rstrip("/") + "/parse",
                                           content=data, headers={"Content-Type": content_type})
        except httpx.HTTPError as exc:
            logger.error("Parser request failed: %s", exc)
            await _audit_error(principal, request, route_path, f"library:upload folder={folder_id} parser_unreachable")
            raise HTTPException(status_code=503, detail=_PARSER_DOWN_MESSAGE) from None
        try:
            payload = parsed.json()
        except ValueError:
            payload = {}
        if parsed.status_code >= 500 or not isinstance(payload, dict):  # noqa: literals - HTTP status class
            await _audit_error(principal, request, route_path, f"library:upload folder={folder_id} parser_error")
            raise HTTPException(status_code=503, detail=_PARSER_DOWN_MESSAGE)
        if parsed.status_code != 200 or not payload.get("ok"):  # noqa: literals - HTTP status code
            await _audit_error(principal, request, route_path,
                               f"library:upload folder={folder_id} {payload.get('error') or 'rejected'}")
            raise HTTPException(status_code=HTTP_UNPROCESSABLE,
                                detail=payload.get("message") or "The file could not be read.")
        pages = payload.get("pages") or []

        result = await _execute(principal, request, "start_document", route_path, folder_id=folder_id,
                                title=title_from_filename(filename), filename=filename, content_type=content_type,
                                pages=len(pages), bytes=len(data), sha256=digest)
        document = result["document"]

        async def job() -> None:
            async with MCPSession(tenant_id=principal.tenant_id, base_url=_mcp_url()) as session:
                async def call(action: str, **params: Any) -> dict[str, Any]:
                    return await library_manage(session, principal_id=principal.principal_id, action=action,
                                                **params)
                await ingest(call, document["document_id"], pages)

        tasks.start(job())
        return {"document": document}

    @app.delete("/library/documents/{document_id}", tags=["library"])
    async def delete_document(document_id: str, request: Request,
                              principal: Principal = Depends(require_principal)) -> dict[str, Any]:
        result = await _execute(principal, request, "delete_document", f"/library/documents/{document_id}",
                                document_id=document_id)
        return {"deleted": result.get("deleted", 1)}

    # ── Connections: the owner's own Postgres (mcp/connections.py) ──────────
    # The password travels once, in the create request, to the MCP, which
    # encrypts it; nothing returns it and it is never logged or audited.

    @app.get("/library/connections", tags=["library"])
    async def list_connections(request: Request, principal: Principal = Depends(require_principal)) -> dict[str, Any]:
        result = await _execute(principal, request, "connections", "/library/connections")
        return {"connections": result.get("connections", []), "available": result.get("available", False)}

    @app.post("/library/connections", status_code=201, tags=["library"])
    async def create_connection(body: ConnectionRequest, request: Request,
                                principal: Principal = Depends(require_principal)) -> dict[str, Any]:
        result = await _execute(principal, request, "create_connection", "/library/connections",
                                name=body.name, host=body.host, port=body.port, dbname=body.dbname,
                                username=body.username, password=body.password, sslmode=body.sslmode)
        return {k: result.get(k) for k in ("connection", "schemas", "warnings")}

    @app.put("/library/connections/{connection_id}/schema", tags=["library"])
    async def set_connection_schema(connection_id: str, body: ConnectionSchemaRequest, request: Request,
                                    principal: Principal = Depends(require_principal)) -> dict[str, Any]:
        result = await _execute(principal, request, "set_connection_schema",
                                f"/library/connections/{connection_id}/schema", connection_id=connection_id,
                                schema_name=body.schema_name)
        return {"connection": result["connection"]}

    @app.post("/library/connections/{connection_id}/test", tags=["library"])
    async def test_connection(connection_id: str, request: Request,
                              principal: Principal = Depends(require_principal)) -> dict[str, Any]:
        result = await _execute(principal, request, "test_connection", f"/library/connections/{connection_id}/test",
                                connection_id=connection_id)
        return {k: result.get(k) for k in ("connection", "schemas", "warnings")}

    @app.delete("/library/connections/{connection_id}", tags=["library"])
    async def delete_connection(connection_id: str, request: Request,
                                principal: Principal = Depends(require_principal)) -> dict[str, Any]:
        result = await _execute(principal, request, "delete_connection", f"/library/connections/{connection_id}",
                                connection_id=connection_id)
        return {"deleted": result.get("deleted", 1)}

    @app.get("/workspaces/{workspace_id}/folders", tags=["library"])
    async def get_workspace_folders(workspace_id: str, request: Request,
                                    principal: Principal = Depends(require_principal)) -> dict[str, Any]:
        result = await _execute(principal, request, "workspace_folders", f"/workspaces/{workspace_id}/folders",
                                workspace_id=workspace_id)
        return {"folder_ids": result.get("folder_ids", [])}

    @app.put("/workspaces/{workspace_id}/folders", tags=["library"])
    async def put_workspace_folders(workspace_id: str, body: WorkspaceFoldersRequest, request: Request,
                                    principal: Principal = Depends(require_principal)) -> dict[str, Any]:
        result = await _execute(principal, request, "set_workspace_folders",
                                f"/workspaces/{workspace_id}/folders", workspace_id=workspace_id,
                                folder_ids=body.folder_ids)
        return {"folder_ids": result.get("folder_ids", [])}
