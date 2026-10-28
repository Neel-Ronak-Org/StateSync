"""
orchestrator/main.py
The orchestrator's web API (FastAPI).

  POST /query            ask a question in a workspace; runs the graph (graph.py) and returns the answer,
                         its citations, its chart and the trace the Pipeline Inspector shows
  GET  /health           for Docker and the web app: is the service up, are the models configured
  /sessions ...          the caller's own conversations
  /workspaces, /library, /tiles, /dashboards, /auth ...   registered from the *_api.py modules

Every route authenticates the caller (auth.py) and is rate limited; /query has the tighter budget because
it spends model tokens. Every request is written to the audit log through the MCP.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from contextlib import asynccontextmanager

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from auth import (CSRF_HEADER, SESSION_COOKIE, AuthError, Principal, RateLimited, RateLimiter,
                  authenticate, credential_header, init_auth, rate_limiter, read_rate_limiter)
from config_loader import cfg, snapshot_flags
from graph import graph
from llm.provider import LLM_READINESS, validate_llm_config
from llm.recording import start_recording, stop_recording
from failures import user_facing_failure
from evidence import evidence_tier
from number_grounding import applies as grounding_applies
from number_grounding import caveat as grounding_caveat
from number_grounding import ground
from sql_explain import explain_sql
from mcp_client import (MCPSession, audit_record, history_append_turn,
                        history_check_session, history_get_recent_turns,
                        history_get_turn_rows, history_list_sessions, history_delete_session)
from nodes.synthesizer import build_citations
from schemas.synthesizer_output import safe_synthesizer_output
from state import StateSyncState
from tiles_api import register_tile_routes
from dashboards_api import register_dashboard_routes
from library_api import call_library, is_connection_dataset, register_library_routes
from workspaces_api import call_workspaces, register_workspace_routes
from inspect_api import register_inspect_routes
from session_api import register_session_routes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("StateSync orchestrator starting.")
    # Both checks fail loudly here, before any traffic: the model settings and keys, and the sign-in
    # tokens (a missing or empty token file must never mean "no sign-in needed").
    validate_llm_config()
    init_auth()
    yield
    logger.info("StateSync orchestrator shutting down.")


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="StateSync Orchestrator",
    description="Answers business questions from a workspace's database and documents.",
    version="1.0.0",
    lifespan=lifespan,
)

# Wildcard origins are incompatible with credentialed requests and unsafe for
# authenticated APIs. Explicitly allow only trusted origins from config.
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(cfg.api.cors_allowed_origins),
    # Credentials: the web app's HttpOnly session cookie (auth.py, session_api.py). Safe only
    # because the origins are an explicit list, never a wildcard.
    allow_credentials=True,
    # PATCH and DELETE for the /tiles routes (rename, unpin); PUT for the
    # /workspaces knowledge routes (Phase 3). Without them the browser
    # preflight refuses those calls from the web UI's origin, which no
    # server-side test can see (found auditing Phase 1).
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Filename", CSRF_HEADER],
    # Browsers hide every non-safelisted response header from cross-origin
    # JavaScript unless it is exposed. Without this the web UI could never read
    # Retry-After on a 429 and could only say "try again shortly" (the old UI
    # had the same blind spot).
    expose_headers=["Retry-After"],
)


# ── Audit & Security Dependencies ─────────────────────────────────────────────
async def _audit(
    principal_id: str,
    tenant_id: str,
    route: str,
    outcome: str,
    query_id: str | None = None,
    session_id: str | None = None,
    client_ip: str | None = None,
    detail: str | None = None,
) -> None:
    """
    Record an identity audit event via MCP audit.record.

    Swallows all exceptions: an audit logging failure must never fail an
    otherwise successful user request or mask the underlying error.
    """
    try:
        async with MCPSession(
            tenant_id=tenant_id,
            base_url=os.environ.get("MCP_URL", "http://mcp:8080"),
        ) as session:
            await audit_record(
                session,
                principal_id=principal_id,
                route=route,
                outcome=outcome,
                query_id=query_id,
                session_id=session_id,
                client_ip=client_ip,
                detail=detail,
            )
    except Exception as exc:
        logger.error(
            "Audit record failed (principal=%s tenant=%s route=%s outcome=%s): %s",
            principal_id,
            tenant_id,
            route,
            outcome,
            exc,
            exc_info=True,
        )


async def require_principal(request: Request) -> Principal:
    """
    FastAPI dependency: authenticate the caller and charge the READ budget
    (every route that does not run the model; see auth.RateLimiter).
    """
    return await _authenticate_and_limit(request, read_rate_limiter)


async def require_principal_for_model(request: Request) -> Principal:
    """
    FastAPI dependency for routes that run the model (/query): authenticate the
    caller and charge the tight budget (api.rate_limit_max_requests), which
    bounds token spend and denial-of-wallet loops.
    """
    return await _authenticate_and_limit(request, rate_limiter)


async def _authenticate_and_limit(request: Request, limiter: RateLimiter) -> Principal:
    """
    Extracts bearer token from the Authorization header and verifies it against
    known principals. Enforces the given in-process fixed-window rate limit.
    Records audit events on auth or rate limit failures before raising
    corresponding HTTP exceptions.
    """
    client_ip = request.client.host if request.client else None
    route = request.url.path

    try:
        principal = authenticate(credential_header(request.headers.get("Authorization"),
                                                   request.cookies.get(SESSION_COOKIE), request.headers.get(CSRF_HEADER)))
    except AuthError as exc:
        await _audit(
            "anonymous",
            cfg.tenancy.default_tenant,
            route,
            "denied_auth",
            client_ip=client_ip,
            detail=str(exc),
        )
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        limiter.check(principal.principal_id)
    except RateLimited as exc:
        await _audit(
            principal.principal_id,
            principal.tenant_id,
            route,
            "denied_rate",
            client_ip=client_ip,
        )
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded.",
            headers={"Retry-After": str(exc.retry_after)},
        )

    return principal



# ── Request / Response models ─────────────────────────────────────────────────
class QueryRequest(BaseModel):
    query: str
    # The workspace the question is asked in: its database, its documents and its approved knowledge.
    workspace_id: str | None = None
    session_id: str | None = None


class QueryResponse(BaseModel):
    query_id: str
    answer_text: str
    chart: dict | None
    citations: list[dict]
    state_trace: dict   # what each step did, for the Pipeline Inspector and the "How I got this" panel
    latency_ms: int


# ── Endpoints ─────────────────────────────────────────────────────────────────
@app.get("/health", tags=["ops"])
async def health() -> dict:
    return {
        "status": "ok",
        "service": "orchestrator",
        # From startup's check, never a live model call.
        "llm_ready": LLM_READINESS["ready"],
        "llm_detail": LLM_READINESS["detail"],
    }


# ── Session browser ───────────────────────────────────────────────────────────
# Formerly gated for local development only: history was scoped by tenant_id +
# session_id alone, so any user of a shared tenant could enumerate every other
# user's questions and answers (and stored answers contain real rows). C1 added
# authentication and migration 014 added a NOT NULL principal_id to
# conversation_turns. Both routes now filter on the authenticated principal, so
# a caller can only enumerate and read their own conversations. Turns written
# before auth carry the sentinel principal 'p_preauth_legacy', which no real
# principal holds, so pre-auth history is deliberately invisible rather than
# shown to an arbitrary caller.


@app.get("/sessions", tags=["history"])
async def list_sessions_route(
    request: Request,
    limit: int = cfg.mcp.session_browser_list_limit,
    principal: Principal = Depends(require_principal),
) -> dict:
    """Past conversations for this tenant, most recently active first."""
    async with MCPSession(
        tenant_id=principal.tenant_id,
        base_url=os.environ.get("MCP_URL", "http://mcp:8080"),
    ) as session:
        result = await history_list_sessions(
            session, principal_id=principal.principal_id, limit=limit
        )
    client_ip = request.client.host if request.client else None
    await _audit(
        principal.principal_id,
        principal.tenant_id,
        "/sessions",
        "allowed",
        client_ip=client_ip,
    )
    return result


@app.get("/sessions/{session_id}", tags=["history"])
async def get_session_route(
    session_id: str,
    request: Request,
    limit: int = cfg.mcp.session_browser_turn_limit,
    principal: Principal = Depends(require_principal),
) -> dict:
    """Every stored turn of one past conversation, oldest first."""
    async with MCPSession(
        tenant_id=principal.tenant_id,
        base_url=os.environ.get("MCP_URL", "http://mcp:8080"),
    ) as session:
        result = await history_get_recent_turns(
            session, session_id, principal_id=principal.principal_id, limit=limit
        )
    client_ip = request.client.host if request.client else None
    await _audit(
        principal.principal_id,
        principal.tenant_id,
        f"/sessions/{session_id}",
        "allowed",
        session_id=session_id,
        client_ip=client_ip,
    )
    return result


@app.delete("/sessions/{session_id}", tags=["history"])
async def delete_session_route(
    session_id: str,
    request: Request,
    principal: Principal = Depends(require_principal),
) -> dict:
    """Erase one of my conversations (Phase 2b). Someone else's is a 404, like a missing one."""
    try:
        async with MCPSession(
            tenant_id=principal.tenant_id,
            base_url=os.environ.get("MCP_URL", "http://mcp:8080"),
        ) as session:
            result = await history_delete_session(
                session, session_id=session_id, principal_id=principal.principal_id
            )
    except Exception as exc:
        logger.error("Deleting session %s failed: %s", session_id, exc, exc_info=True)
        raise HTTPException(status_code=503, detail="History service unavailable. Try again shortly.") from None
    client_ip = request.client.host if request.client else None
    if not result.get("ok"):
        await _audit(principal.principal_id, principal.tenant_id, f"/sessions/{session_id}", "error",
                     session_id=session_id, client_ip=client_ip,
                     detail=f"history:delete {result.get('error')}")
        raise HTTPException(status_code=404 if result.get("error") == "not_found" else 422,
                            detail=result.get("message") or "Request failed.")
    await _audit(principal.principal_id, principal.tenant_id, f"/sessions/{session_id}", "allowed",
                 session_id=session_id, client_ip=client_ip,
                 detail=f"history:delete turns={result.get('deleted_turns')}")
    return {"deleted": session_id, "deleted_turns": result.get("deleted_turns")}


