"""
orchestrator/main.py
FastAPI application exposing the query endpoint.

Endpoints:
  POST /query   — Run a query through the graph; returns answer_text + state_trace.
  GET  /health  — Health check for Docker / B-V1.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from auth import (AuthError, Principal, RateLimited, RateLimiter, authenticate, init_auth,
                  rate_limiter, read_rate_limiter)
from config_loader import cfg, flags, snapshot_flags
from graph import graph
from llm.provider import LLM_READINESS, validate_llm_config
from llm.recording import start_recording, stop_recording
from failures import user_facing_failure
from evidence import evidence_tier
from sql_explain import explain_sql
from mcp_client import (MCPSession, audit_record, history_append_turn,
                        history_check_session, history_get_recent_turns,
                        history_get_turn_rows, history_list_sessions, history_delete_session)
from nodes.synthesizer import build_citations
from schema_retriever import target_schema
from schemas.synthesizer_output import safe_synthesizer_output
from state import StateSyncState
from tiles_api import register_tile_routes
from builder_api import register_build_routes
from dashboards_api import register_dashboard_routes
from library_api import register_library_routes
from workspaces_api import call_workspaces, register_workspace_routes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    provider: str = str(flags.llm.provider)
    logger.info(
        "StateSync Orchestrator starting up. LLM provider: %s.",
        provider,
    )
    # Validate LLM config eagerly — fails loudly here rather than mid-query.
    # For provider='local' this does NOT check the Anthropic key.
    # For provider='anthropic' this validates key presence and all role→model
    # mappings before accepting traffic.
    validate_llm_config()
    # Eagerly load and validate API principals — fails closed at startup if the
    # secret file is missing, empty, or malformed, preventing unauthenticated deploys.
    init_auth()
    yield
    logger.info("StateSync Orchestrator shutting down.")


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="StateSync Orchestrator",
    description="Multi-agent analytics API for ONDC network data.",
    version="0.1.0-phase-b",
    lifespan=lifespan,
)

# Wildcard origins are incompatible with credentialed requests and unsafe for
# authenticated APIs. Explicitly allow only trusted origins from config.
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(cfg.api.cors_allowed_origins),
    allow_credentials=False,
    # PATCH and DELETE for the /tiles routes (rename, unpin); PUT for the
    # /workspaces knowledge routes (Phase 3). Without them the browser
    # preflight refuses those calls from the web UI's origin, which no
    # server-side test can see (found auditing Phase 1).
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Filename"],
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
        principal = authenticate(request.headers.get("Authorization"))
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
    session_id: str | None = None
    # None means "use the configured default", which keeps existing callers
    # and every measurement script byte-identical in behaviour.
    schema: str | None = None
    # A workspace of the caller's (docs/V3_PLAN.md Phase 3): the question is
    # answered on its dataset with its approved knowledge.
    workspace_id: str | None = None
    # Test harness only (C2-V1/V2): seeds the attempt-0 candidate SQL,
    # bypassing LLM generation. Never sent by the frontend.
    debug_candidate_sql: str | None = None


class QueryResponse(BaseModel):
    query_id: str
    answer_text: str
    chart: dict | None
    citations: list[dict]
    state_trace: dict   # subset of state for B-V2 log check
    latency_ms: int


# ── Endpoints ─────────────────────────────────────────────────────────────────
@app.get("/health", tags=["ops"])
async def health() -> dict:
    return {
        "status": "ok",
        "service": "orchestrator",
        "phase": "B",
        "llm_provider": str(flags.llm.provider),
        # From startup's check, never a live model call (docs/V3_PLAN.md 0.4).
        "llm_ready": LLM_READINESS["ready"],
        "llm_detail": LLM_READINESS["detail"],
        "default_schema": target_schema(),
        "available_schemas": sorted(cfg.api.allowed_schemas),
        # Datasets whose data keeps changing (Phase 2b): the web app polls their
        # dashboards and starts a new browser on the first one.
        "live_schemas": [s for s in cfg.dashboards.live_schemas if s in cfg.api.allowed_schemas],
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


async def answer_question(
    principal: Principal,
    query: str,
    session_id: str,
    requested: str,
    client_ip: str | None,
    route_path: str = "/query",
    debug_candidate_sql: str | None = None,
    workspace_id: str | None = None,
    allow_fast_path: bool = True,
) -> QueryResponse:
    """
    Run one question through the pipeline and record the turn: the body of
    /query after its request checks (schema allowlist, session ownership,
    dataset match). Also used by the dashboard builder (dashboard_builder.py),
    which asks its planned questions through exactly this path, so a tile it
    creates came from the same SQL gate and validators as any answer.
    Raises HTTPException with a user-facing message when the graph fails.
    """
    tenant_id = principal.tenant_id
    query_id = str(uuid.uuid4())

    # Build the initial state input for the entry node
    initial_input = {
        "query_id": query_id,   # A2: set once here, immutable from here on
        "tenant_id": tenant_id,
        "principal_id": principal.principal_id,
        "session_id": session_id,
        "raw_query": query,
        "target_schema": requested,
        "flags_snapshot": snapshot_flags(),
    }

    t0 = time.monotonic()
    model_calls = start_recording()
    try:
        raw_final = await graph.ainvoke(
            StateSyncState(
                query_id=initial_input["query_id"],
                tenant_id=initial_input["tenant_id"],
                principal_id=principal.principal_id,
                session_id=initial_input["session_id"],
                raw_query=initial_input["raw_query"],
                target_schema=requested,
                workspace_id=workspace_id,
                allow_fast_path=allow_fast_path,
                flags_snapshot=initial_input["flags_snapshot"],
                debug_candidate_sql=debug_candidate_sql,
            )
        )
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

    # Build state trace for B-V2 log verification and C1 memory verification.
    # sql_rows/sql_columns are included (not just the count) so C1-V3 can do
    # execution-based dataframe equality across repeated runs -- this trace
    # object is verification/debugging-oriented, not the user-facing payload.
    state_trace = {
        "query_id":    final_state.query_id,
        "tenant_id":   final_state.tenant_id,
        "principal_id": final_state.principal_id,
        "target_schema": final_state.target_schema,
        "workspace_id": final_state.workspace_id,
        "verified_example": final_state.verified_example,
        "evidence": evidence_tier(final_state.verified_example, final_state.executed_sql,
                                  final_state.flags_snapshot, final_state.caveats,
                                  final_state.compiled_template),
        "route":       final_state.route,
        "executed_sql": final_state.executed_sql,
        # executed_sql is null when a statement was rejected before execution,
        # so candidate_sql is what a caller inspects to see what the agent actually proposed.
        "candidate_sql": final_state.candidate_sql,
        # Plain-English reading of the SQL (deterministic, no model): "How I got this".
        "explanation": explain_sql(final_state.executed_sql),
        "sql_rows_count": len(final_state.sql_rows),
        "sql_rows":    final_state.sql_rows,
        "sql_columns": final_state.sql_columns,
        "attempt":     final_state.attempt,
        "errors":      [e.model_dump() for e in final_state.errors],
        "interrogation_samples": final_state.interrogation_samples,
        "script_repairs": final_state.script_repairs,
        "caveats":     final_state.caveats,
        "metrics":     final_state.metrics.model_dump(),
        "memory_hit":       final_state.memory_hit,
        "memory_trace_id":  final_state.memory_trace_id,
        "schema_fingerprint": final_state.schema_fingerprint,
        "detected_language":  final_state.detected_language,
        "normalized_query":   final_state.normalized_query,
        "preserved_entities": final_state.preserved_entities,
        "turn_index":         final_state.turn_index,
        "rewrite_applied":    final_state.rewrite_applied,
        "context_guessed":    final_state.context_guessed,
        "spatial":            final_state.spatial.model_dump(),
        "subtasks":           final_state.subtasks,
        "retrievals":         [r.model_dump() for r in final_state.retrievals],
        "calls":              model_calls,
        "synth_style":        final_state.flags_snapshot.get("pipeline.synth_style", "full"),
    }

    # Recomputed here from final_state's typed fields, not read off the
    # synthesizer node's own return value: graph.ainvoke()'s return is
    # reconstructed against StateSyncState's schema (A2, locked -- no
    # citations field) regardless of whether the last hop was a direct or
    # conditional edge, so a scratch key set by synthesizer_node cannot
    # survive out to this boundary (verified empirically; same principle as
    # C2's scratch-key-through-conditional-edge fix, one level further out).
    # build_citations is pure and reused as-is, so this always matches what
    # synthesizer_node itself computed and validated the answer_text against.
    sql_error = final_state.errors[-1].model_dump() if len(final_state.errors) > final_state.attempt else None
    citations = build_citations(final_state.executed_sql, sql_error, state_trace["retrievals"])

    # A7 enforcement, validator 1 of 2: "in the orchestrator before the
    # response leaves the process". This must run HERE, on the exact object
    # about to be serialized -- not only inside synthesizer_node. The node
    # validates its own output, but citations are recomputed above (see the
    # comment on build_citations), so the payload the node validated is not
    # byte-for-byte the payload this endpoint emits. Validating only in the
    # node would leave the emitted response unchecked, which is precisely what
    # A7's "before the response leaves the process" wording rules out.
    #
    # safe_synthesizer_output never repairs: on failure it returns the same
    # prose and citations with chart set to null, and logs the failure (A7:
    # "silent repair would hide M7 regressions"). The frontend then validates
    # again, independently (E2-V6).
    validated = safe_synthesizer_output({
        "answer_text": final_state.answer_text or "",
        "chart": final_state.vega_spec,
        "citations": citations,
    })

    # Cycle 007 (Workstream A): record the turn AFTER validation, using the
    # exact answer_text the user is about to receive -- not final_state's
    # pre-repair value, for the same reason citations are recomputed here
    # rather than trusted from the node's own return (see the comment above
    # build_citations). Best-effort: a history-write failure must never turn
    # a correct answer into a 500. Logged loudly rather than silently
    # swallowed -- C2's own verification (row counts per session) would
    # surface a persistent failure here immediately, so this is not an
    # unobservable failure mode, just one that must not block the response.
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
                context_guessed=final_state.context_guessed,
                answer_text=validated["answer_text"],
                route=final_state.route,
                executed_sql=final_state.executed_sql,
                # Artifacts (migration 012), so a resumed conversation renders
                # what the user saw. The chart is the VALIDATED spec -- the same
                # object returned to the browser, already through both A7
                # validators -- never a pre-repair draft. Rows are sliced to the
                # preview cap HERE as well as clamped server-side: sending 1000
                # rows across the MCP boundary only to discard 900 of them is
                # waste, and the cap is a property of what gets stored, so both
                # ends enforce it.
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

    requested = body.schema or target_schema()
    if body.workspace_id:
        # The workspace decides the dataset; it must be the caller's own.
        try:
            workspace = (await call_workspaces(principal, "get", workspace_id=body.workspace_id)).get("workspace")
        except Exception as exc:
            logger.error("Workspace lookup failed for %s: %s", body.workspace_id, exc)
            raise HTTPException(status_code=503, detail="Workspace service unavailable. Try again shortly.")
        if workspace is None:
            raise HTTPException(status_code=404, detail="No such workspace.")
        if body.schema and body.schema != workspace["target_schema"]:
            raise HTTPException(status_code=409, detail=(
                f"This workspace uses the {workspace['target_schema']} dataset, not {body.schema}."))
        requested = workspace["target_schema"]
    if requested not in cfg.api.allowed_schemas:
        raise HTTPException(
            status_code=422,
            detail=f"Schema {requested!r} is not allowed. Allowed schemas: {sorted(cfg.api.allowed_schemas)}",
        )

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
        debug_candidate_sql=body.debug_candidate_sql,
        workspace_id=body.workspace_id,
    )


register_tile_routes(app, require_principal=require_principal, audit=_audit)
register_dashboard_routes(app, require_principal=require_principal, audit=_audit)
register_workspace_routes(app, require_principal=require_principal, audit=_audit,
                          require_principal_for_model=require_principal_for_model,
                          answer_question=answer_question)
register_library_routes(app, require_principal=require_principal, audit=_audit)
register_build_routes(app, require_principal=require_principal,
                      require_principal_for_model=require_principal_for_model,
                      audit=_audit, answer_question=answer_question)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=os.environ.get("ORCHESTRATOR_HOST", "0.0.0.0"),
        port=int(os.environ.get("ORCHESTRATOR_PORT", "8090")),
        log_level="info",
    )
