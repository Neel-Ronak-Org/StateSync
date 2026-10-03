#!/usr/bin/env bash
# scripts/run_reranker_host.sh
# Run the passage re-ranker (reranker/server.py) natively on the Mac GPU (owner's choice,
# 2026-10-03): Docker on macOS cannot reach the GPU. Same server code, same model files at the
# same pinned revisions (copied out of the reranker image into data/external/reranker_models),
# torch 2.14.1 / transformers 5.18.0 as in the image. Listens on 127.0.0.1 only; the MCP reaches
# it through host.docker.internal (docker-compose.override.yml sets RERANKER_URL).
#
#   ./scripts/run_reranker_host.sh            (foreground; Ctrl-C to stop)
# One-time setup:
#   uv venv --python 3.12 .venv-reranker
#   uv pip install --python .venv-reranker/bin/python torch==2.14.1 transformers==5.18.0 \
#       safetensors==0.8.0 sentencepiece==0.2.2 huggingface_hub==1.33.0
#   docker cp statesync-reranker-1:/models/. data/external/reranker_models/
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export CONFIG_DIR="${REPO_ROOT}/config"
export MODEL_DIR="${REPO_ROOT}/data/external/reranker_models"
export RERANKER_DEVICE="${RERANKER_DEVICE:-mps}"
export RERANKER_HOST=127.0.0.1
export RERANKER_PORT="${RERANKER_PORT:-8096}"
exec "${REPO_ROOT}/.venv-reranker/bin/python" "${REPO_ROOT}/reranker/server.py"
