"""
orchestrator/main.py
FastAPI application exposing the query endpoint.

Endpoints:
  POST /query   — Run a query through the graph; returns answer_text + state_trace.
  GET  /health  — Health check for Docker / B-V1.
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from config_loader import cfg, flags, snapshot_flags
from graph import graph
from llm.provider import validate_llm_config
from nodes.synthesizer import build_citations
from schemas.synthesizer_output import safe_synthesizer_output
from state import StateSyncState

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
        "StateSync Orchestrator starting up. "
        "LLM provider: %s. Tenant: %s.",
        provider,
        cfg.tenancy.default_tenant,
    )
    # Validate LLM config eagerly — fails loudly here rather than mid-query.
    # For provider='local' this does NOT check the Anthropic key.
    # For provider='anthropic' this validates key presence and all role→model
    # mappings before accepting traffic.
    validate_llm_config()
    yield
    logger.info("StateSync Orchestrator shutting down.")


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="StateSync Orchestrator",
    description="Multi-agent analytics API for ONDC network data.",
    version="0.1.0-phase-b",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # frontend is same-origin in prod; wildcard ok for dev
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ── Request / Response models ─────────────────────────────────────────────────
class QueryRequest(BaseModel):
    query: str
    session_id: str | None = None
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
    }


@app.post("/query", response_model=QueryResponse, tags=["query"])
async def run_query(request: QueryRequest) -> QueryResponse:
    if not request.query or not request.query.strip():
        raise HTTPException(status_code=422, detail="query must not be empty")

    session_id = request.session_id or str(uuid.uuid4())
    tenant_id = cfg.tenancy.default_tenant

    # Build the initial state input for the entry node
    initial_input = {
        "query_id": str(uuid.uuid4()),   # A2: set once here, immutable from here on
        "tenant_id": tenant_id,
        "session_id": session_id,
        "raw_query": request.query,
        "flags_snapshot": snapshot_flags(),
    }

    t0 = time.monotonic()
    try:
        raw_final = await graph.ainvoke(
            StateSyncState(
                query_id=initial_input["query_id"],
                tenant_id=initial_input["tenant_id"],
                session_id=initial_input["session_id"],
                raw_query=initial_input["raw_query"],
                flags_snapshot=initial_input["flags_snapshot"],
                debug_candidate_sql=request.debug_candidate_sql,
            )
        )
        final_state: StateSyncState = StateSyncState(**raw_final) if isinstance(raw_final, dict) else raw_final
    except Exception as exc:
        logger.error("Graph execution failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Graph execution error: {exc}")

    latency_ms = int((time.monotonic() - t0) * 1000)  # noqa: literals — ms conversion factor

    # Build state trace for B-V2 log verification and C1 memory verification.
    # sql_rows/sql_columns are included (not just the count) so C1-V3 can do
    # execution-based dataframe equality across repeated runs -- this trace
    # object is verification/debugging-oriented, not the user-facing payload.
    state_trace = {
        "query_id":    final_state.query_id,
        "tenant_id":   final_state.tenant_id,
        "route":       final_state.route,
        "executed_sql": final_state.executed_sql,
        "sql_rows_count": len(final_state.sql_rows),
        "sql_rows":    final_state.sql_rows,
        "sql_columns": final_state.sql_columns,
        "attempt":     final_state.attempt,
        "errors":      [e.model_dump() for e in final_state.errors],
        "interrogation_samples": final_state.interrogation_samples,
        "metrics":     final_state.metrics.model_dump(),
        "memory_hit":       final_state.memory_hit,
        "memory_trace_id":  final_state.memory_trace_id,
        "schema_fingerprint": final_state.schema_fingerprint,
        "detected_language":  final_state.detected_language,
        "normalized_query":   final_state.normalized_query,
        "preserved_entities": final_state.preserved_entities,
        "spatial":            final_state.spatial.model_dump(),
        "subtasks":           final_state.subtasks,
        "retrievals":         [r.model_dump() for r in final_state.retrievals],
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

    logger.info(
        "Query completed: query_id=%s route=%s rows=%d latency_ms=%d chart=%s",
        final_state.query_id,
        final_state.route,
        len(final_state.sql_rows),
        latency_ms,
        "present" if validated["chart"] is not None else "null",
    )

    return QueryResponse(
        query_id=final_state.query_id,
        answer_text=validated["answer_text"],
        chart=validated["chart"],
        citations=validated["citations"],
        state_trace=state_trace,
        latency_ms=latency_ms,
    )


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import os
    uvicorn.run(
        "main:app",
        host=os.environ.get("ORCHESTRATOR_HOST", "0.0.0.0"),
        port=int(os.environ.get("ORCHESTRATOR_PORT", "8090")),
        log_level="info",
    )