@app.get("/sessions/{session_id}/turns/{turn_index}/rows", tags=["history"])
async def get_turn_rows_route(
    session_id: str,
    turn_index: int,
    request: Request,
    principal: Principal = Depends(require_principal),
) -> dict:
    """Fetch stored full result set for a conversation turn."""
    async with MCPSession(
        tenant_id=principal.tenant_id,
        base_url=os.environ.get("MCP_URL", "http://mcp:8080"),
    ) as session:
        result = await history_get_turn_rows(
            session,
            session_id=session_id,
            principal_id=principal.principal_id,
            turn_index=turn_index,
        )
    client_ip = request.client.host if request.client else None
    # An absent payload and a full payload are both "allowed", so without this the audit row
    # cannot distinguish a caller who read a result set from one who was correctly scoped out of it.
    audit_detail = (
        f"available={'true' if result.get('available') else 'false'} "
        f"stored_row_count={result.get('stored_row_count', 0)}"
    )
    await _audit(
        principal.principal_id,
        principal.tenant_id,
        f"/sessions/{session_id}/turns/{turn_index}/rows",
        "allowed",
        session_id=session_id,
        client_ip=client_ip,
        detail=audit_detail,
    )
    # Return unchanged: available:false is a normal state (not found or pruned) that the
    # client must distinguish from an error, falling back to the turn's stored preview.
    return result


