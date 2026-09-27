"""
orchestrator/dashboards_api.py
FastAPI routes for dashboards (docs/V3_PLAN.md Phase 2b).

WHY THIS EXISTS: a dashboard is a named, per-dataset container for tiles with a
stored layout. This layer only authenticates the caller and forwards their
identity; ownership, bounds and the dataset allowlist are enforced in the MCP's
dashboards.manage (mcp/tools/dashboard_tools.py), which is the authority.

Statuses, as for tiles (tile_render.error_status):
   - 404 (not_found) for a dashboard or tile that does not exist or is someone else's;
   - 422 (invalid) for a blank name, an unknown dataset, or a layout outside the grid;
   - 409 (limit_reached) when the per-principal dashboard quota is used up;
   - 503 when the MCP service cannot be reached.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from auth import Principal
from mcp_client import MCPSession, dashboards_manage
from tile_render import error_status

logger = logging.getLogger(__name__)


class CreateDashboardRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    target_schema: str


class RenameDashboardRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str


class LayoutItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tile_id: str
    x: int
    y: int
    w: int
    h: int


class SaveLayoutRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    items: list[LayoutItem]


def register_dashboard_routes(app: Any, require_principal: Any, audit: Any) -> None:
    """Register the /dashboards routes on the FastAPI application."""

    async def _execute(
        principal: Principal,
        request: Request,
        action: str,
        route_path: str,
        dashboard_id: str | None = None,
        **params: Any,
    ) -> dict[str, Any]:
        client_ip = request.client.host if request.client else None
        try:
            async with MCPSession(
                tenant_id=principal.tenant_id,
                base_url=os.environ.get("MCP_URL", "http://mcp:8080"),
            ) as session:
                result = await dashboards_manage(
                    session,
                    principal_id=principal.principal_id,
                    action=action,
                    dashboard_id=dashboard_id,
                    **params,
                )
        except Exception as exc:
            logger.error("Dashboard action %s failed: %s", action, exc, exc_info=True)
            raise HTTPException(status_code=503,
                                detail="Dashboard service unavailable. Try again shortly.") from None

        effective_id = dashboard_id or (result.get("dashboard") or {}).get("dashboard_id")
        detail = f"dashboards:{action}" + (f" dashboard={effective_id}" if effective_id else "")
        if not result.get("ok"):
            await audit(principal.principal_id, principal.tenant_id, route_path, "error",
                        client_ip=client_ip, detail=f"{detail} {result.get('error')}")
            raise HTTPException(status_code=error_status(result.get("error")),
                                detail=result.get("message") or "Request failed.")
        await audit(principal.principal_id, principal.tenant_id, route_path, "allowed",
                    client_ip=client_ip, detail=detail)
        return result

    @app.get("/dashboards", tags=["dashboards"])
    async def list_dashboards(
        request: Request,
        schema: str | None = None,
        principal: Principal = Depends(require_principal),
    ) -> dict[str, Any]:
        """My dashboards, optionally for one dataset, in tab order."""
        result = await _execute(principal, request, "list", "/dashboards", target_schema=schema)
        return {"dashboards": result.get("dashboards", [])}

    @app.post("/dashboards", status_code=201, tags=["dashboards"])
    async def create_dashboard(
        body: CreateDashboardRequest,
        request: Request,
        principal: Principal = Depends(require_principal),
    ) -> dict[str, Any]:
        result = await _execute(principal, request, "create", "/dashboards",
                                name=body.name, target_schema=body.target_schema)
        return {"dashboard": result["dashboard"]}

    @app.patch("/dashboards/{dashboard_id}", tags=["dashboards"])
    async def rename_dashboard(
        dashboard_id: str,
        body: RenameDashboardRequest,
        request: Request,
        principal: Principal = Depends(require_principal),
    ) -> dict[str, Any]:
        result = await _execute(principal, request, "rename", f"/dashboards/{dashboard_id}",
                                dashboard_id=dashboard_id, name=body.name)
        return {"dashboard": result["dashboard"]}

    @app.delete("/dashboards/{dashboard_id}", tags=["dashboards"])
    async def delete_dashboard(
        dashboard_id: str,
        request: Request,
        principal: Principal = Depends(require_principal),
    ) -> dict[str, Any]:
        await _execute(principal, request, "delete", f"/dashboards/{dashboard_id}",
                       dashboard_id=dashboard_id)
        return {"deleted": dashboard_id}

    @app.put("/dashboards/{dashboard_id}/layout", tags=["dashboards"])
    async def save_layout(
        dashboard_id: str,
        body: SaveLayoutRequest,
        request: Request,
        principal: Principal = Depends(require_principal),
    ) -> dict[str, Any]:
        result = await _execute(principal, request, "layout", f"/dashboards/{dashboard_id}/layout",
                                dashboard_id=dashboard_id,
                                items=[item.model_dump() for item in body.items])
        return {"dashboard_id": dashboard_id, "updated": result.get("updated", 0)}
