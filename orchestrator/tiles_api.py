"""
orchestrator/tiles_api.py
FastAPI route registrations for dashboard tiles.

WHY THIS EXISTS:
1. The API accepts no SQL. Tiles only originate from previously stored conversation
   turns that the caller already owns.
2. Ownership is enforced server-side in the MCP tiles.manage tool (scoped strictly
   by tenant_id and principal_id), and this HTTP layer only forwards the authenticated
   caller's identity from the require_principal dependency.
3. Failures map to specific HTTP statuses:
   - 404 (not_found) when a tile or source turn does not exist or belongs to another principal.
   - 422 (not_pinnable, invalid) when an answer did not come from the database, failed the
     SQL gate, or parameter bounds (such as refresh interval or non-empty title) were violated.
   - 409 (limit_reached) when the per-principal tile quota is exhausted.
   - 500 when an unexpected internal error code occurs.
   - 503 when transport or communication with the MCP service fails.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from auth import Principal
from config_loader import cfg
from mcp_client import MCPSession, tiles_manage
from tile_render import error_status, render_tile

logger = logging.getLogger(__name__)


class LayoutSlot(BaseModel):
    model_config = ConfigDict(extra="ignore")

    x: int
    y: int
    w: int
    h: int


class PinTileRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    session_id: str
    turn_index: int
    title: str | None = None
    refresh_interval_s: int | None = None
    # Phase 2b: which dashboard (default: the dataset's first), how to show it,
    # and where. The MCP validates all three.
    dashboard_id: str | None = None
    viz: str | None = None
    layout: LayoutSlot | None = None
    # Pinned in a workspace: the answer also becomes that workspace's approved
    # example (mcp/tools/tile_tools.py, property 6). The MCP checks ownership.
    workspace_id: str | None = None


class ReplaceTileRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    session_id: str
    turn_index: int


class UpdateTileRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str | None = None
    refresh_interval_s: int | None = None
    viz: str | None = None            # a kind, or "auto" to decide from the data again
    dashboard_id: str | None = None   # move to another dashboard of the same dataset


def register_tile_routes(app: Any, require_principal: Any, audit: Any) -> None:
    """Register dashboard tile routes on the FastAPI application."""

    async def _execute_tile_action(
        principal: Principal,
        request: Request,
        action: str,
        route_path: str,
        tile_id: str | None = None,
        session_id: str | None = None,
        **params: Any,
    ) -> dict[str, Any]:
        client_ip = request.client.host if request.client else None
        try:
            async with MCPSession(
                tenant_id=principal.tenant_id,
                base_url=os.environ.get("MCP_URL", "http://mcp:8080"),
            ) as session:
                result = await tiles_manage(
                    session,
                    principal_id=principal.principal_id,
                    action=action,
                    tile_id=tile_id,
                    session_id=session_id,
                    **params,
                )
        except Exception as exc:
            logger.error("Tile action %s failed: %s", action, exc, exc_info=True)
            raise HTTPException(
                status_code=503,
                detail="Tile service unavailable. Try again shortly.",
            ) from None

        if not result.get("ok"):
            detail = f"tiles:{action} {result.get('error')}"
            if tile_id:
                detail = f"{detail} tile={tile_id}"
            await audit(
                principal.principal_id,
                principal.tenant_id,
                route_path,
                "error",
                client_ip=client_ip,
                detail=detail,
            )
            status_code = error_status(result.get("error"))
            raise HTTPException(
                status_code=status_code,
                detail=result.get("message") or "Request failed.",
            )

        if action == "list":
            detail = "tiles:list" + (f" dashboard={params['dashboard_id']}" if params.get("dashboard_id") else "")
        else:
            effective_tile_id = tile_id or (result.get("tile") or {}).get("tile_id")
            detail = f"tiles:{action} tile={effective_tile_id}"
            if (result.get("example") or {}).get("saved"):
                detail = f"{detail} example={result['example']['example_id']}"

        await audit(
            principal.principal_id,
            principal.tenant_id,
            route_path,
            "allowed",
            session_id=session_id,
            client_ip=client_ip,
            detail=detail,
        )
        return result

    @app.post("/tiles", status_code=201, tags=["tiles"])
    async def pin_tile(
        body: PinTileRequest,
        request: Request,
        principal: Principal = Depends(require_principal),
    ) -> dict[str, Any]:
        result = await _execute_tile_action(
            principal,
            request,
            action="pin",
            route_path="/tiles",
            session_id=body.session_id,
            turn_index=body.turn_index,
            title=body.title,
            refresh_interval_s=body.refresh_interval_s,
            dashboard_id=body.dashboard_id,
            viz=body.viz,
            layout=body.layout.model_dump() if body.layout else None,
            workspace_id=body.workspace_id,
        )
        rendered = render_tile(result["tile"], cfg.sql.chart_max_data_rows)
        response: dict[str, Any] = {"tile": rendered}
        if "example" in result:
            response["example"] = result["example"]
        return response

    @app.get("/tiles", tags=["tiles"])
    async def list_tiles(
        request: Request,
        dashboard_id: str | None = None,
        principal: Principal = Depends(require_principal),
    ) -> dict[str, Any]:
        result = await _execute_tile_action(
            principal,
            request,
            action="list",
            route_path="/tiles",
            dashboard_id=dashboard_id,
        )
        rendered_tiles = [
            render_tile(t, cfg.sql.chart_max_data_rows)
            for t in result.get("tiles", [])
        ]
        return {"tiles": rendered_tiles}

    @app.get("/tiles/{tile_id}", tags=["tiles"])
    async def get_tile(
        tile_id: str,
        request: Request,
        principal: Principal = Depends(require_principal),
    ) -> dict[str, Any]:
        result = await _execute_tile_action(
            principal,
            request,
            action="get",
            route_path=f"/tiles/{tile_id}",
            tile_id=tile_id,
        )
        rendered = render_tile(result["tile"], cfg.sql.chart_max_data_rows)
        return {"tile": rendered}

    @app.post("/tiles/{tile_id}/refresh", tags=["tiles"])
    async def refresh_tile(
        tile_id: str,
        request: Request,
        principal: Principal = Depends(require_principal),
    ) -> dict[str, Any]:
        result = await _execute_tile_action(
            principal,
            request,
            action="refresh",
            route_path=f"/tiles/{tile_id}/refresh",
            tile_id=tile_id,
        )
        rendered = render_tile(result["tile"], cfg.sql.chart_max_data_rows)
        return {"tile": rendered}

    @app.post("/tiles/{tile_id}/replace", tags=["tiles"])
    async def replace_tile(
        tile_id: str,
        body: ReplaceTileRequest,
        request: Request,
        principal: Principal = Depends(require_principal),
    ) -> dict[str, Any]:
        result = await _execute_tile_action(
            principal,
            request,
            action="replace",
            route_path=f"/tiles/{tile_id}/replace",
            tile_id=tile_id,
            session_id=body.session_id,
            turn_index=body.turn_index,
        )
        rendered = render_tile(result["tile"], cfg.sql.chart_max_data_rows)
        return {"tile": rendered}

    @app.patch("/tiles/{tile_id}", tags=["tiles"])
    async def update_tile(
        tile_id: str,
        body: UpdateTileRequest,
        request: Request,
        principal: Principal = Depends(require_principal),
    ) -> dict[str, Any]:
        result = await _execute_tile_action(
            principal,
            request,
            action="update",
            route_path=f"/tiles/{tile_id}",
            tile_id=tile_id,
            title=body.title,
            refresh_interval_s=body.refresh_interval_s,
            viz=body.viz,
            dashboard_id=body.dashboard_id,
        )
        rendered = render_tile(result["tile"], cfg.sql.chart_max_data_rows)
        return {"tile": rendered}

    @app.post("/tiles/{tile_id}/confirm", tags=["tiles"])
    async def confirm_tile(
        tile_id: str,
        request: Request,
        principal: Principal = Depends(require_principal),
    ) -> dict[str, Any]:
        result = await _execute_tile_action(
            principal,
            request,
            action="confirm",
            route_path=f"/tiles/{tile_id}/confirm",
            tile_id=tile_id,
        )
        rendered = render_tile(result["tile"], cfg.sql.chart_max_data_rows)
        return {"tile": rendered}

    @app.delete("/tiles/{tile_id}", tags=["tiles"])
    async def delete_tile(
        tile_id: str,
        request: Request,
        principal: Principal = Depends(require_principal),
    ) -> dict[str, Any]:
        await _execute_tile_action(
            principal,
            request,
            action="unpin",
            route_path=f"/tiles/{tile_id}",
            tile_id=tile_id,
        )
        return {"deleted": tile_id}
