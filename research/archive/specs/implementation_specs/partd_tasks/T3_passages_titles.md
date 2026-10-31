# T3: the writer reads the most relevant passages; titles for the re-ranker and the writer

Parent spec: `docs/specs/routing_docs_fixes.md` (flaws D1–D3; fixes F-D, F-T). Base: the head of branch `v3-phase3-workspaces` when you start (it contains this file, and `abf7e15`).
You implement; the lead reviews by running the checks and probing past them.

## Why

The documents route returns up to 25 passages. The answer writer reads only the first
`retrieval.semantic_top_k` (8). Those 8 are ordered by document and then by position in the document
(`_semantic_search_expanded`), not by relevance.

On FinanceBench, a gold page was among the 25 for 60 of 82 answers but among the writer's 8 for only 31.
The re-sort also throws away the re-ranker's order.

Neither the re-ranker nor the writer sees which filing a passage comes from. The writer gets
`document_id`, not the title, so it said "the documents only cover FY2015".

## Flags

Add each flag to `config/flags.toml` under `[pipeline]`, next to the existing flags. The defaults keep
the shipped behaviour byte-identical.

| Flag | Values | Default | Fix |
|---|---|---|---|
| `passage_order` | `"document"` \| `"relevance"` | `"document"` | F-D |
| `passage_titles` | `"off"` \| `"on"` | `"off"` | F-T, the writer |
| `rerank_titles` | `"off"` \| `"on"` | `"off"` | F-T, the re-ranker |

## Requirements

### 1. `passage_order = "relevance"` (F-D)

`_semantic_search_expanded` keeps both rounds and its merge exactly as now: the first call's record wins
for its chunks. Only the final order changes, done by a new pure function:

- **Relevance** of a match is its `rerank_score` when present, else its `score`.
- **The first `cfg.retrieval.semantic_top_k`** matches by relevance (ties: lower `chunk_id`) are the
  writer's window. Inside the window, passages are grouped by document:
  - documents ordered by their best passage's relevance;
  - inside a document, by `chunk_id` (its ordinal), so a table stays next to its narrative.
- **The rest** follow the window, in relevance order.
- `"document"`, the default, gives exactly today's order.
- **Provenance.** `_to_retrievals` adds `"rerank_score"` to provenance when the match has one, so a run's
  trace shows what the order used. Do this in both modes; it is a new key and changes no existing one.
- **Coverage.** The same applies wherever `_semantic_search_expanded` is used (the documents route, and
  hybrid's documents half). `_targeted_doc_search` is unchanged.

### 2. `passage_titles = "on"` (F-T, the writer)

`synthesizer._format_doc_context` labels each passage with:

```
[Document: {title} · page {page} · chunk_id={chunk_id} · score={score:.3f}]
```

`{title}` is the provenance title, or the `document_id` when there is none. `{page}` is the page, or `?`.
Off: the label is unchanged.

### 3. `rerank_titles = "on"` (F-T, the re-ranker)

- **The orchestrator** passes `rerank_titles=True` to `mcp_client.vector_semantic_search` only when the
  flag is on and re-ranking is on. Off, the parameter is not sent, so an older MCP never sees it.
- **The MCP.** `vector_tools.semantic_search` accepts `rerank_titles: bool | None = None` (validate the
  type), and `_rerank` sends `f"Document: {title}\n{content}"` for each passage that has a title.
  Without the flag, the passages it sends are byte-identical to today's.
- **Plumbing.** Add the parameter wherever the tool's parameters are declared or forwarded: the tool
  registration, `mcp_client`. List what you touched.

## Acceptance (the lead runs these)

- **C1.** Unit tests for the ordering function:
  - relevance with and without `rerank_score`;
  - the window size;
  - grouping and order within a document;
  - ties;
  - `"document"` giving today's order on the same input.
- **C2.** Unit tests for the writer's label (on and off) and for `_rerank`'s passage texts (on, off, and
  a missing title). Use a stub in place of the HTTP call to the re-ranker.
- **C3.** The orchestrator sends `rerank_titles` only when it should.
- **C4.** `/opt/anaconda3/bin/python3 -m pytest -q tests` passes, and the venv run of the tests the host
  skips passes (reuse `/tmp/t1venv`). `PATH=/opt/anaconda3/bin:$PATH scripts/lint-no-literals.sh`
  passes. Report ruff on the files you changed only.

## Rules

- **Allowed:** `orchestrator/`, `mcp/tools/vector_tools.py` and whatever registers or validates that
  tool's parameters, `config/flags.toml` (new flags only), `tests/`.
- **Not allowed:** other `mcp/` behaviour, `web/`, `scripts/`, `config/error_taxonomy.json`,
  `docs/eval/`, Docker.
- Never `git stash`, never push, never touch another branch. If your worktree is not at `abf7e15`,
  reset your own worktree branch to it. Commit once, with a message starting `T3:`.
- Match the surrounding code's style. Every numeric literal is config or carries `# noqa: literals -
  <reason>`.
- Report the files changed, the tests added or changed, the commands run with their output tails, and
  anything you were unsure of.
