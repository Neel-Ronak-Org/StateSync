"""
orchestrator/builder_api.py
Routes for "Build with AI" (docs/V3_PLAN.md Phase 2b; the work itself is in
dashboard_builder.py).

  POST /dashboards/{id}/build {goal?}  start a build (202); charges the MODEL
                                       rate budget, like /query
  GET  /dashboards/{id}/build          the latest build of that dashboard, or null

Ownership: the dashboard is looked up through the MCP's dashboards.manage,
scoped to the caller, so someone else's dashboard is a 404. Jobs are kept per
principal, so a status read can only ever return the caller's own build.
Status reads are not audited: the web app polls them every 1.5 s while a build
runs, and the build itself audits every question it asks.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from auth import Principal
from config_loader import cfg
from dashboard_builder import (HISTORICAL_TIME_RULE, LIVE_TIME_RULE, PLANNER_SYSTEM_PROMPT, BuildJob,
                               BuildRegistry, PlannedInsight, new_job, parse_plan, run_build,
                               summarize_schema)
from llm.provider import get_agent_llm
from mcp_client import MCPSession, dashboards_manage, schema_describe_table, schema_list_tables, tiles_manage

logger = logging.getLogger(__name__)

PLANNER_ATTEMPTS = 2   # noqa: literals - one retry for an unparseable plan, not configuration


class BuildRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    goal: str | None = None


def _mcp(principal: Principal) -> MCPSession:
    return MCPSession(tenant_id=principal.tenant_id, base_url=os.environ.get("MCP_URL", "http://mcp:8080"))


def register_build_routes(
    app: Any,
    require_principal: Any,
    require_principal_for_model: Any,
    audit: Any,
    answer_question: Callable[..., Any],
) -> None:
    registry = BuildRegistry()

    async def owned_dashboard(principal: Principal, dashboard_id: str) -> dict | None:
        try:
            async with _mcp(principal) as session:
                result = await dashboards_manage(session, principal_id=principal.principal_id, action="list")
        except Exception as exc:
            logger.error("dashboard lookup failed: %s", exc, exc_info=True)
            raise HTTPException(status_code=503, detail="Dashboard service unavailable. Try again shortly.") from None
        return next((d for d in result.get("dashboards", []) if d.get("dashboard_id") == dashboard_id), None)

    def planner(principal: Principal, schema: str):
        async def plan(goal: str | None) -> list[PlannedInsight] | str:
            async with _mcp(principal) as session:
                listing = await schema_list_tables(session, schema)
                tables = listing.get("tables", [])[: int(cfg.dashboards.planner_max_tables)]
                columns: dict[str, list[dict]] = {}
                for table in tables:
                    described = await schema_describe_table(session, table["name"], schema)
                    columns[table["name"]] = described.get("columns", [])
            summary = summarize_schema(schema, tables, columns, int(cfg.dashboards.planner_schema_max_chars))
            live = schema in cfg.dashboards.live_schemas
            system = PLANNER_SYSTEM_PROMPT.format(
                min_items=cfg.dashboards.min_insights, max_items=cfg.dashboards.max_insights,
                time_rule=LIVE_TIME_RULE if live else HISTORICAL_TIME_RULE,
            )
            focus = goal or "a general overview of how the business is doing"
            user = f"{summary}\n\nFocus: {focus}"
            llm = get_agent_llm("decomposer")
            result: list[PlannedInsight] | str = "The planner did not answer."
            for _ in range(PLANNER_ATTEMPTS):
                reply = await llm.complete(system=system, messages=[{"role": "user", "content": user}],
                                           max_tokens=int(cfg.dashboards.planner_max_tokens))
                result = parse_plan(reply.text, int(cfg.dashboards.min_insights), int(cfg.dashboards.max_insights),
                                    int(cfg.tiles.title_max_chars), int(cfg.dashboards.question_max_chars))
                if not isinstance(result, str):
                    return result
                logger.warning("planner reply unusable (%s); %s", result, reply.text[:300])
            return result
        return plan

    @app.post("/dashboards/{dashboard_id}/build", status_code=202, tags=["dashboards"])
    async def start_build(
        dashboard_id: str,
        body: BuildRequest,
        request: Request,
        principal: Principal = Depends(require_principal_for_model),
    ) -> dict[str, Any]:
        client_ip = request.client.host if request.client else None
        route = f"/dashboards/{dashboard_id}/build"
        dashboard = await owned_dashboard(principal, dashboard_id)
        if dashboard is None:
            await audit(principal.principal_id, principal.tenant_id, route, "error",
                        client_ip=client_ip, detail="dashboards:build not_found")
            raise HTTPException(status_code=404, detail="No such dashboard.")
        if registry.active(principal.principal_id):
            raise HTTPException(status_code=409, detail="A build is already running. Wait for it to finish.")

        goal = (body.goal or "").strip()[: int(cfg.dashboards.goal_max_chars)] or None
        schema = dashboard["target_schema"]
        job = new_job(principal.principal_id, dashboard_id, schema, goal)

        async def ask(question: str, session_id: str) -> dict:
            response = await answer_question(principal, question, session_id, schema, client_ip, route_path=route)
            return response.model_dump()

        async def pin(session_id: str, turn_index: int, insight: PlannedInsight, slot: dict) -> dict:
            async with _mcp(principal) as session:
                return await tiles_manage(session, principal_id=principal.principal_id, action="pin",
                                          session_id=session_id, turn_index=turn_index, title=insight.title,
                                          dashboard_id=dashboard_id, viz=insight.viz, layout=slot)

        plan = planner(principal, schema)

        async def work(j: BuildJob) -> None:
            await run_build(j, plan, ask, pin, columns=int(cfg.dashboards.grid_columns))
            added = sum(1 for item in j.items if item.state == "added")
            logger.info("dashboard build %s on %s finished: %s, %d/%d added",
                        j.job_id, dashboard_id, j.state, added, len(j.items))

        registry.start(job, work)
        await audit(principal.principal_id, principal.tenant_id, route, "allowed",
                    client_ip=client_ip, detail=f"dashboards:build dashboard={dashboard_id} job={job.job_id}")
        return {"job": job.view()}

    @app.get("/dashboards/{dashboard_id}/build", tags=["dashboards"])
    async def build_status(
        dashboard_id: str,
        principal: Principal = Depends(require_principal),
    ) -> dict[str, Any]:
        job = registry.latest(principal.principal_id, dashboard_id)
        return {"job": job.view() if job else None}
