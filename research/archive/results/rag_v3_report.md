# RAG v3 report: cross-encoder re-ranking (docs/specs/rag_v3.md)

Pre-registered before any result: runs, primary comparison, `rerank_candidates` = 30, and the ship rule
("accuracy over latency: recommend a re-ranker if it improves page hit@5 by at least 5 points; latency is
reported but does not veto", owner, 2026-10-02).

## Setup

- **Corpus:** the 84 FinanceBench filings, paragraph chunker, all 84 ready (44,918 chunks;
  `docs/eval/financebench_ingest_paragraph.json`).
- **Questions:** all 150, verbatim (sha256 `a5a2aa67…`), uploads only, dense retrieval, top 10.
- **Stack:** as deployed 2026-10-03 (`verify_deployed` 17/19, the 2 failures being the evaluator, which is
  off by design). Shipped flags unchanged; the re-ranker is passed per search.
- **Deviation, recorded:** a first `fb3_dense` run on 2026-10-02 ran while the ingest was incomplete (33 of
  84 filings attempted, 3 failed: the embedding service timed out while other heavy work competed for
  the CPU). It is void, kept as `financebench_fb3_dense_VOID_incomplete_corpus.json`. The ingest was
  resumed (failed uploads removed first) and all three runs were repeated on the full corpus.

## Results (FinanceBench, 150 questions)

| run | page hit@1 | page hit@5 | page hit@10 | doc hit@1 | doc hit@5 | MRR@10 | p50 | p90 |
|---|---|---|---|---|---|---|---|---|
| `fb3_dense` (off) | 19.3% | 43.3% | 58.7% | 46.7% | 84.0% | 0.303 | 0.23 s | 0.36 s |
| `fb3_minilm` | 13.3% | 41.3% | 54.7% | 39.3% | 86.0% | 0.246 | 1.5 s | 1.9 s |
| `fb3_bge` | **32.0%** | **59.3%** | **69.3%** | **68.0%** | **95.3%** | **0.433** | 30.9 s | 37.3 s |

**Primary comparison (dense against bge, paired per question, exact McNemar):**
- page hit@5: bge alone hits 32 questions, dense alone 8; p = 0.0002;
- page hit@1: 26 against 7; p = 0.001;
- doc hit@5: 21 against 4; p = 0.0009.

**By question type (page hit@5, dense → bge):** metrics-generated 58% → 86%; domain-relevant 26% → 40%;
novel-generated 46% → 52%. The gain is largest where the evidence is a statement line item.

**MiniLM** is no better than dense: page hit@5 16 questions lost against 13 won (p = 0.71), and page hit@1
drops. A small cross-encoder trained on web search passages does not help on filings.

## Latency

Measured with the machine otherwise idle (Docker CPU, 30 candidates per search): MiniLM 1.5 s, **bge 31 s
per search** (p90 37 s). A documents answer runs two searches (the expansion round), so bge would add about
a minute to it. The ship rule says latency does not veto; it does decide how bge is offered.

## Recommendation

- **bge passes the pre-registered rule by a wide margin (+16.0 points page hit@5, p = 0.0002).**
  Recommend it for document search, under the owner's accuracy-over-latency decision.
- **MiniLM: do not ship.** No accuracy gain.
- **Before turning it on by default:** the minute of extra latency needs a decision on how it is offered:
  (a) on for every documents question; (b) a "thorough search" option; or (c) first a faster path for the
  same model (int8 quantisation, or the Mac GPU), measured with these same 150 questions. These are
  follow-ups, not part of this decision (spec).
- `[pipeline] rerank` stays `"off"` until the owner chooses; nothing was changed in the shipped flags.

## bge on the Mac GPU (owner's choice, 2026-10-03)

Docker on macOS cannot reach the GPU, so the same server code and the same model files (copied out of the
image, same pinned revisions; torch 2.14.1, transformers 5.18.0) run natively with `RERANKER_DEVICE=mps`
(`scripts/run_reranker_host.sh`, listening on 127.0.0.1 only; the MCP reaches it via
`host.docker.internal`).

| run | page hit@5 | MRR@10 | p50 per search | p90 |
|---|---|---|---|---|
| `fb3_bge` (Docker CPU) | 59.3% | 0.4327 | 30.9 s | 37.3 s |
| `fb3_bge_mps` (Mac GPU) | 59.3% | 0.4327 | **4.5 s** | **5.7 s** |

- **Identical results:** the gold page's rank is the same for all 150 questions.
- Re-scoring 30 passages takes 1.4 s on the GPU; the rest of the 4.5 s is the ordinary search path
  (query embedding, candidate fetch). Native CPU, outside Docker, takes 2.7 s for the same 30 passages:
  most of the 31 s was the Docker VM's CPU allowance, not the model.
- A documents answer runs two searches, so bge now adds roughly 9 s to it instead of about a minute.

## Not yet run

- `fb3_bge_rewrite` (query rewriting) needs Groq quota.
- The layout ingest and `fb3_layout_dense` / `fb3_layout_bge` (an 8-hour ingest, then the runs).

## In-house set (110 questions, 100 answerable; `retrieval_v2_v3_paragraph_{dense,bge}.json`)

The dense run reproduces the RAG v2 baseline exactly (hit@1 60.0%, hit@5 77.0%, hit@10 79.0%, MRR 0.670),
so the stack measured is the one the earlier report described.

| run | hit@1 | hit@5 | hit@10 | MRR@10 | ID hit@1 | ID hit@10 | p50 |
|---|---|---|---|---|---|---|---|
| dense | 60.0% | 77.0% | 79.0% | 0.670 | 13.3% | 30.0% | 0.16 s |
| bge | 67.0% | 78.0% | 83.0% | 0.721 | 23.3% | 43.3% | 12.0 s |

- Paired: hit@1 bge alone 11, dense alone 4 (p = 0.12); hit@5 5 against 4 (p = 1.0). The direction matches
  FinanceBench, but this set is too small to show it on its own.
- By category, hit@1: fact 81% → 100%, definition 71% → 100%, ID 13% → 23%, table 85% → 85%,
  paraphrase 60% → 50% (10 questions).
- **The rank-2 ID question** (`retrieval_v2_report.md`, "Two RRF ties"): those ties arise in hybrid mode,
  which these dense runs do not use. In dense mode bge lifts ID rank 1 from 13% to 23%, but ID hit@10 is
  only 43%: dense retrieval often does not return the ID document at all, and a re-ranker can only
  re-order the 30 candidates it is given. The natural next run is hybrid + bge (keyword recall, then
  re-ranking), which would also settle the ties. It is not part of this pre-registration and would be
  specified before it is run.
- Unanswerable questions: the first-stage top-1 cosine is unchanged by design (re-ranking happens after
  it), so abstention is not affected.
