"""
orchestrator/mcp_client.py
MCP client — sends JSON-RPC 2.0 calls to the mcp service.

Per A0.6: tenant_id is bound at session establishment and injected server-side.
It is part of every call's session context, never passed as a parameter.

Per A4: All tools are JSON-RPC 2.0 methods. A failed query returns ok: false
with a populated error field — it does not raise a transport-level fault.
"""

from __future__ import annotations

import functools
import logging
import os
import uuid
from pathlib import Path
from typing import Any

import httpx
from config_loader import cfg

logger = logging.getLogger(__name__)

# ── Session ───────────────────────────────────────────────────────────────────
MCP_TOKEN_FILE_ENV = "MCP_TOKEN_FILE"
MCP_TOKEN_FILE_DEFAULT = "/run/secrets/mcp_token"


@functools.lru_cache(maxsize=1)
def service_token() -> str:
    """The shared secret the MCP requires on every /rpc call (mcp/service_auth.py). Read once
    from the Docker secret; a missing file raises here, at the first MCP call, rather than
    sending requests the MCP will refuse."""
    path = os.environ.get(MCP_TOKEN_FILE_ENV, MCP_TOKEN_FILE_DEFAULT)
    try:
        token = Path(path).read_text().strip()
    except OSError as exc:
        raise RuntimeError(f"No MCP service token at {path}; mount the mcp_token secret.") from exc
    if not token:
        raise RuntimeError(f"The MCP service token at {path} is empty.")
    return token


