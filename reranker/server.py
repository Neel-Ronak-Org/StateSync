"""
reranker/server.py
The passage re-ranker (docs/specs/rag_v3.md): re-scores a question against candidate
passages with a cross-encoder, which reads the two together, unlike the embedding search
that compares them only as separate vectors. Nothing else.

WHY ITS OWN SERVICE. A cross-encoder needs PyTorch and gigabytes of weights; the MCP stays
small, and a slow or crashed re-ranker can never take the search path down with it (the MCP
falls back to the un-reranked order). Like the parser, it
  - sits on an internal-only network (reranker_net) shared only with the MCP, so it reaches
    no database, no model service and no internet;
  - holds no credentials, runs as a non-root user on a read-only filesystem with every Linux
    capability dropped;
  - loads only the models baked into the image at build time, at pinned revisions.

API (HTTP, JSON):
  GET  /health   -> {"ok": true, "models": [...]}
  POST /rerank   {"model": "minilm" | "bge", "query": "...", "passages": ["...", ...]}
                 200 {"ok": true, "scores": [float, ...]}   (one per passage, higher = more relevant)
                 4xx {"ok": false, "error": "<code>", "message": "..."}
Limits come from [reranker] in config/statesync.toml (mounted read-only).
"""

from __future__ import annotations

import json
import logging
import os
import time
import tomllib
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from typing import Any

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("reranker")

_CONFIG_DIR = Path(os.environ.get("CONFIG_DIR", "/app/config"))
LIMITS: dict[str, Any] = tomllib.loads((_CONFIG_DIR / "statesync.toml").read_text())["reranker"]
MODEL_DIR = Path(os.environ.get("MODEL_DIR", "/models"))
MODELS = {"minilm": "ms-marco-MiniLM-L-6-v2", "bge": "bge-reranker-v2-m3"}

torch.set_num_threads(int(LIMITS["threads"]))
_loaded: dict[str, tuple[Any, Any]] = {}
_lock = Lock()  # one inference at a time: CPU-bound, and the MCP sends one request per search


def load_models() -> None:
    for name, folder in MODELS.items():
        path = MODEL_DIR / folder
        tok = AutoTokenizer.from_pretrained(path)
        model = AutoModelForSequenceClassification.from_pretrained(path).eval()
        _loaded[name] = (tok, model)
        logger.info("loaded %s from %s", name, path)


@torch.inference_mode()
def score(name: str, query: str, passages: list[str]) -> list[float]:
    tok, model = _loaded[name]
    scores: list[float] = []
    batch = int(LIMITS["batch"])
    for start in range(0, len(passages), batch):
        chunk = passages[start:start + batch]
        enc = tok([query] * len(chunk), chunk, padding=True, truncation="only_second",
                  max_length=int(LIMITS["max_tokens"]), return_tensors="pt")
        logits = model(**enc).logits
        scores.extend(logits.view(-1).float().tolist() if logits.shape[-1] == 1 else logits[:, -1].float().tolist())
    return scores


class Handler(BaseHTTPRequestHandler):
    server_version = "statesync-reranker"
    sys_version = ""

    def _send(self, status: HTTPStatus, body: dict[str, Any]) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _fail(self, status: HTTPStatus, code: str, message: str) -> None:
        self._send(status, {"ok": False, "error": code, "message": message})

    def log_message(self, fmt: str, *args: Any) -> None:  # no client data in logs
        logger.debug("%s %s", self.command, self.path)

    def do_GET(self) -> None:  # noqa: N802 -- http.server naming
        if self.path == "/health":
            self._send(HTTPStatus.OK, {"ok": bool(_loaded), "models": sorted(_loaded)})
        else:
            self._fail(HTTPStatus.NOT_FOUND, "not_found", "Unknown path.")

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/rerank":
            self._fail(HTTPStatus.NOT_FOUND, "not_found", "Unknown path.")
            return
        try:
            length = int(self.headers.get("Content-Length") or "")
        except ValueError:
            self._fail(HTTPStatus.LENGTH_REQUIRED, "invalid", "Content-Length is required.")
            return
        if length <= 0 or length > int(LIMITS["max_request_bytes"]):
            self._fail(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "too_large", "Request too large.")
            return
        try:
            body = json.loads(self.rfile.read(length))
        except ValueError:
            self._fail(HTTPStatus.BAD_REQUEST, "invalid", "Body must be JSON.")
            return
        name, query, passages = body.get("model"), body.get("query"), body.get("passages")
        if name not in _loaded:
            self._fail(HTTPStatus.BAD_REQUEST, "invalid", f"model must be one of {sorted(_loaded)}.")
            return
        if not isinstance(query, str) or not query.strip() or len(query) > int(LIMITS["max_query_chars"]):
            self._fail(HTTPStatus.BAD_REQUEST, "invalid", "query must be a non-empty string within the limit.")
            return
        if (not isinstance(passages, list) or not passages or len(passages) > int(LIMITS["max_passages"])
                or not all(isinstance(p, str) for p in passages)):
            self._fail(HTTPStatus.BAD_REQUEST, "invalid", f"passages must be 1 to {LIMITS['max_passages']} strings.")
            return
        start = time.perf_counter()
        with _lock:
            scores = score(name, query, passages)
        logger.info("reranked %d passages with %s in %.0f ms", len(passages), name, (time.perf_counter() - start) * 1000)
        self._send(HTTPStatus.OK, {"ok": True, "scores": scores})


def main() -> None:
    load_models()
    port = int(os.environ.get("RERANKER_PORT", "8096"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)  # noqa: S104 -- internal network only
    logger.info("reranker listening on %d", port)
    server.serve_forever()


if __name__ == "__main__":
    main()
