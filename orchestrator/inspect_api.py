"""
orchestrator/inspect_api.py
One read-only route for the web app's Pipeline Inspector and Schema page: the tables, columns and foreign
keys of a workspace's database.

WHY THIS EXISTS. An answer's trace says which SQL ran; to show where that SQL sits in the database, the page
needs the database's shape. The MCP already describes it (schema.list_tables, schema.describe_table: the
calls the SQL agent and the front door's database card use), for a built-in schema and for a connected
database alike. This route asks those two tools as the signed-in principal, for the dataset of a workspace
that principal owns, and returns the result as one graph. It reads catalog metadata only: no row of data,
no model call, no credential. At most workspaces.scan_max_tables tables are described.

Statuses: 404 for a workspace that does not exist or is someone else's; 503 when the MCP cannot be reached.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import Depends, HTTPException, Request

from auth import Principal
from config_loader import cfg
from mcp_client import MCPSession, schema_describe_table, schema_list_tables
from schema_graph import schema_graph
from workspaces_api import call_workspaces

logger = logging.getLogger(__name__)


def register_inspect_routes(app: Any, require_principal: Any, audit: Any) -> None:
    """Register GET /workspaces/{workspace_id}/schema-graph on the FastAPI application."""

    @app.get("/workspaces/{workspace_id}/schema-graph", tags=["inspect"])
    async def workspace_schema_graph(workspace_id: str, request: Request,
                                     principal: Principal = Depends(require_principal)) -> dict[str, Any]:
        client_ip = request.client.host if request.client else None
        route_path = f"/workspaces/{workspace_id}/schema-graph"
        try:
            workspace = (await call_workspaces(principal, "get", workspace_id=workspace_id)).get("workspace")
        except Exception as exc:
            logger.error("Schema graph: workspace lookup failed for %s: %s", workspace_id, exc)
            raise HTTPException(status_code=503, detail="Workspace service unavailable. Try again shortly.") from None
        if workspace is None:
            await audit(principal.principal_id, principal.tenant_id, route_path, "error",
                        client_ip=client_ip, detail="inspect:schema_graph not_found")
            raise HTTPException(status_code=404, detail="No such workspace.")
        dataset = workspace["target_schema"]
        try:
            async with MCPSession(tenant_id=principal.tenant_id, base_url=os.environ.get("MCP_URL", "http://mcp:8080"),
                                  workspace_id=workspace_id, dataset=dataset) as session:
                listing = await schema_list_tables(session, dataset)
                wanted = listing.get("tables", [])[:int(cfg.workspaces.scan_max_tables)]
                described = [await schema_describe_table(session, t["name"], dataset) for t in wanted]
        except Exception as exc:
            logger.error("Schema graph: schema read failed for workspace %s: %s", workspace_id, exc)
            raise HTTPException(status_code=503, detail="The database could not be read. Try again shortly.") from None
        await audit(principal.principal_id, principal.tenant_id, route_path, "allowed",
                    client_ip=client_ip, detail=f"inspect:schema_graph workspace={workspace_id}")
        return schema_graph(dataset, listing, described, len(listing.get("tables", [])))
