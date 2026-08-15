"""
evaluator/mcp_client.py
Minimal MCP client for the evaluator service.

Own copy of the MCPSession pattern from orchestrator/mcp_client.py, trimmed
to what the evaluator's pipeline actually needs: executing a ground-truth
SELECT (data.run_select) to get the reference rows ExecutionScorer compares
against. Per A1's topology row ("evaluator | ... | orchestrator, mcp
(read-only fixtures)"), this is a read-only client exactly like the
orchestrator's own -- mcp always connects to postgres as statesync_reader
(Layer 3), so a second SELECT-only caller adds no new write surface.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any

import httpx
from config_loader import cfg

logger = logging.getLogger(__name__)


class MCPProtocolError(Exception):
    """Raised on MCP transport/protocol errors, not on query failures."""


class MCPSession:
    def __init__(self, tenant_id: str, base_url: str | None = None) -> None:
        self.tenant_id = tenant_id
        self.session_id = str(uuid.uuid4())
        self._base_url = base_url or os.environ.get("MCP_URL", "http://mcp:8080")
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(
                (cfg.mcp.statement_timeout_ms / 1000) + cfg.mcp.network_overhead_s,  # noqa: literals — ms conversion
                connect=cfg.mcp.connect_timeout_s,
            ),
            headers={
                "X-Tenant-ID": tenant_id,
                "X-Session-ID": self.session_id,
                "Content-Type": "application/json",
            },
        )

    async def __aenter__(self) -> "MCPSession":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self._client.aclose()

    async def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        request_id = str(uuid.uuid4())
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
        response = await self._client.post("/rpc", json=payload)
        response.raise_for_status()
        body = response.json()
        if body.get("id") != request_id:
            raise MCPProtocolError(f"Response ID mismatch: expected {request_id!r}, got {body.get('id')!r}.")
        if "error" in body:
            err = body["error"]
            raise MCPProtocolError(f"MCP JSON-RPC error {err.get('code')}: {err.get('message')}")
        return body.get("result")


async def data_run_select(session: MCPSession, sql: str, max_rows: int | None = None) -> dict[str, Any]:
    """A4.4 data.run_select — same ok/error contract as the orchestrator's own wrapper."""
    params: dict[str, Any] = {"sql": sql}
    if max_rows is not None:
        params["max_rows"] = max_rows
    return await session.call("data.run_select", params)
