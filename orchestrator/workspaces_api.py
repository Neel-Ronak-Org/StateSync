"""
orchestrator/workspaces_api.py
FastAPI routes for workspaces and their knowledge store (docs/V3_PLAN.md Phase 3).

WHY THIS EXISTS: a workspace is a named data source plus what the owner has
taught StateSync about it (business context, column descriptions, definitions,
approved examples). This layer only authenticates the caller and forwards
their identity; ownership, bounds, the dataset allowlist and the SQL gate on
examples are enforced in the MCP's workspaces.manage
(mcp/tools/workspace_tools.py), which is the authority.

Everything written through these routes is the OWNER's word: descriptions and
definitions are stored with source "owner". AI drafts are written by the
orchestrator's own setup flow (slice 2), never by a client claiming to be AI.

Statuses, as for tiles and dashboards (tile_render.error_status):
   - 404 (not_found) for a workspace, definition or run that does not exist or is someone else's;
   - 422 (invalid) for a blank name, an unknown dataset, or malformed items;
   - 409 (limit_reached) when a quota is used up;
   - 503 when the MCP service cannot be reached.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Literal

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from auth import Principal
from mcp_client import MCPSession, workspaces_manage
from tile_render import error_status

logger = logging.getLogger(__name__)


class CreateWorkspaceRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    target_schema: str
    business_context: str = ""


class UpdateWorkspaceRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    business_context: str | None = None


class DescriptionItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    table_name: str
    column_name: str = ""
    description: str


class SaveDescriptionsRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    items: list[DescriptionItem]


class DefinitionItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    term: str
    definition: str


class SaveDefinitionsRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    items: list[DefinitionItem]


class VerdictRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    verdict: Literal["right", "wrong"]


async def call_workspaces(principal: Principal, action: str, **params: Any) -> dict[str, Any]:
    """One workspaces.manage call as this principal (also used by the setup flow)."""
    async with MCPSession(
        tenant_id=principal.tenant_id,
        base_url=os.environ.get("MCP_URL", "http://mcp:8080"),
    ) as session:
        return await workspaces_manage(session, principal_id=principal.principal_id, action=action, **params)


def register_workspace_routes(app: Any, require_principal: Any, audit: Any) -> None:
    """Register the /workspaces routes on the FastAPI application."""

    async def _execute(principal: Principal, request: Request, action: str, route_path: str,
                       **params: Any) -> dict[str, Any]:
        client_ip = request.client.host if request.client else None
        try:
            result = await call_workspaces(principal, action, **params)
        except Exception as exc:
            logger.error("Workspace action %s failed: %s", action, exc, exc_info=True)
            raise HTTPException(status_code=503,
                                detail="Workspace service unavailable. Try again shortly.") from None

        effective_id = params.get("workspace_id") or (result.get("workspace") or {}).get("workspace_id")
        detail = f"workspaces:{action}" + (f" workspace={effective_id}" if effective_id else "")
        if not result.get("ok"):
            await audit(principal.principal_id, principal.tenant_id, route_path, "error",
                        client_ip=client_ip, detail=f"{detail} {result.get('error')}")
            raise HTTPException(status_code=error_status(result.get("error")),
                                detail=result.get("message") or "Request failed.")
        await audit(principal.principal_id, principal.tenant_id, route_path, "allowed",
                    client_ip=client_ip, detail=detail)
        return result

    @app.get("/workspaces", tags=["workspaces"])
    async def list_workspaces(request: Request, principal: Principal = Depends(require_principal)) -> dict[str, Any]:
        result = await _execute(principal, request, "list", "/workspaces")
        return {"workspaces": result.get("workspaces", [])}

    @app.post("/workspaces", status_code=201, tags=["workspaces"])
    async def create_workspace(body: CreateWorkspaceRequest, request: Request,
                               principal: Principal = Depends(require_principal)) -> dict[str, Any]:
        result = await _execute(principal, request, "create", "/workspaces", name=body.name,
                                target_schema=body.target_schema, business_context=body.business_context)
        return {"workspace": result["workspace"]}

    @app.get("/workspaces/{workspace_id}", tags=["workspaces"])
    async def get_workspace(workspace_id: str, request: Request,
                            principal: Principal = Depends(require_principal)) -> dict[str, Any]:
        result = await _execute(principal, request, "get", f"/workspaces/{workspace_id}", workspace_id=workspace_id)
        return {"workspace": result["workspace"]}

    @app.patch("/workspaces/{workspace_id}", tags=["workspaces"])
    async def update_workspace(workspace_id: str, body: UpdateWorkspaceRequest, request: Request,
                               principal: Principal = Depends(require_principal)) -> dict[str, Any]:
        result = await _execute(principal, request, "update", f"/workspaces/{workspace_id}",
                                workspace_id=workspace_id, name=body.name, business_context=body.business_context)
        return {"workspace": result["workspace"]}

    @app.delete("/workspaces/{workspace_id}", tags=["workspaces"])
    async def delete_workspace(workspace_id: str, request: Request,
                               principal: Principal = Depends(require_principal)) -> dict[str, Any]:
        await _execute(principal, request, "delete", f"/workspaces/{workspace_id}", workspace_id=workspace_id)
        return {"deleted": workspace_id}

    @app.post("/workspaces/{workspace_id}/scan", tags=["workspaces"])
    async def scan_workspace(workspace_id: str, request: Request,
                             principal: Principal = Depends(require_principal)) -> dict[str, Any]:
        result = await _execute(principal, request, "scan", f"/workspaces/{workspace_id}/scan",
                                workspace_id=workspace_id)
        return {"tables": result["tables"], "columns": result["columns"]}

    @app.get("/workspaces/{workspace_id}/catalog", tags=["workspaces"])
    async def workspace_catalog(workspace_id: str, request: Request,
                                principal: Principal = Depends(require_principal)) -> dict[str, Any]:
        result = await _execute(principal, request, "catalog", f"/workspaces/{workspace_id}/catalog",
                                workspace_id=workspace_id)
        return {"columns": result["columns"]}

    @app.put("/workspaces/{workspace_id}/descriptions", tags=["workspaces"])
    async def save_descriptions(workspace_id: str, body: SaveDescriptionsRequest, request: Request,
                                principal: Principal = Depends(require_principal)) -> dict[str, Any]:
        items = [{**item.model_dump(), "source": "owner"} for item in body.items]
        result = await _execute(principal, request, "save_descriptions",
                                f"/workspaces/{workspace_id}/descriptions", workspace_id=workspace_id, items=items)
        return {"updated": result["updated"]}

    @app.get("/workspaces/{workspace_id}/definitions", tags=["workspaces"])
    async def list_definitions(workspace_id: str, request: Request,
                               principal: Principal = Depends(require_principal)) -> dict[str, Any]:
        result = await _execute(principal, request, "definitions", f"/workspaces/{workspace_id}/definitions",
                                workspace_id=workspace_id)
        return {"definitions": result["definitions"]}

    @app.put("/workspaces/{workspace_id}/definitions", tags=["workspaces"])
    async def save_definitions(workspace_id: str, body: SaveDefinitionsRequest, request: Request,
                               principal: Principal = Depends(require_principal)) -> dict[str, Any]:
        items = [{**item.model_dump(), "source": "owner"} for item in body.items]
        result = await _execute(principal, request, "save_definitions",
                                f"/workspaces/{workspace_id}/definitions", workspace_id=workspace_id, items=items)
        return {"definitions": result["definitions"]}

    @app.delete("/workspaces/{workspace_id}/definitions/{definition_id}", tags=["workspaces"])
    async def delete_definition(workspace_id: str, definition_id: str, request: Request,
                                principal: Principal = Depends(require_principal)) -> dict[str, Any]:
        await _execute(principal, request, "delete_definition",
                       f"/workspaces/{workspace_id}/definitions/{definition_id}",
                       workspace_id=workspace_id, definition_id=definition_id)
        return {"deleted": definition_id}

    @app.post("/workspaces/{workspace_id}/approve", tags=["workspaces"])
    async def approve_workspace(workspace_id: str, request: Request,
                                principal: Principal = Depends(require_principal)) -> dict[str, Any]:
        result = await _execute(principal, request, "approve", f"/workspaces/{workspace_id}/approve",
                                workspace_id=workspace_id)
        return {"workspace": result["workspace"], "approved_columns": result["approved_columns"],
                "approved_definitions": result["approved_definitions"]}

    @app.get("/workspaces/{workspace_id}/setup", tags=["workspaces"])
    async def latest_setup_run(workspace_id: str, request: Request,
                               principal: Principal = Depends(require_principal)) -> dict[str, Any]:
        result = await _execute(principal, request, "get_run", f"/workspaces/{workspace_id}/setup",
                                workspace_id=workspace_id)
        return {"run": result["run"]}

    @app.put("/workspaces/{workspace_id}/setup/{run_id}/results/{position}", tags=["workspaces"])
    async def set_setup_verdict(workspace_id: str, run_id: str, position: int, body: VerdictRequest,
                                request: Request, principal: Principal = Depends(require_principal)) -> dict[str, Any]:
        result = await _execute(principal, request, "set_verdict",
                                f"/workspaces/{workspace_id}/setup/{run_id}/results/{position}",
                                workspace_id=workspace_id, run_id=run_id, position=position, verdict=body.verdict)
        return {"run": result["run"]}
