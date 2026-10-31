"""
mcp/main.py
The MCP tool server: the only service that holds database passwords. The orchestrator (and so every
language model) reaches data only by calling these tools over JSON-RPC 2.0 at POST /rpc.

  schema.list_tables, schema.describe_table   what tables and columns a database has
  data.sample_rows                             a few rows of a table, for the SQL writer
  data.run_select                              run one model-written query through the three read-only layers
  vector.semantic_search                       the passages of a workspace's documents closest to a text
  history.*                                    conversations: append, read, check ownership, delete
  workspaces.manage, library.manage            workspaces and their knowledge; folders and uploaded documents
  tiles.manage, dashboards.manage              answers pinned to dashboards
  audit.record                                 the per-person audit log

Only the orchestrator may call /rpc: every request carries a shared service token (service_auth.py),
checked before anything else. The tenant comes from the X-Tenant-ID header bound to the session, never
from a tool's arguments, and row-level security in Postgres enforces it again. A failed query returns
ok: false, not a protocol error.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from config_loader import cfg
from db import init_pool, close_pool, get_connection, init_history_pool, close_history_pool
from embeddings import close_embeddings_client
from fingerprint import verify_fingerprint_at_startup
from service_auth import ServiceTokenMissing, authorized, load_token
from session import set_session, enforce_tenancy_policy, TenantNotAllowed
from tools.schema_tools import list_tables, describe_table
from tools.data_tools import sample_rows, run_select
from tools.vector_tools import semantic_search
from serialization import to_json_safe
from tools.tile_tools import manage as tiles_manage
from tools.dashboard_tools import manage as dashboards_manage
from tools.workspace_tools import manage as workspaces_manage
from connections import close_all as close_external_connections
from tools.library_tools import manage as library_manage
from tools.history_tools import (append_turn, check_session, delete_session, get_recent_turns,
                                 get_turn_rows, list_sessions, record_audit)

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail closed (service_auth.py rule 2): no token, no server.
    try:
        app.state.service_token = load_token()
    except ServiceTokenMissing as exc:
        logger.critical("%s", exc)
        raise
    await init_pool()
    await init_history_pool()

    # The schema fingerprint is recomputed at startup; a mismatch is a startup failure, not a
    # warning. Uses the ordinary statesync_reader pool (see fingerprint.py).
    async with get_connection() as conn:
        await verify_fingerprint_at_startup(conn)

    logger.info("MCP service ready. Exposing %d tools.", len(_REGISTERED_TOOLS))
    yield
    await close_external_connections()
    await close_embeddings_client()
    await close_pool()
    await close_history_pool()
    logger.info("MCP service shutdown.")


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="StateSync MCP",
    description="The tool server: the only service with database passwords.",
    version="1.0.0",
    lifespan=lifespan,
)


# ── JSON-RPC dispatch ─────────────────────────────────────────────────────────
# mcp.tool_ceiling is the single source of truth; the env var remains an
# explicit override. Raised 15 -> 20 for tiles.
TOOL_CEILING = int(os.environ.get("TOOL_CEILING", cfg.mcp.tool_ceiling))

_REGISTERED_TOOLS: dict[str, Any] = {
    "schema.list_tables":    list_tables,
    "schema.describe_table": describe_table,
    "data.sample_rows":      sample_rows,
    "data.run_select":       run_select,
    "vector.semantic_search": semantic_search,
    "history.append_turn":      append_turn,
    "history.get_recent_turns": get_recent_turns,
    "history.check_session":    check_session,
    "history.get_turn_rows":    get_turn_rows,
    "history.list_sessions":    list_sessions,
    "tiles.manage":             tiles_manage,
    "dashboards.manage":        dashboards_manage,
    "workspaces.manage":        workspaces_manage,
    "library.manage":           library_manage,
    "history.delete_session":   delete_session,
    "audit.record": record_audit,
}

assert len(_REGISTERED_TOOLS) <= TOOL_CEILING, (
    f"Tool count {len(_REGISTERED_TOOLS)} exceeds ceiling {TOOL_CEILING}. "
    "A BD entry is required before adding more tools."
)




RPC_UNAUTHORIZED = -32001  # noqa: literals - JSON-RPC implementation-defined server error (-32000..-32099)


def _serialize_json(obj: Any) -> Any:
    return to_json_safe(obj)


def _jsonrpc_error(request_id: Any, code: int, message: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _jsonrpc_result(request_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": _serialize_json(result)}


@app.get("/health", tags=["ops"])
async def health() -> dict:
    return {"status": "ok", "service": "mcp", "tools": list(_REGISTERED_TOOLS.keys())}


@app.post("/rpc", tags=["rpc"])
async def rpc_handler(request: Request) -> JSONResponse:
    """
    JSON-RPC 2.0 dispatch endpoint.

    tenant_id is taken from the X-Tenant-ID header and bound
    into the session context before any tool runs. Agents cannot forge it
    because it comes from the session context, not from params.
    """
    # Only the orchestrator: the identity headers below are trusted only from a
    # caller holding the service token (service_auth.py).
    if not authorized(request.headers.get("Authorization"), getattr(request.app.state, "service_token", "")):
        logger.warning("Refused an /rpc call without a valid service token from %s",
                       request.client.host if request.client else "unknown")
        return JSONResponse(_jsonrpc_error(None, RPC_UNAUTHORIZED, "Unauthorized."), status_code=401)

    # Establish session context from headers
    tenant_id = request.headers.get("X-Tenant-ID", "")
    session_id = request.headers.get("X-Session-ID", "")

    if not tenant_id:
        return JSONResponse(
            _jsonrpc_error(None, -32600, "Missing X-Tenant-ID header."),
            status_code=400,
        )

    set_session(tenant_id=tenant_id, session_id=session_id,
                workspace_id=request.headers.get("X-Workspace-ID", "") or None)

    # Admission control for a multi-tenant deployment ([tenancy] enforced; off for one owner).
    try:
        enforce_tenancy_policy(tenant_id)
    except TenantNotAllowed as exc:
        logger.warning("Tenancy admission refused: %s", exc)
        return JSONResponse(
            _jsonrpc_error(None, -32602, str(exc)),
            status_code=403,
        )

    # Parse JSON-RPC body
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            _jsonrpc_error(None, -32700, "Parse error: invalid JSON."),
            status_code=400,
        )

    if body.get("jsonrpc") != "2.0":
        return JSONResponse(
            _jsonrpc_error(body.get("id"), -32600, "Invalid Request: jsonrpc must be '2.0'."),
            status_code=400,
        )

    request_id = body.get("id")
    method = body.get("method", "")
    params = body.get("params", {})

    if method not in _REGISTERED_TOOLS:
        return JSONResponse(
            _jsonrpc_error(request_id, -32601, f"Method not found: {method!r}"),
            status_code=404,
        )

    handler = _REGISTERED_TOOLS[method]

    try:
        # Dispatch with params; each handler validates its own inputs
        if isinstance(params, dict):
            result = await handler(**params)
        else:
            result = await handler()

        return JSONResponse(_jsonrpc_result(request_id, result))

    except ValueError as exc:
        # Validation errors (bad table name, etc.) — invalid params
        logger.warning("Tool %r invalid params: %s", method, exc)
        return JSONResponse(
            _jsonrpc_error(request_id, -32602, f"Invalid params: {exc}"),
            status_code=422,
        )
    except Exception as exc:
        # Unexpected errors — internal error (NOT query failures; those are ok:false in result)
        logger.error("Tool %r internal error: %s", method, exc, exc_info=True)
        return JSONResponse(
            _jsonrpc_error(request_id, -32603, f"Internal error: {exc}"),
            status_code=500,
        )


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=os.environ.get("MCP_HOST", "0.0.0.0"),
        port=int(os.environ.get("MCP_PORT", "8080")),
        log_level="info",
    )
