"""
orchestrator/workspaces_api.py
The /workspaces routes: setting up a workspace and what the owner teaches StateSync about it.

A workspace is one business: a connected database, the document folders it searches, and the owner's
knowledge. Setting one up:
  1. create it on a connected database (and optionally describe the business in a few lines);
  2. scan: list the tables and columns (with sample values);
  3. draft: an AI drafts a description of each table and column (POST /draft, in the background);
  4. the owner edits and approves the notes, and may write definitions of business terms;
  5. later, a thumbs-up on an answer saves it as an approved example (POST /examples); the same question
     asked again then runs the approved SQL as is and is labelled "verified".
Only approved text ever reaches the SQL writer and the router.

This layer authenticates the caller and forwards who they are; ownership, limits and the SQL safety gate on
examples are enforced in the MCP's workspaces.manage (mcp/tools/workspace_tools.py). Text written through
these routes is stored as the owner's; AI drafts are written only by the draft job here.

Statuses, as for tiles and dashboards (tile_render.error_status):
   - 404 (not_found) for a workspace, definition or run that does not exist or is someone else's;
   - 422 (invalid) for a blank name, an unknown dataset, or malformed items;
   - 409 (limit_reached) when a quota is used up;
   - 503 when the MCP service cannot be reached.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from auth import Principal
from llm.provider import get_agent_llm
from mcp_client import MCPSession, workspaces_manage
from tile_render import error_status
from workspace_setup import SetupJob, SetupRegistry, new_job, run_draft

logger = logging.getLogger(__name__)

HTTP_ACCEPTED = 202   # noqa: literals - HTTP status code


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


class ExampleRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    question: str
    sql: str


class RemoveExampleRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    question: str


async def call_workspaces(principal: Principal, action: str, **params: Any) -> dict[str, Any]:
    """One workspaces.manage call as this principal."""
    async with MCPSession(
        tenant_id=principal.tenant_id,
        base_url=os.environ.get("MCP_URL", "http://mcp:8080"),
    ) as session:
        return await workspaces_manage(session, principal_id=principal.principal_id, action=action, **params)


def register_workspace_routes(app: Any, require_principal: Any, audit: Any, require_principal_for_model: Any) -> None:
    """Register the /workspaces routes on the FastAPI application. The draft route spends model calls, so
    it uses the tighter rate limit (require_principal_for_model)."""

    registry = SetupRegistry()

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

    @app.post("/workspaces/{workspace_id}/examples", tags=["workspaces"])
    async def add_example(workspace_id: str, body: ExampleRequest, request: Request,
                          principal: Principal = Depends(require_principal)) -> dict[str, Any]:
        # A thumbs-up is the owner's word; the MCP runs the SQL through the gate and caps the count.
        result = await _execute(principal, request, "add_example", f"/workspaces/{workspace_id}/examples",
                                workspace_id=workspace_id, question=body.question, sql=body.sql, source="owner")
        return {"example_id": result["example_id"]}

    @app.delete("/workspaces/{workspace_id}/examples", tags=["workspaces"])
    async def remove_example(workspace_id: str, body: RemoveExampleRequest, request: Request,
                             principal: Principal = Depends(require_principal)) -> dict[str, Any]:
        result = await _execute(principal, request, "remove_example", f"/workspaces/{workspace_id}/examples",
                                workspace_id=workspace_id, question=body.question)
        return {"removed": result["removed"]}

    @app.get("/workspaces/{workspace_id}/examples", tags=["workspaces"])
    async def list_examples(workspace_id: str, request: Request,
                            principal: Principal = Depends(require_principal)) -> dict[str, Any]:
        result = await _execute(principal, request, "examples", f"/workspaces/{workspace_id}/examples",
                                workspace_id=workspace_id)
        return {"examples": result["examples"]}

    @app.post("/workspaces/{workspace_id}/approve", tags=["workspaces"])
    async def approve_workspace(workspace_id: str, request: Request,
                                principal: Principal = Depends(require_principal)) -> dict[str, Any]:
        result = await _execute(principal, request, "approve", f"/workspaces/{workspace_id}/approve",
                                workspace_id=workspace_id)
        return {"workspace": result["workspace"], "approved_columns": result["approved_columns"],
                "approved_definitions": result["approved_definitions"]}

    def manager(principal: Principal):
        async def manage(action: str, **params: Any) -> dict:
            return await call_workspaces(principal, action, **params)
        return manage

    async def complete(system: str, user: str, max_tokens: int) -> str:
        reply = await get_agent_llm("decomposer").complete(
            system=system, messages=[{"role": "user", "content": user}], max_tokens=max_tokens)
        return reply.text

    async def owned(principal: Principal, request: Request, workspace_id: str, route: str) -> dict:
        result = await _execute(principal, request, "get", route, workspace_id=workspace_id)
        return result["workspace"]

    @app.post("/workspaces/{workspace_id}/draft", status_code=HTTP_ACCEPTED, tags=["workspaces"])
    async def start_draft(workspace_id: str, request: Request,
                          principal: Principal = Depends(require_principal_for_model)) -> dict[str, Any]:
        """Scan (if needed) and draft descriptions and definitions with AI, in the background."""
        workspace = await owned(principal, request, workspace_id, f"/workspaces/{workspace_id}/draft")
        if registry.active(principal.principal_id):
            raise HTTPException(status_code=409, detail="A setup step is already running. Wait for it to finish.")
        job = new_job("draft", principal.principal_id, workspace_id)
        manage = manager(principal)

        async def work(j: SetupJob) -> None:
            await run_draft(j, manage, complete, workspace["business_context"])
            logger.info("workspace draft %s on %s: %s (%d/%d)", j.job_id, workspace_id, j.state, j.done, j.total)

        registry.start(job, work)
        return {"job": job.view()}

    @app.get("/workspaces/{workspace_id}/draft", tags=["workspaces"])
    async def draft_status(workspace_id: str, request: Request,
                           principal: Principal = Depends(require_principal)) -> dict[str, Any]:
        await owned(principal, request, workspace_id, f"/workspaces/{workspace_id}/draft")
        job = registry.latest(principal.principal_id, workspace_id, "draft")
        return {"job": job.view() if job else None}