async def _library_titles(principal: Principal, workspace_id: str | None) -> list[str]:
    """Titles of the ready uploaded documents a workspace searches ([] without one, or on failure)."""
    if not workspace_id:
        return []
    try:
        result = await call_library(principal, "workspace_documents", workspace_id=workspace_id)
    except Exception as exc:
        logger.warning("Library titles unavailable for workspace %s: %s", workspace_id, exc)
        return []
    return [d["title"] for d in result.get("documents", [])] if result.get("ok") else []


async def answer_question(
    principal: Principal,
    query: str,
    session_id: str,
    requested: str,
    client_ip: str | None,
    route_path: str = "/query",
    workspace_id: str | None = None,
) -> QueryResponse:
    """Run one question through the pipeline and record the turn: the body of /query after its request
    checks (the workspace is the caller's, the conversation is the caller's and on the same database).
    Raises HTTPException with a user-facing message when the graph fails."""
    tenant_id = principal.tenant_id
    query_id = str(uuid.uuid4())

    t0 = time.monotonic()
    model_calls = start_recording()   # every model call of this question, for the trace's cost table
    try:
        raw_final = await graph.ainvoke(StateSyncState(
            query_id=query_id, tenant_id=tenant_id, principal_id=principal.principal_id, session_id=session_id,
            raw_query=query, target_schema=requested, workspace_id=workspace_id,
            library_titles=await _library_titles(principal, workspace_id), flags_snapshot=snapshot_flags(),
        ))
        final_state: StateSyncState = StateSyncState(**raw_final) if isinstance(raw_final, dict) else raw_final
    except Exception as exc:
        logger.error("Graph execution failed: %s", exc, exc_info=True)
        await _audit(
            principal.principal_id,
            principal.tenant_id,
            route_path,
            "error",
            query_id=query_id,
            session_id=session_id,
            client_ip=client_ip,
            detail=f"{exc} schema={requested}",
        )
        status_code, message = user_facing_failure(exc, query_id)
        raise HTTPException(status_code=status_code, detail=message)
    finally:
        stop_recording()

    latency_ms = int((time.monotonic() - t0) * 1000)  # noqa: literals — ms conversion factor

    # Every figure in the answer, traced to a cell, a total, the question or a cited passage
    # (number_grounding.py, no model call). A figure found nowhere becomes a caveat under the answer.
    grounding = None
    caveats = list(final_state.caveats)
    if final_state.answer_text and grounding_applies(final_state.route, final_state.executed_sql):
        grounding = ground(final_state.answer_text, final_state.sql_rows, final_state.sql_columns,
                           final_state.normalized_query or final_state.raw_query,
                           [r.content for r in final_state.retrievals])
        figure_caveat = grounding_caveat(grounding["unmatched"])
        if figure_caveat:
            caveats.append(figure_caveat)

    state_trace = {
        "query_id":    final_state.query_id,
        "tenant_id":   final_state.tenant_id,
        "principal_id": final_state.principal_id,
        "target_schema": final_state.target_schema,
        "workspace_id": final_state.workspace_id,
        "verified_example": final_state.verified_example,
        "evidence": evidence_tier(final_state.verified_example, final_state.executed_sql),
        "number_grounding": grounding,
        "route":       "neither" if final_state.out_of_scope else final_state.route,
        "recovered_from": final_state.recovered_from,   # "sql" when route recovery ran the documents route instead
        "executed_sql": final_state.executed_sql,
        # The last query the agent wrote, also when it was refused before running (the Inspector shows it).
        "candidate_sql": final_state.candidate_sql,
        # Plain-English reading of the SQL (deterministic, no model): "How I got this".
        "explanation": explain_sql(final_state.executed_sql),
        "sql_rows_count": len(final_state.sql_rows),
        "sql_rows":    final_state.sql_rows,
        "sql_columns": final_state.sql_columns,
        "attempt":     final_state.attempt,
        "errors":      [e.model_dump() for e in final_state.errors],
        "interrogation_samples": final_state.interrogation_samples,
        "caveats":     caveats,
        "metrics":     final_state.metrics.model_dump(),
        "normalized_query": final_state.normalized_query,
        "turn_index":       final_state.turn_index,
        "rewrite_applied":  final_state.rewrite_applied,
        "subtasks":         final_state.subtasks,
        "retrievals":       [r.model_dump() for r in final_state.retrievals],
        "calls":            model_calls,
        "front_door":       final_state.front_door,       # the router: what it read and decided
        "out_of_scope":     final_state.out_of_scope,
        "retrieval_trace":  final_state.retrieval_trace,  # the documents search, stage by stage
        "hybrid_entities":  final_state.hybrid_entities,  # the narrowing step of a two-source question
    }

    # The citations are rebuilt here from the final state (the same pure function the writer used), so
    # the response carries exactly what is validated below.
    sql_error = final_state.errors[-1].model_dump() if len(final_state.errors) > final_state.attempt else None
    citations = build_citations(final_state.executed_sql, sql_error, state_trace["retrievals"])

    # The response is checked against the output schema on the exact object about to be sent (the web app
    # checks it again before drawing). A chart that fails is dropped, never repaired.
    validated = safe_synthesizer_output({
        "answer_text": final_state.answer_text or "",
        "chart": final_state.vega_spec,
        "citations": citations,
    })

    # The turn is recorded after validation, with exactly the answer the user receives. Best-effort: a
    # failed history write is logged but never turns a correct answer into an error.
    try:
        async with MCPSession(
            tenant_id=final_state.tenant_id,
            base_url=os.environ.get("MCP_URL", "http://mcp:8080"),
            session_id=final_state.session_id,
        ) as history_session:
            write = await history_append_turn(
                history_session,
                session_id=final_state.session_id,
                principal_id=final_state.principal_id,
                raw_query=final_state.raw_query,
                rewritten_query=final_state.normalized_query or final_state.raw_query,
                rewrite_applied=final_state.rewrite_applied,
                context_guessed=False,
                answer_text=validated["answer_text"],
                route="neither" if final_state.out_of_scope else final_state.route,
                executed_sql=final_state.executed_sql,
                # What the user saw, so a reopened conversation shows the same answer, chart and rows.
                chart=validated["chart"],
                sql_columns=final_state.sql_columns or None,
                sql_rows_preview=(final_state.sql_rows or None) and
                    final_state.sql_rows[: cfg.mcp.history_row_preview_max],
                sql_rows_count=len(final_state.sql_rows),
                # Do NOT slice it — MCP applies the caps and logs truncation; slicing here would hide from those logs how large the real result was.
                sql_rows_full=(final_state.sql_rows or None),
                target_schema=final_state.target_schema,
            )
        # MCP assigns the turn number (0.1), so the trace reports the stored one --
        # the web client uses it to fetch this turn's full rows later. A turn that
        # was not recorded gets no number, which turns that button off.
        if write.get("written"):
            state_trace["turn_index"] = write.get("turn_index")
        else:
            state_trace["turn_index"] = None
            logger.error(
                "History write refused for query_id=%s session_id=%s: %s "
                "-- response still returned to the user.",
                final_state.query_id, final_state.session_id, write.get("error"),
            )
    except Exception as exc:
        state_trace["turn_index"] = None
        logger.error(
            "History write failed for query_id=%s session_id=%s: %s "
            "-- response still returned to the user unaffected.",
            final_state.query_id, final_state.session_id, exc,
        )

    logger.info(
        "Query completed: query_id=%s route=%s rows=%d latency_ms=%d chart=%s",
        final_state.query_id,
        final_state.route,
        len(final_state.sql_rows),
        latency_ms,
        "present" if validated["chart"] is not None else "null",
    )

    response = QueryResponse(
        query_id=final_state.query_id,
        answer_text=validated["answer_text"],
        chart=validated["chart"],
        citations=validated["citations"],
        state_trace=state_trace,
        latency_ms=latency_ms,
    )

    await _audit(
        principal.principal_id,
        principal.tenant_id,
        route_path,
        "allowed",
        query_id=final_state.query_id,
        session_id=session_id,
        client_ip=client_ip,
        detail=f"schema={final_state.target_schema}",
    )

    return response


