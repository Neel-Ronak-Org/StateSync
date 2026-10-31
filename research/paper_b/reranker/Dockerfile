FROM python:3.12.14-slim

# The passage re-ranker (reranker/server.py, docs/specs/rag_v3.md): CPU PyTorch, transformers,
# and two cross-encoders baked in at pinned revisions (owner-approved 2026-10-02). The running
# container has no internet: everything it loads is in this image.
# Exact versions of everything in requirements.lock (frozen from the running image, 2026-10-02).
COPY requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir -r /tmp/requirements.lock \
 && useradd --system --uid 10002 --no-create-home reranker

# BAAI/bge-reranker-v2-m3 (Apache-2.0) and cross-encoder/ms-marco-MiniLM-L-6-v2 (Apache-2.0).
RUN python -c "\
from huggingface_hub import snapshot_download as d; \
d('BAAI/bge-reranker-v2-m3', revision='953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e', local_dir='/models/bge-reranker-v2-m3', \
  allow_patterns=['config.json','model.safetensors','sentencepiece.bpe.model','special_tokens_map.json','tokenizer.json','tokenizer_config.json']); \
d('cross-encoder/ms-marco-MiniLM-L-6-v2', revision='233902d25c440f23af6f7d6e94d2946bac0bee0a', local_dir='/models/ms-marco-MiniLM-L-6-v2', \
  allow_patterns=['config.json','model.safetensors','special_tokens_map.json','tokenizer.json','tokenizer_config.json','vocab.txt'])" \
 && rm -rf /root/.cache

WORKDIR /app
COPY server.py ./

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV HF_HUB_OFFLINE=1
ENV TRANSFORMERS_OFFLINE=1

USER reranker
EXPOSE 8096

HEALTHCHECK --interval=15s --timeout=5s --start-period=120s --retries=5 \
  CMD python -c "import json, urllib.request; assert json.load(urllib.request.urlopen('http://localhost:8096/health'))['ok']" || exit 1

CMD ["python", "server.py"]
