"""
orchestrator/mcp_client.py
MCP client — sends JSON-RPC 2.0 calls to the mcp service.

Per A0.6: tenant_id is bound at session establishment and injected server-side.
It is part of every call's session context, never passed as a parameter.

Per A4: All tools are JSON-RPC 2.0 methods. A failed query returns ok: false
with a populated error field — it does not raise a transport-level fault.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any

import httpx
from config_loader import cfg

logger = logging.getLogger(__name__)

# ── Session ───────────────────────────────────────────────────────────────────
class MCPSession:
    """
    Represents a session context with the MCP service.
    tenant_id is bound at construction and carried in every request header.
    The server injects it server-side; agents never see it as a parameter.
    """

    def __init__(self, tenant_id: str, base_url: str | None = None) -> None:
        self.tenant_id = tenant_id
        self.session_id = str(uuid.uuid4())
        self._base_url = (
            base_url
            or os.environ.get("MCP_URL", "http://mcp:8080")
        )
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(
                # statement_timeout (ms→s) + network overhead buffer
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
        """
        Send a JSON-RPC 2.0 request to the MCP service.

        Returns the `result` field on success.
        Returns the `error` field on JSON-RPC error (transport/protocol errors).
        Note: Query failures are returned as ok: false in result, NOT as
        JSON-RPC errors (A4.4 contract).

        Raises:
            httpx.HTTPError: On transport-level failures.
            MCPProtocolError: On malformed JSON-RPC responses.
        """
        request_id = str(uuid.uuid4())
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        }

        logger.debug(
            "MCP call: method=%s session=%s tenant=%s",
            method, self.session_id, self.tenant_id,
        )

        response = await self._client.post("/rpc", json=payload)
        response.raise_for_status()

        body = response.json()

        if body.get("id") != request_id:
            raise MCPProtocolError(
                f"Response ID mismatch: expected {request_id!r}, "
                f"got {body.get('id')!r}."
            )

        if "error" in body:
            # JSON-RPC protocol-level error (malformed request, invalid session).
            # NOT a query failure (those are ok: false in result).
            err = body["error"]
            raise MCPProtocolError(
                f"MCP JSON-RPC error {err.get('code')}: {err.get('message')}"
            )

        return body.get("result")


class MCPProtocolError(Exception):
    """Raised on MCP transport/protocol errors, not on query failures."""


# ── Convenience functions (thin wrappers over MCPSession.call) ─────────────────
# These are the only callsites for MCP tools in the orchestrator.
# Node code imports these; it does not call MCPSession.call directly.

async def schema_list_tables(session: MCPSession) -> dict[str, Any]:
    """A4.1 schema.list_tables"""
    return await session.call("schema.list_tables")


async def schema_describe_table(session: MCPSession, table: str) -> dict[str, Any]:
    """A4.2 schema.describe_table"""
    return await session.call("schema.describe_table", {"table": table})


async def data_sample_rows(
    session: MCPSession,
    table: str,
    columns: list[str] | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """A4.3 data.sample_rows"""
    params: dict[str, Any] = {"table": table}
    if columns is not None:
        params["columns"] = columns
    if limit is not None:
        params["limit"] = limit
    return await session.call("data.sample_rows", params)


async def data_run_select(
    session: MCPSession,
    sql: str,
    max_rows: int | None = None,
) -> dict[str, Any]:
    """
    A4.4 data.run_select
    Returns result dict with ok: bool. On ok=False, error.sqlstate is populated.
    Never raises on query failures — only raises on transport failures.
    """
    params: dict[str, Any] = {"sql": sql}
    if max_rows is not None:
        params["max_rows"] = max_rows
    return await session.call("data.run_select", params)
