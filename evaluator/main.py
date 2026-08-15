"""
evaluator/main.py
Phase F Deliverable 1/2 — the continuous evaluation pipeline.

Runs the Scorer registry (A8: SpecScorer, ExecutionScorer enabled; VisionScorer
registered and flag-disabled per D11/F-V5) over two golden fixtures whose
shape matches each enabled scorer's own need:
  - fixtures/golden/phase_c2.json (ground_truth_sql)  -> ExecutionScorer
  - fixtures/golden/phase_e2.json (reference_spec)    -> SpecScorer
Fixture choice is config (evaluation.spec_scorer_fixture /
execution_scorer_fixture), not a literal -- see GAP-8, docs/spec-gaps.md,
for why "the full golden set" needed a definition F1 Scope itself doesn't give.

Every query is submitted to the LIVE orchestrator (POST /query) -- the
production BD1 seam, whichever provider is configured -- never a direct
LLM call from this service. This module makes no model call of its own.

Runs on a schedule (evaluation.golden_run_schedule_cron) and on demand
(POST /run), persisting each run via history_store.py so regressions are
visible as a trend (Deliverable 2), not a single verdict.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, HTTPException

import history_store
from config_loader import cfg, flags
from mcp_client import MCPSession, data_run_select
from scorers import get_scorer

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

DB_PATH = Path(os.environ.get("EVAL_DB_PATH", "/app/data/eval_history.db"))
ORCH_URL = os.environ.get("ORCHESTRATOR_URL", "http://orchestrator:8090")
FIXTURES_DIR = Path(os.environ.get("FIXTURES_DIR", "/app/fixtures"))
TENANT_ID = str(cfg.tenancy.default_tenant)

_run_lock = asyncio.Lock()


async def _submit_query(client: httpx.AsyncClient, text: str) -> dict[str, Any]:
    resp = await client.post(f"{ORCH_URL}/query", json={"query": text})
    resp.raise_for_status()
    return resp.json()


async def _score_execution(client: httpx.AsyncClient) -> dict[str, Any]:
    fixture_path = FIXTURES_DIR / "golden" / str(cfg.evaluation.execution_scorer_fixture)
    queries = json.loads(fixture_path.read_text())["queries"]
    scorer = get_scorer("execution")

    per_query = []
    async with MCPSession(tenant_id=TENANT_ID) as session:
        for q in queries:
            try:
                generated = await _submit_query(client, q["text"])
                gen_rows = (generated.get("state_trace", {}) or {}).get("sql_rows", [])

                ref_result = await data_run_select(session, q["ground_truth_sql"])
                ref_rows = ref_result.get("rows", []) if ref_result.get("ok") else []

                result = scorer.score(gen_rows, ref_rows)
                per_query.append({"id": q["id"], **result})
            except Exception as exc:
                logger.warning("ExecutionScorer failed for %s: %s", q["id"], exc)
                per_query.append({"id": q["id"], "score": 0.0, "error": str(exc)})

    scores = [p["score"] for p in per_query]
    mean = sum(scores) / len(scores) if scores else 0.0
    return {"mean": mean, "n": len(scores), "per_query": per_query}


async def _score_spec(client: httpx.AsyncClient) -> dict[str, Any]:
    fixture_path = FIXTURES_DIR / "golden" / str(cfg.evaluation.spec_scorer_fixture)
    queries = json.loads(fixture_path.read_text())["queries"]
    scorer = get_scorer("spec")

    per_query = []
    null_count = 0
    for q in queries:
        try:
            generated = await _submit_query(client, q["text"])
            chart = generated.get("chart")
            if chart is None:
                null_count += 1
            result = scorer.score(chart, q["reference_spec"])
            per_query.append({"id": q["id"], **result})
        except Exception as exc:
            logger.warning("SpecScorer failed for %s: %s", q["id"], exc)
            per_query.append({"id": q["id"], "score": 0.0, "error": str(exc)})
            null_count += 1

    scores = [p["score"] for p in per_query]
    mean = sum(scores) / len(scores) if scores else 0.0
    return {"mean": mean, "null_count": null_count, "n": len(scores), "per_query": per_query}


async def _maybe_run_vision(spec_result: dict[str, Any]) -> bool:
    """
    F-V5: with the flag on, the pipeline must actually ATTEMPT the vision
    scorer (not just skip it because content is unbuilt) -- get_scorer
    resolves fine (registration is real), .score() raises NotImplementedError
    (content is real Strategy §6 stretch scope, correctly not built). The
    failure is logged and does not fail the run.
    """
    if not bool(flags.evaluation.vision_scorer.enabled):
        return False
    vision = get_scorer("vision")
    sample = next((p for p in spec_result["per_query"] if not p.get("error")), None)
    try:
        vision.score(sample, sample)
        logger.error("VisionScorer.score() returned without raising — content was expected to be unbuilt (D11).")
    except NotImplementedError as exc:
        logger.info("VisionScorer attempted per evaluation.vision_scorer.enabled=true, failed as expected (D11): %s", exc)
    return True


async def run_evaluation(trigger: str) -> dict[str, Any]:
    async with _run_lock:
        run_id = str(uuid.uuid4())
        started = datetime.now(timezone.utc).isoformat()
        logger.info("Evaluation run %s starting (trigger=%s)...", run_id, trigger)

        async with httpx.AsyncClient(timeout=httpx.Timeout(cfg.mcp.statement_timeout_ms / 1000 + 60)) as client:  # noqa: literals — ms conversion + generous full-query budget
            execution_result = await _score_execution(client)
            spec_result = await _score_spec(client)
            vision_attempted = await _maybe_run_vision(spec_result)

        finished = datetime.now(timezone.utc).isoformat()
        run = {
            "run_id": run_id, "started_at": started, "finished_at": finished, "trigger": trigger,
            "spec_mean": spec_result["mean"], "spec_null_count": spec_result["null_count"], "spec_n": spec_result["n"],
            "execution_mean": execution_result["mean"], "execution_n": execution_result["n"],
            "vision_attempted": vision_attempted,
            "detail": {"execution": execution_result, "spec": spec_result},
        }
        history_store.record_run(DB_PATH, run)
        history_store.prune_history(DB_PATH, int(cfg.evaluation.history_retention_runs))

        logger.info(
            "Evaluation run %s complete: execution_mean=%.3f (n=%d) spec_mean=%.3f null=%d (n=%d)",
            run_id, execution_result["mean"], execution_result["n"],
            spec_result["mean"], spec_result["null_count"], spec_result["n"],
        )
        return run


_scheduler: AsyncIOScheduler | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler
    history_store.init_db(DB_PATH)

    cron = str(cfg.evaluation.golden_run_schedule_cron)
    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(
        lambda: asyncio.create_task(run_evaluation("scheduled")),
        CronTrigger.from_crontab(cron),
        id="golden_run",
    )
    _scheduler.start()
    logger.info("Evaluator ready. Scheduled golden run: '%s'. DB: %s", cron, DB_PATH)
    yield
    _scheduler.shutdown(wait=False)
    logger.info("Evaluator shutdown.")


app = FastAPI(
    title="StateSync Evaluator",
    description="Continuous evaluation pipeline (Phase F).",
    version="1.0.0-phase-f",
    lifespan=lifespan,
)


@app.get("/health", tags=["ops"])
async def health() -> dict:
    return {
        "status": "ok", "service": "evaluator", "phase": "F",
        "scheduled_cron": str(cfg.evaluation.golden_run_schedule_cron),
        "vision_scorer_enabled": bool(flags.evaluation.vision_scorer.enabled),
    }


@app.post("/run", tags=["evaluation"])
async def trigger_run() -> dict:
    if _run_lock.locked():
        raise HTTPException(status_code=409, detail="An evaluation run is already in progress.")
    return await run_evaluation("on_demand")


@app.get("/history", tags=["evaluation"])
async def get_history(limit: int = int(cfg.evaluation.history_default_limit)) -> dict:
    return {"runs": history_store.get_history(DB_PATH, limit)}


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=os.environ.get("EVALUATOR_HOST", "0.0.0.0"),
        port=int(os.environ.get("EVALUATOR_PORT", "8095")),
        log_level="info",
    )