class MCPSession:
    """
    Represents a session context with the MCP service.
    tenant_id is bound at construction and carried in every request header.
    The server injects it server-side; agents never see it as a parameter.
    """

    def __init__(self, tenant_id: str, base_url: str | None = None,
                session_id: str | None = None, workspace_id: str | None = None,
                dataset: str | None = None) -> None:
        self.tenant_id = tenant_id
        # The dataset the question is about. data_run_select sends it, so the
        # MCP runs the SQL on the right database: ours for a built-in schema,
        # the owner's for a connected one (mcp/connections.py, cn_... names).
        self.dataset = dataset
        # Phase 3: the workspace a question is asked in, carried as
        # X-Workspace-ID. The MCP checks it against tenant and principal; it
        # is routing information, not a credential.
        self.workspace_id = workspace_id
        # Cycle 007 (audit 007 F-007-1): session_id now flows in from the
        # caller -- the conversation's real StateSyncState.session_id -- so
        # X-Session-ID actually identifies the conversation, which is what
        # history_tools.py's session-scoped queries need. Minting a fresh
        # uuid4 remains the fallback for any caller that has no session of
        # its own to thread (verify_tenancy.py, one-off scripts).
        self.session_id = session_id or str(uuid.uuid4())
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
                "Authorization": f"Bearer {service_token()}",
                "X-Tenant-ID": tenant_id,
                "X-Session-ID": self.session_id,
                "Content-Type": "application/json",
                **({"X-Workspace-ID": workspace_id} if workspace_id else {}),
            },
        )

    async def __aenter__(self) -> "MCPSession":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self._client.aclose()

    async def call(self, method: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> Any:
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

        if timeout is None:
            response = await self._client.post("/rpc", json=payload)
        else:
            response = await self._client.post(
                "/rpc", json=payload, timeout=httpx.Timeout(timeout, connect=cfg.mcp.connect_timeout_s),
            )
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

async def schema_list_tables(session: MCPSession, schema: str | None = None) -> dict[str, Any]:
    """A4.1 schema.list_tables. `schema` defaults server-side to public (A5)."""
    return await session.call("schema.list_tables", {"schema": schema} if schema else {})


async def schema_describe_table(session: MCPSession, table: str,
                                schema: str | None = None) -> dict[str, Any]:
    """A4.2 schema.describe_table. `schema` defaults server-side to public (A5)."""
    params: dict[str, Any] = {"table": table}
    if schema:
        params["schema"] = schema
    return await session.call("schema.describe_table", params)


async def data_sample_rows(
    session: MCPSession,
    table: str,
    columns: list[str] | None = None,
    limit: int | None = None,
    schema: str | None = None,
) -> dict[str, Any]:
    """A4.3 data.sample_rows. `schema` defaults server-side to public (A5)."""
    params: dict[str, Any] = {"table": table}
    if columns is not None:
        params["columns"] = columns
    if limit is not None:
        params["limit"] = limit
    if schema:
        params["schema"] = schema
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
    if getattr(session, "dataset", None):
        params["dataset"] = session.dataset
    return await session.call("data.run_select", params)


async def vector_semantic_search(
    session: MCPSession,
    query_text: str,
    top_k: int | None = None,
    document_ids: list[str] | None = None,
) -> dict[str, Any]:
    """vector.semantic_search: the passages of the session's workspace closest to query_text, optionally
    only inside some documents. The MCP decides which documents are searchable from the session."""
    params: dict[str, Any] = {"query_text": query_text}
    if top_k is not None:
        params["top_k"] = top_k
    if document_ids is not None:
        params["document_ids"] = document_ids
    return await session.call("vector.semantic_search", params)


# ── history.* (cycle 007, Workstream A) ─────────────────────────────────────
async def history_append_turn(
    session: MCPSession,
    session_id: str,
    principal_id: str,
    raw_query: str,
    rewritten_query: str,
    rewrite_applied: bool,
    context_guessed: bool,
    answer_text: str,
    route: str | None = None,
    executed_sql: str | None = None,
    chart: dict[str, Any] | None = None,
    sql_columns: list[str] | None = None,
    sql_rows_preview: list[dict[str, Any]] | None = None,
    sql_rows_count: int | None = None,
    sql_rows_full: list[dict[str, Any]] | None = None,
    target_schema: str | None = None,
) -> dict[str, Any]:
    """history.append_turn -- MCP assigns the turn number and returns it."""
    params: dict[str, Any] = {
        "session_id": session_id,
        "principal_id": principal_id,
        "raw_query": raw_query,
        "rewritten_query": rewritten_query,
        "rewrite_applied": rewrite_applied,
        "context_guessed": context_guessed,
        "answer_text": answer_text,
    }
    if route is not None:
        params["route"] = route
    if executed_sql is not None:
        params["executed_sql"] = executed_sql
    if chart is not None:
        params["chart"] = chart
    if sql_columns:
        params["sql_columns"] = sql_columns
    if sql_rows_preview:
        params["sql_rows_preview"] = sql_rows_preview
    if sql_rows_count is not None:
        params["sql_rows_count"] = sql_rows_count
    if sql_rows_full is not None:
        params["sql_rows_full"] = sql_rows_full
    if target_schema is not None:
        params["target_schema"] = target_schema
    return await session.call("history.append_turn", params)


async def history_get_recent_turns(
    session: MCPSession, session_id: str, principal_id: str, limit: int | None = None,
) -> dict[str, Any]:
    """history.get_recent_turns"""
    params: dict[str, Any] = {"session_id": session_id, "principal_id": principal_id}
    if limit is not None:
        params["limit"] = limit
    return await session.call("history.get_recent_turns", params)


async def history_check_session(
    session: MCPSession, session_id: str, principal_id: str,
) -> dict[str, Any]:
    """history.check_session -- {"exists": bool, "usable": bool} for this principal."""
    return await session.call(
        "history.check_session", {"session_id": session_id, "principal_id": principal_id},
    )


async def history_get_turn_rows(
    session: MCPSession, session_id: str, principal_id: str, turn_index: int,
) -> dict[str, Any]:
    """history.get_turn_rows"""
    params: dict[str, Any] = {
        "session_id": session_id,
        "principal_id": principal_id,
        "turn_index": turn_index,
    }
    return await session.call("history.get_turn_rows", params)


async def history_delete_session(
    session: MCPSession, session_id: str, principal_id: str,
) -> dict[str, Any]:
    """history.delete_session -- erase one of the principal's conversations (Phase 2b)."""
    return await session.call(
        "history.delete_session", {"session_id": session_id, "principal_id": principal_id},
    )


async def history_list_sessions(
    session: MCPSession, principal_id: str, limit: int | None = None,
) -> dict[str, Any]:
    """history.list_sessions"""
    params: dict[str, Any] = {"principal_id": principal_id}
    if limit is not None:
        params["limit"] = limit
    return await session.call("history.list_sessions", params)


# ── audit.* (cycle 008, Workstream C1) ──────────────────────────────────────
async def audit_record(
    session: MCPSession,
    principal_id: str,
    route: str,
    outcome: str,
    query_id: str | None = None,
    session_id: str | None = None,
    client_ip: str | None = None,
    detail: str | None = None,
) -> dict[str, Any]:
    """audit.record"""
    params: dict[str, Any] = {
        "principal_id": principal_id,
        "route": route,
        "outcome": outcome,
    }
    if query_id is not None:
        params["query_id"] = query_id
    if session_id is not None:
        params["session_id"] = session_id
    if client_ip is not None:
        params["client_ip"] = client_ip
    if detail is not None:
        params["detail"] = detail
    return await session.call("audit.record", params)


# ── tiles.* (Phase 1) ────────────────────────────────────────────────────────
async def tiles_manage(
    session: MCPSession,
    principal_id: str,
    action: str,
    **params: Any,
) -> dict[str, Any]:
    """Forward a dashboard tile management action to the MCP tiles.manage tool."""
    return await session.call(
        "tiles.manage",
        {"action": action, "principal_id": principal_id, **{k: v for k, v in params.items() if v is not None}},
    )


async def dashboards_manage(
    session: MCPSession,
    principal_id: str,
    action: str,
    **params: Any,
) -> dict[str, Any]:
    """Forward a dashboard action to the MCP dashboards.manage tool (Phase 2b)."""
    return await session.call(
        "dashboards.manage",
        {"action": action, "principal_id": principal_id, **{k: v for k, v in params.items() if v is not None}},
    )



async def workspaces_manage(
    session: MCPSession,
    principal_id: str,
    action: str,
    **params: Any,
) -> dict[str, Any]:
    """Forward a workspace action to the MCP workspaces.manage tool (Phase 3)."""
    return await session.call(
        "workspaces.manage",
        {"action": action, "principal_id": principal_id, **{k: v for k, v in params.items() if v is not None}},
    )


async def library_manage(
    session: MCPSession,
    principal_id: str,
    action: str,
    **params: Any,
) -> dict[str, Any]:
    """Forward a document-library action to the MCP library.manage tool (Phase 3c).

    add_chunks embeds a whole batch through Ollama before answering, and the MCP waits up to
    retrieval.embedding_timeout_s for that. The session's default timeout (statement timeout
    plus overhead, 20 s) is shorter, so a slow batch used to fail here while the MCP was still
    working. add_chunks therefore waits the embedding timeout plus the network overhead, so
    the MCP always answers first: with the chunks stored, or with "unavailable" and nothing
    stored (which library_ingest may retry)."""
    timeout = (float(cfg.retrieval.embedding_timeout_s) + float(cfg.mcp.network_overhead_s)
               if action == "add_chunks" else None)
    return await session.call(
        "library.manage",
        {"action": action, "principal_id": principal_id, **{k: v for k, v in params.items() if v is not None}},
        timeout=timeout,
    )
