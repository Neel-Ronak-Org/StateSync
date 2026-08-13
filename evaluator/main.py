"""
evaluator/main.py
Evaluator service stub — Phase B.

Full implementation is Phase F (continuous evaluation pipeline).
This stub:
  - Starts and stays healthy (B-V1 all-six-services check).
  - Exposes /health.
  - Reads config to prove the config loader works from this service too.
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

# The evaluator needs access to the config loader.
# In Docker, the orchestrator source is not shared — evaluator has its own copy.
# For Phase B stub we just check the config directory exists.
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    config_dir = os.environ.get("CONFIG_DIR", "/app/config")
    if not os.path.isdir(config_dir):
        logger.warning(
            "Config directory not found at %s. "
            "Evaluator running without config (Phase B stub).",
            config_dir,
        )
    else:
        logger.info("Evaluator stub started. Config dir: %s.", config_dir)
    yield
    logger.info("Evaluator stub shutdown.")


app = FastAPI(
    title="StateSync Evaluator",
    description="Continuous evaluation pipeline stub (full impl: Phase F).",
    version="0.1.0-phase-b-stub",
    lifespan=lifespan,
)


@app.get("/health", tags=["ops"])
async def health() -> dict:
    return {
        "status": "ok",
        "service": "evaluator",
        "phase": "B",
        "note": "Stub — full implementation in Phase F.",
    }


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=os.environ.get("EVALUATOR_HOST", "0.0.0.0"),
        port=int(os.environ.get("EVALUATOR_PORT", "8095")),
        log_level="info",
    )