@app.post("/query", response_model=QueryResponse, tags=["query"])
async def run_query(
    body: QueryRequest,
    request: Request,
    principal: Principal = Depends(require_principal_for_model),
) -> QueryResponse:
    if not body.query or not body.query.strip():
        raise HTTPException(status_code=422, detail="query must not be empty")
    if not body.workspace_id:
        raise HTTPException(status_code=422, detail="Choose a workspace to ask in.")
    # The workspace decides the database; it must be the caller's own.
    try:
        workspace = (await call_workspaces(principal, "get", workspace_id=body.workspace_id)).get("workspace")
    except Exception as exc:
        logger.error("Workspace lookup failed for %s: %s", body.workspace_id, exc)
        raise HTTPException(status_code=503, detail="Workspace service unavailable. Try again shortly.")
    if workspace is None:
        raise HTTPException(status_code=404, detail="No such workspace.")
    requested = workspace["target_schema"]
    if not is_connection_dataset(requested):
        raise HTTPException(status_code=409, detail="This workspace has no connected database.")

    session_id = body.session_id or str(uuid.uuid4())
    tenant_id = principal.tenant_id
    client_ip = request.client.host if request.client else None

    # A session belongs to the principal who wrote its first turn (docs/V3_PLAN.md
    # 0.2). The web client generates session ids, so one owned by someone else is
    # refused here, before any model call -- rather than answered and then not
    # recorded. history.append_turn enforces the same rule at the write.
    if body.session_id:
        try:
            async with MCPSession(
                tenant_id=tenant_id,
                base_url=os.environ.get("MCP_URL", "http://mcp:8080"),
                session_id=session_id,
            ) as check_session:
                access = await history_check_session(check_session, session_id, principal.principal_id)
        except Exception as exc:
            logger.error("Session ownership check failed for session_id=%s: %s", session_id, exc)
            raise HTTPException(status_code=503, detail="History service unavailable. Try again shortly.")
        if not access.get("usable", False):
            await _audit(
                principal.principal_id,
                tenant_id,
                "/query",
                "denied_session",
                session_id=session_id,
                client_ip=client_ip,
                detail="session belongs to another principal",
            )
            raise HTTPException(status_code=404, detail="Conversation not found. Start a new conversation.")
        # A conversation stays on the dataset it started on (migration 018). A
        # follow-up aimed elsewhere would be answered from the other corpus while
        # its context came from the first -- refuse it rather than mix them.
        started_on = access.get("target_schema")
        if started_on and started_on != requested:
            await _audit(
                principal.principal_id,
                tenant_id,
                "/query",
                "error",
                session_id=session_id,
                client_ip=client_ip,
                detail=f"dataset mismatch: conversation={started_on} requested={requested}",
            )
            raise HTTPException(
                status_code=409,
                detail=(f"This conversation uses the {started_on} dataset. "
                        f"Start a new conversation to ask about {requested}."),
            )

    return await answer_question(
        principal,
        body.query,
        session_id,
        requested,
        client_ip,
        workspace_id=body.workspace_id,
    )


register_session_routes(app, require_principal=require_principal, audit=_audit)
register_tile_routes(app, require_principal=require_principal, audit=_audit)
register_dashboard_routes(app, require_principal=require_principal, audit=_audit)
register_workspace_routes(app, require_principal=require_principal, audit=_audit,
                          require_principal_for_model=require_principal_for_model)
register_library_routes(app, require_principal=require_principal, audit=_audit)
register_inspect_routes(app, require_principal=require_principal, audit=_audit)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=os.environ.get("ORCHESTRATOR_HOST", "0.0.0.0"),
        port=int(os.environ.get("ORCHESTRATOR_PORT", "8090")),
        log_level="info",
    )
