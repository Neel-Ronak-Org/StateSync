# Spec: RAG v3, finishing document search (pre-registered 2026-10-02)

## Why

On real filings (FinanceBench, `docs/eval/financebench_*`), today's search finds the right
filing in the top 5 for 81% of questions, but the exact evidence page for only 42%
(`fb_paragraph_dense`). Keyword fusion made it worse (31%). The two standard remedies:

1. **A cross-encoder re-ranker.** It reads the question and each candidate passage together,
   and re-scores the top N from vector search.
2. **Query rewriting.** FinanceBench questions carry instructions ("Give a response by relying
   on the cash flow statement…") that dilute the embedding.

## Owner decisions (2026-10-02)

- **Re-rankers.** Download BAAI `bge-reranker-v2-m3` (568M, Apache-2.0) and
  `cross-encoder/ms-marco-MiniLM-L-6-v2` (22M, Apache-2.0), at pinned revisions.
- **Upload limit.** `[library] max_pages` goes from 300 to 600, so all 84 filings are ingested.
  Each of the six long ones reads in under 8 s and holds under `max_chars`.

## Design

- **A new `reranker` service.** It is internal-only, like `parser`: no internet at run time,
  models baked into the image at build, CPU only, and reachable only from `mcp`.
  - `POST /rerank {model, query, passages: [str]}` returns `{scores: [float]}`.
  - Passages are truncated to the model's window.
- **The MCP.** `vector.semantic_search` gains `rerank: "off" | "minilm" | "bge"`.
  - When rerank is on, it fetches the top `[retrieval] rerank_candidates` (30) by the
    configured mode, re-scores them, and returns the top `top_k` in re-ranker order.
  - Each match carries `rerank_score`.
  - The two-round expansion in the orchestrator passes the same value on both rounds.
  - If the service fails or times out, search falls back to the un-reranked order and the
    fallback is logged. A re-ranker failure never fails a search.
- **The flag.** `[pipeline] rerank = "off"` (shipped), registered in `scripts/shipped_flags.py`.
- **Query rewriting.** `[pipeline] query_rewrite = "off" | "llm"`.
  - With `llm`, the docs route first asks the normaliser model (qwen3.8-27b) for a short search
    query (prompt: rewrite as a search query; keep entities, years and metric names; drop
    instructions about answer format). Retrieval uses the rewrite; the synthesizer still sees
    the original question.
  - A general technique, not tuned to FinanceBench wording. No hand-written stripping rules.

## Runs (pre-registered; FinanceBench, 150 questions, uploads only, paragraph ingest kept)

| # | label | rerank | rewrite |
|---|---|---|---|
| 1 | `fb3_dense` | off | off |
| 2 | `fb3_minilm` | minilm | off |
| 3 | `fb3_bge` | bge | off |
| 4 | `fb3_bge_rewrite` | bge | llm |

Then the layout ingest: `fb3_layout_dense` and `fb3_layout_bge`. That gives the layout
comparison left open by the paused benchmark.

- **Primary comparison:** run 1 against run 3, on page hit@5 and MRR@10.
- **Secondary:** every other pair, plus p50/p90 latency. A re-ranker only ships if it improves
  page hit@5 by at least 5 points with p50 latency under 1.5 s per search.
- **Also reported:** the in-house 110-question set (`scripts/eval_retrieval.py`), dense against
  bge.
- **No tuning.** `rerank_candidates` = 30 and the rewrite prompt are fixed now. No other values
  are tried on these questions.
