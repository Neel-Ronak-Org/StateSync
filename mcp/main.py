"""
mcp/main.py
MCP service — JSON-RPC 2.0 server exposing the 9 Phase-B + C1 + E1 tools.

Tools exposed (A4):
  schema.list_tables      (A4.1)
  schema.describe_table   (A4.2)
  data.sample_rows        (A4.3)
  data.run_select         (A4.4) — with all 3 SELECT-enforcement layers
  vector.semantic_search  (A4.6, E1)
  vector.hybrid_select    (A4.7, E1) — with the ss_embed request-scoped vector
  docs.get_passage        (A4.9, E1)
  memory.get_trajectory   (A4.8, C1)
  memory.put_trajectory   (A4.8, C1)
  history.append_turn       (cycle 007, Workstream A)
  history.get_recent_turns  (cycle 007, Workstream A)
  history.check_session     (session ownership, docs/V3_PLAN.md 0.2)
  tiles.manage              (dashboard tiles, docs/V3_PLAN.md Phase 1)
  history.get_turn_rows     (full result-set storage)
  audit.record              (per-identity audit log)

Per A4: tenant_id is bound at session establishment from the X-Tenant-ID header.
It is not a tool parameter — it is injected server-side by session middleware.

Per A4.4: A failed query returns ok: false — it does NOT raise a transport-level
fault. JSON-RPC errors are reserved for protocol failures only.
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
from qdrant_store import init_qdrant, close_qdrant
from session import (set_session, get_tenant_id, enforce_tenancy_policy,
                     TenancyPolicyNotImplemented, TenantNotAllowed,
                     TenancyAcknowledgementMissing)
from tools.schema_tools import list_tables, describe_table
from tools.data_tools import sample_rows, run_select
from tools.vector_tools import semantic_search, hybrid_select, get_passage
from tools.memory_tools import get_trajectory, put_trajectory
from tools.tile_tools import manage as tiles_manage
from tools.dashboard_tools import manage as dashboards_manage
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
    await init_pool()
    await init_history_pool()

    # A5: recomputed at mcp startup; mismatch is a startup failure, not a
    # warning. Uses the ordinary statesync_reader pool (see fingerprint.py).
    async with get_connection() as conn:
        await verify_fingerprint_at_startup(conn)

    await init_qdrant()

    logger.info("MCP service ready. Exposing %d tools.", len(_REGISTERED_TOOLS))
    yield
    await close_qdrant()
    await close_embeddings_client()
    await close_pool()
    await close_history_pool()
    logger.info("MCP service shutdown.")


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="StateSync MCP",
    description="MCP tool server — sole credential holder (A1).",
    version="0.1.0-phase-b",
    lifespan=lifespan,
)


# ── JSON-RPC dispatch ─────────────────────────────────────────────────────────
# mcp.tool_ceiling is the single source of truth; the env var remains an
# explicit override. Raised 15 -> 20 for tiles (docs/V3_PLAN.md decision log).
TOOL_CEILING = int(os.environ.get("TOOL_CEILING", cfg.mcp.tool_ceiling))

_REGISTERED_TOOLS: dict[str, Any] = {
    "schema.list_tables":    list_tables,
    "schema.describe_table": describe_table,
    "data.sample_rows":      sample_rows,
    "data.run_select":       run_select,
    "vector.semantic_search": semantic_search,
    "vector.hybrid_select":   hybrid_select,
    "docs.get_passage":       get_passage,
    "memory.get_trajectory": get_trajectory,
    "memory.put_trajectory": put_trajectory,
    "history.append_turn":      append_turn,
    "history.get_recent_turns": get_recent_turns,
    "history.check_session":    check_session,
    "history.get_turn_rows":    get_turn_rows,
    "history.list_sessions":    list_sessions,
    "tiles.manage":             tiles_manage,
    "dashboards.manage":        dashboards_manage,
    "history.delete_session":   delete_session,
    "audit.record": record_audit,
}

assert len(_REGISTERED_TOOLS) <= TOOL_CEILING, (
    f"Tool count {len(_REGISTERED_TOOLS)} exceeds ceiling {TOOL_CEILING}. "
    "A BD entry is required before adding more tools (A4 spec)."
)


import datetime
from decimal import Decimal
from uuid import UUID


def _serialize_json(obj: Any) -> Any:
    if isinstance(obj, (datetime.datetime, datetime.date, datetime.time)):
        return obj.isoformat()
    if isinstance(obj, datetime.timedelta):
        # psycopg maps a Postgres INTERVAL (e.g. AVG(delivered_at -
        # promised_at)) to timedelta. Previously unhandled: fell through to
        # the raw object, which Starlette's JSON encoder cannot serialize --
        # an uncaught TypeError deep in the response path, surfacing as a
        # 500 transport fault. A4.4 requires a failed query to return
        # ok:false as data, never a transport-level fault; an aggregate over
        # a timestamp difference (delivery delay, dispute resolution time,
        # etc.) is an ordinary, expected result shape, not a failure.
        return obj.total_seconds()
    if isinstance(obj, Decimal):
        return float(obj) if obj % 1 else int(obj)
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, bytes):
        return obj.hex()
    if isinstance(obj, dict):
        return {k: _serialize_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize_json(x) for x in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    # Catch-all for any remaining psycopg type mapping that JSON cannot
    # encode natively. Found the hard way twice, one type at a time:
    # INTERVAL -> timedelta (F-V2 baseline), then INET -> IPv4Address
    # (F-V1 Class 3). Both surfaced as an uncaught TypeError deep inside
    # Starlette's encoder, i.e. a 500 transport fault -- exactly what A4.4
    # forbids ("A failed query returns ok: false ... it does not raise a
    # transport-level fault"), and worse, a SUCCESSFUL query being reported
    # as a transport failure. Postgres has many more such types (NUMERIC
    # ranges, arrays of custom types, JSONB edge cases, CIDR, MACADDR,
    # tsvector...); enumerating them individually is whack-a-mole. Anything
    # unrecognized degrades to its string form, which is lossy but honest
    # and keeps the A4.4 contract intact.
    logger.debug("Serializing unrecognized type %s via str().", type(obj).__name__)
    return str(obj)


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

    Per A0.6: tenant_id is extracted from X-Tenant-ID header and bound
    into the session context before any tool runs. Agents cannot forge it
    because it comes from the session context, not from params.
    """
    # Establish session context from headers (A0.6)
    tenant_id = request.headers.get("X-Tenant-ID", "")
    session_id = request.headers.get("X-Session-ID", "")

    if not tenant_id:
        return JSONResponse(
            _jsonrpc_error(None, -32600, "Missing X-Tenant-ID header."),
            status_code=400,
        )

    set_session(tenant_id=tenant_id, session_id=session_id)

    # Appendix 4 seam: MCP session policy layer for tenancy.enforced (A0.5).
    # No-op while the flag is false (the shipped default, D6); real dispatch
    # point regardless -- see session.enforce_tenancy_policy's docstring.
    try:
        enforce_tenancy_policy(tenant_id)
    except TenantNotAllowed as exc:
        # 403, not 501: the policy IS implemented and it is refusing this
        # tenant. Distinguishing the two matters -- "not built" and "not
        # permitted" are different operational facts.
        logger.warning("Tenancy admission refused: %s", exc)
        return JSONResponse(
            _jsonrpc_error(None, -32602, str(exc)),
            status_code=403,
        )
    except TenancyAcknowledgementMissing as exc:
        logger.error("Tenancy misconfiguration: %s", exc)
        return JSONResponse(
            _jsonrpc_error(None, -32603, str(exc)),
            status_code=501,
        )
    except TenancyPolicyNotImplemented as exc:
        return JSONResponse(
            _jsonrpc_error(None, -32603, str(exc)),
            status_code=501,
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
