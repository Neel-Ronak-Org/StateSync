# Spec: RAG v2 — measured, layout-aware, hybrid retrieval

Owner: StateSync lead (audits the result by execution). Implementer: a coding agent.
Branch: `v3-phase3-workspaces` (do not create or switch branches).
Read first: `docs/V3_PLAN.md` (Phase 3c), `docs/eval/retrieval_2026-10-01.md`, this spec in full.

## 0. Why, and what "done" means

Measured on 2026-10-01 (`scripts/eval_retrieval.py`, 35 labelled questions): dense-only
retrieval finds **0 of 20** documents by an ID they contain; a crude hybrid lifted MRR
0.41 → 0.50 and chunk recall@8 77% → 87%. Uploaded PDFs are parsed as flat text (`pypdf`):
headings are lost and tables are flattened into lines.

This spec delivers, in order, each step **measured against a test set frozen before any
retrieval code changes**:

| Phase | Deliverable |
|---|---|
| A | A ~110-question retrieval test set (frozen) and a harness that measures the real MCP search path; a baseline run on today's code |
| B | Layout-aware parsing of uploads with pdfplumber: headings, paragraphs, lists, whole tables |
| C | Structure-aware chunking: sections never mixed, tables whole (split by rows with the header repeated), breadcrumb prefix, small overlap inside a section only |
| D | Hybrid retrieval: BM25 (term rarity) keyword leg in plain SQL + dense, fused with RRF |
| E | Documents scoped by dataset; abstention when nothing relevant is found |
| F | A final measured comparison and a written report |

Out of scope (separate specs; they need downloads the owner must approve): a re-ranker
model, a new embedding model, Docling/OCR, re-chunking or re-embedding the 20,245-chunk
sample corpus.

## 1. Rules (non-negotiable)

- No `git` command that changes history or the tree (`commit`, `stash`, `checkout`, `reset`,
  `rebase`). Leave everything uncommitted; the lead commits after the audit.
- Never `docker compose down`, never `-v`. **Never build or restart `postgres`.** Build and
  start one service at a time: `docker compose build <svc> && docker compose up -d --no-deps <svc>`.
  `docker compose up --build <svc>` is FORBIDDEN (it rebuilds postgres as a dependency).
- Do not start the `evaluator`. **Make no Groq/LLM calls**: nothing in this spec needs one
  (embeddings are local Ollama). Do not ask the running app chat questions.
- Do not download models or images other than Python packages named here.
- Migrations: apply by hand, twice (idempotence check):
  `docker exec -i postgres psql -v ON_ERROR_STOP=1 -U statesync_migrator -d statesync < migrations/027_rag_v2.sql`
- Host Python: `/opt/anaconda3/bin/python3` (never bare `python3`). FastAPI tests run in
  `/private/tmp/claude-501/-Users-neeljaiswal-StateSync/ed6b98eb-8276-4b49-94ad-097eb56566e3/scratchpad/venv/bin/python -m pytest`.
- No configurable numeric literals in `mcp/`, `orchestrator/`: values go in
  `config/statesync.toml` / `config/flags.toml`. `PATH=/opt/anaconda3/bin:$PATH bash scripts/lint-no-literals.sh` must pass.
- New behaviour ships behind flags **at today's behaviour** (D-v3-16). Every new flag is
  registered in `scripts/shipped_flags.py` SHIPPED_DEFAULTS. The lead flips defaults after the audit.
- **Test-set integrity:** the test set (Phase A) is written and its SHA-256 recorded BEFORE
  any Phase B–E code is written. Never edit a question or its gold after seeing a result,
  except to fix a demonstrable label error, which must be listed in the report with the
  reason. Never tune thresholds on the same questions you report without saying so.
- Match the codebase style: module docstrings that explain WHY, `_fail(code, message)`
  results in MCP tools, config via `cfg.*`, tests with a WHY docstring.

## 2. Phase A — test set and harness (do this first)

### A1. `fixtures/golden/retrieval_v2.json`

```json
{"version": 1, "created": "<date>", "questions": [
  {"id": "rv2_001", "corpus": "sample" | "upload", "dataset": "public" | "olist" | "upload",
   "category": "table" | "definition" | "fact" | "paraphrase" | "id" | "cross_section" | "unanswerable",
   "question": "...",
   "gold_document": "<document_id for sample | upload filename>" | null,
   "evidence": ["verbatim substring the right passage contains", "..."]}
]}
```

Gold is **evidence text, not chunk ids**, so it survives re-chunking. A question is a hit at
k when a passage in the top k is from `gold_document` and contains (after lower-casing and
collapsing whitespace) at least one `evidence` string. `unanswerable` items have
`gold_document: null`, `evidence: []`.

Contents (author-written by you from the real documents; read them, do not guess):
- The 15 `fixtures/golden/phase_e1.json` questions that have `expected_chunk_ids`: keep the
  question, set `gold_document` from the chunk ids, and take a distinctive 4–12-word
  `evidence` substring from each expected chunk's `content_md` (query
  `public.document_chunks` as `statesync_migrator`, read-only SELECTs only).
- The 20 `fixtures/golden/review_probe_set.json` probes: `category: "id"`,
  `dataset: "olist"`, evidence = the seller id.
- 40 new sample-corpus questions over `public` documents (supplier agreements, SLA
  annexures, policies, SOPs): 10 `table` (answer is a cell of a table), 10 `fact`,
  10 `paraphrase` (no content word shared with the evidence sentence), 10 `id` (name an
  `S0xx`/`LP0xx`/document-specific id).
- 25 upload questions over the three demo PDFs in `demo/documents/`: 8 `table` (the seller
  levels table, the SLA table), 7 `definition` (the handbook definitions), 6 `fact`
  (returns policy), 4 `cross_section`.
- 10 `unanswerable`: in-domain questions no document answers (e.g. a policy that does not
  exist), 5 for each corpus.

Record `sha256sum fixtures/golden/retrieval_v2.json` in your report **before Phase B**.

### A2. `scripts/eval_retrieval.py` (rewrite; keep the host/container split it has)

- Measures the **production path**: it calls the MCP method `vector.semantic_search` over
  JSON-RPC from inside the orchestrator container (the pattern in
  `scripts/verify_library.py::mcp`), with headers `X-Tenant-ID: t_capstone_default` and,
  for upload questions, `X-Workspace-ID`.
- Upload corpus setup per run, through the HTTP API with the dev token
  (`secrets/api_token_dev`; never print it): create folder `eval-retrieval-<timestamp>`,
  upload the three demo PDFs, wait until `ready`, create a workspace on `olist`, link the
  folder; **delete workspace and folder at the end, even on failure** (`try/finally`).
- Per question: top 10 passages, latency. Metrics overall, per corpus and per category:
  hit@1, hit@5, hit@10, MRR@10; for `unanswerable`: the top-1 score distribution; latency
  p50/p90.
- Parameters forwarded to `vector.semantic_search` (added in Phases D/E; the harness passes
  them through when given): `--mode dense|hybrid`, `--scope all|dataset`.
- `--label <name>` writes `docs/eval/retrieval_v2_<label>.json` (all per-question results);
  `--compare <a> <b>` prints a side-by-side table of two saved runs.

### A3. Baseline run, on today's code, before any other change

`python3 scripts/eval_retrieval.py --label baseline`. Report its table. This is the number
everything else is compared to.

## 3. Phase B — layout-aware parsing (parser service)

`parser/server.py` stays the only place untrusted files are read, with the same isolation
(child process, memory/CPU/time limits, no network). Add `pdfplumber==0.11.10` to
`parser/Dockerfile` (pinned; the version already used by `scripts/ingest.py`).

Response becomes (keep `pages` exactly as today for compatibility):

```json
{"ok": true, "pages": [{"page": 1, "text": "..."}],
 "blocks": [
   {"page": 1, "kind": "heading", "level": 1, "text": "Marketplace Seller Handbook 2026"},
   {"page": 1, "kind": "paragraph", "text": "..."},
   {"page": 1, "kind": "list_item", "text": "..."},
   {"page": 1, "kind": "table", "rows": [["Level", "Delivered orders (90 days)", "On-time share"], ["Bronze", "1 - 49", "at least 85%"]]}
 ]}
```

PDF rules (pdfplumber):
- Tables: `page.find_tables()`; rows via `table.extract()`; `None` cells → `""`. Narrative
  text comes from the page **outside** every table's bbox (`page.outside_bbox` per table, as
  `scripts/ingest.py` does), so table text is never duplicated into paragraphs.
- Lines: group words into lines (`page.extract_text_lines()` or `extract_words` grouped by
  `top`), reading order top-to-bottom then left-to-right. Multi-column layouts are a known
  limitation (document it); do not attempt column detection.
- Headings: compute the page-weighted median body font size. A line is a heading when it is
  short (≤ `[library].heading_max_chars`, no sentence-final period) and either its size ≥
  `heading_size_ratio` × median, or it is bold (font name contains "Bold") and
  ≤ `heading_bold_max_words` words, or it matches a numbered-section pattern
  (`^\d+(\.\d+)*\.?\s+\S`) while bold or larger. Level: rank of distinct heading sizes
  (largest = 1), capped at 3.
- Paragraphs: consecutive non-heading lines, broken at vertical gaps >
  `paragraph_gap_ratio` × line height. Lines starting with `•`, `-`, `*`, `\d+[.)]` are
  `list_item`.
- Markdown uploads: `#`…`###` headings, pipe tables (header + separator + rows) → `table`,
  `-`/`*`/`1.` → `list_item`, blank-line paragraphs. Plain text: blank-line paragraphs only.
- All thresholds in `[library]` (`heading_size_ratio = 1.15`, `heading_max_chars = 120`,
  `heading_bold_max_words = 12`, `paragraph_gap_ratio = 1.5`). The parser reads them from
  the mounted config, as it does today.

Tests (`tests/test_parser.py`, extend): build test PDFs with reportlab in the test (title,
two headings, paragraphs, a bulleted list, a table) and assert the block sequence; a table's
cells never appear in a paragraph block; markdown and text parsing; every existing parser
test still passes; the hostile-input probes in the test file still pass.
Live: rebuild and restart only `parser`; parse the three demo PDFs through the container
(`docker run --rm --network statesync_parser_net -v "$PWD/demo/documents":/w:ro python:3.12-slim python -c "<POST each PDF to http://parser:8095/parse>"`) and
include the block list for the handbook in your report.

## 4. Phase C — structure-aware chunking (orchestrator)

`orchestrator/library_ingest.py`: new `chunk_blocks(blocks, title, cfg)` used when the
parser returns `blocks` and `[library].chunker = "layout"`; the existing `chunk_pages`
stays for `chunker = "paragraph"`. **Ship `chunker = "paragraph"`** (today's behaviour).

Rules:
- Sections: a heading opens a section; the section path is the stack of open headings
  (level-aware). A chunk never contains text from two sections.
- Pages: a chunk never spans two pages (citations stay exact); a section that crosses a
  page continues as a new chunk with the same section path.
- Text chunks: pack paragraphs and list items up to `layout_chunk_chars` (default 1200);
  a paragraph longer than that is split at sentence ends (reuse `_split_long`). Overlap:
  up to `layout_overlap_chars` (default 150) of the previous chunk's tail, **only within the
  same section and page**. Fragments under `chunk_min_chars` merge into the previous chunk
  of the same section, or are dropped if there is none.
- Tables: one chunk per table when its Markdown fits `table_chunk_max_chars` (default
  2400); otherwise row groups, each with the header row and separator repeated. A table
  chunk also carries the paragraph immediately before it when that paragraph is ≤
  `table_context_max_chars` (default 300) — it usually says what the table is.
- Every chunk's text starts with a breadcrumb line, then a blank line:
  `{document title} › {h1} › {h2}` (only the levels present). Tables are rendered as
  Markdown (`| a | b |`, `|---|---|`).
- Each chunk dict: `{ordinal, page, text, kind: "text"|"table", section: "<h1 › h2>" | null}`.

MCP `library.manage add_chunks` accepts optional `kind` (`text`/`table`, default `text`)
and `section` (≤ `[library].section_max_chars`, default 300) and stores them. The upload
route passes `blocks` and the title through to `ingest`.

Retrieval results for uploaded chunks add `section` (and `kind`); the orchestrator's
`_to_retrievals` carries `section` in provenance; `build_citations` shows the citation title
as `"{title} › {section}"` when a section exists (sample-corpus citations unchanged).

Tests (`tests/test_library_ingest.py`, extend): sections never mixed; pages never mixed;
overlap only within section and page; a long table split with the header repeated; the
table context paragraph attached; breadcrumbs exact; tiny fragments merged; every existing
test still passes.

## 5. Phase D — hybrid retrieval (MCP)

### D1. Migration `migrations/027_rag_v2.sql` (exactly this, idempotent)

```sql
BEGIN;
-- Upload chunks: structure and a stored tsvector for the keyword leg.
ALTER TABLE workspace_docs.chunks ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'text';
ALTER TABLE workspace_docs.chunks ADD COLUMN IF NOT EXISTS section TEXT;
ALTER TABLE workspace_docs.chunks ADD COLUMN IF NOT EXISTS tsv tsvector
  GENERATED ALWAYS AS (to_tsvector('english', content_md)) STORED;
CREATE INDEX IF NOT EXISTS chunks_tsv_ix ON workspace_docs.chunks USING gin (tsv);
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chunks_kind_ck') THEN
    ALTER TABLE workspace_docs.chunks ADD CONSTRAINT chunks_kind_ck CHECK (kind IN ('text', 'table'));
  END IF;
END $$;

-- Sample corpus: a side table of tsvectors (public is pinned by the A5 fingerprint, so
-- its tables are not altered; indexes are outside A5 but a side table also stores length).
CREATE SCHEMA IF NOT EXISTS search;
CREATE TABLE IF NOT EXISTS search.sample_terms (
  chunk_id    TEXT PRIMARY KEY,
  document_id TEXT NOT NULL,
  tenant_id   TEXT NOT NULL,
  tsv         tsvector NOT NULL,
  len         INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS sample_terms_tsv_ix ON search.sample_terms USING gin (tsv);
CREATE INDEX IF NOT EXISTS sample_terms_tenant_ix ON search.sample_terms (tenant_id);
INSERT INTO search.sample_terms (chunk_id, document_id, tenant_id, tsv, len)
  SELECT chunk_id, document_id, tenant_id, to_tsvector('english', content_md),
         length(to_tsvector('english', content_md))
  FROM public.document_chunks
ON CONFLICT (chunk_id) DO UPDATE SET tsv = EXCLUDED.tsv, len = EXCLUDED.len;
GRANT USAGE ON SCHEMA search TO statesync_reader;
GRANT SELECT ON search.sample_terms TO statesync_reader;
ALTER TABLE search.sample_terms ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'search' AND tablename = 'sample_terms'
                 AND policyname = 'tenant_isolation') THEN
    CREATE POLICY tenant_isolation ON search.sample_terms
      USING (tenant_id = current_setting('statesync.tenant_id', true));
  END IF;
END $$;
COMMIT;
```

Also add `scripts/refresh_sample_search.py` (runs the INSERT … ON CONFLICT and deletes rows
whose chunk no longer exists) for after any re-ingest, and a note in `scripts/ingest.py`'s
docstring. Confirm after applying: MCP restarts healthy (the A5 fingerprint check passes),
`tests/test_rls_coverage.py` passes, and `search` is added to that test's schema list only
if it is designed to cover all app schemas (read it; do not weaken it).

### D2. Keyword leg (BM25 in SQL) — `mcp/tools/vector_tools.py`

- Query lexemes: `tsvector_to_array(to_tsvector('english', :query))`. None → empty leg.
- Per corpus (sample: `search.sample_terms` for the tenant, optionally filtered by
  document ids/prefixes; uploads: `workspace_docs.chunks` joined to the workspace's ready
  documents as `_library_matches` does): N = chunk count, avgdl = avg length (cache the
  sample-corpus N/avgdl per tenant in process for `bm25_stats_ttl_s`; compute uploads live),
  df per lexeme = count of chunks whose tsv contains it (GIN index), idf =
  `ln(1 + (N - df + 0.5) / (df + 0.5))`.
- Candidates: chunks whose tsv matches the OR of the lexemes. Score =
  Σ idf · tf·(k1+1) / (tf + k1·(1 − b + b·len/avgdl)), tf from `unnest(tsv)` positions.
  `k1 = bm25_k1 (1.2)`, `b = bm25_b (0.75)`. Return the top `hybrid_candidates_per_leg` (50).
- Parameterised SQL only; lexemes passed as arrays, never interpolated.

### D3. Fusion

`vector.semantic_search(query_text, top_k=None, document_ids=None, mode=None, dataset=None)`.
`mode` defaults to `cfg.retrieval.mode`; `"dense"` = exactly today's behaviour.
`"hybrid"`: four ranked lists (dense sample, dense uploads, BM25 sample, BM25 uploads, each
top `hybrid_candidates_per_leg`), fused by RRF with `rrf_k` (60); return the top `top_k` with
fields `chunk_id, document_id, content_md, page, score (RRF), dense_score, keyword_score,
title, section, kind`. The second-round `document_ids` expansion uses the same mode.
Orchestrator: `vector_semantic_search` forwards `mode` from
`flags_snapshot["pipeline.retrieval_mode"]` and `dataset` from the state.

New flag `[pipeline] retrieval_mode = "dense"` (`"dense" | "hybrid"`), shipped `"dense"`.
Config `[retrieval]`: `mode`, `bm25_k1`, `bm25_b`, `rrf_k`, `hybrid_candidates_per_leg`,
`bm25_stats_ttl_s`.

Tests (pure, host): the BM25 formula on a hand-computed example; RRF fusion order on a
hand-computed example; lexemes never interpolated (inspect the SQL template); `mode=dense`
returns exactly today's ordering (fake connection).

## 6. Phase E — scope by dataset, and abstention

- Scope: `[retrieval] dataset_document_prefixes` maps datasets to sample document-id
  prefixes: `public = ["doc_policy_", "doc_sop_", "doc_supplier_agreement_", "doc_sla_annexure_"]`,
  `olist = ["doc_review_digest_"]`, `live = []`. Verify these prefixes against
  `SELECT DISTINCT split_part(...)` on the real ids and fix the list if any are missing;
  list every prefix found in the report. With flag `[pipeline] doc_scope = "dataset"`, the
  sample corpus is filtered to the dataset's prefixes; a connected database (`cn_…`) or a
  dataset with no prefixes gets no sample documents; uploads are unaffected (already scoped
  by the workspace's folders). Shipped `"all"` (today).
- Abstention: flag `[pipeline] doc_abstain = "off"` (shipped). When `"on"` and the best
  dense score < `abstain_min_dense` AND the best keyword score < `abstain_min_keyword`, the
  docs route returns no retrievals and the synthesizer answers, **without a model call**,
  `[retrieval].abstain_message` ("I couldn't find this in the documents available to this
  question."). Calibrate the two thresholds on the `unanswerable` vs answerable top-1 score
  distributions from the harness, choose them to keep answerable recall loss ≤ 2 questions,
  and report the distributions and the choice (state that they were chosen on the test set).
- Tests: prefix filtering; connected datasets get none; abstention returns the fixed
  message with zero LLM calls (fake LLM that raises if called); flags off = today.

## 7. Phase F — measure and report

Runs (all on the frozen test set):
1. `baseline` — today's code (Phase A3).
2. `layout_dense` — `chunker=layout`, `mode=dense`.
3. `layout_hybrid` — `chunker=layout`, `mode=hybrid`.
4. `layout_hybrid_scoped` — plus `doc_scope=dataset`.
Changing `chunker` needs the upload re-ingested: restart only `orchestrator` after editing
`config/statesync.toml`, and let the harness re-upload (it creates a fresh folder each run).
**Restore every flag/config value to its shipped value at the end** and confirm with
`/opt/anaconda3/bin/python3 scripts/shipped_flags.py` (restart `orchestrator`/`mcp` by
name if needed).

Report (`docs/eval/retrieval_v2_report.md`): the frozen-set SHA-256; per-run table
(overall, per corpus, per category: hit@1/@5/@10, MRR, latency p50/p90); unanswerable score
distributions and chosen thresholds; label fixes (if any) with reasons; what did not
improve; known limitations (multi-column PDFs, scanned PDFs, sample corpus not re-chunked).

## 8. Completion criteria (report each with command output)

1. `fixtures/golden/retrieval_v2.json` exists with ≥ 105 questions in the stated mix; its
   SHA-256 was reported before Phase B code existed; unchanged since (or every change listed).
2. All four harness runs saved under `docs/eval/`; `retrieval_v2_report.md` written.
3. `/opt/anaconda3/bin/python3 -m pytest -q tests` passes; the venv run passes for
   `tests/test_library_api.py tests/test_workspaces_api.py tests/test_mine_routes.py`.
4. `PATH=/opt/anaconda3/bin:$PATH bash scripts/lint-no-literals.sh` passes; `ruff check
   --select F,B,UP` (venv) clean on every changed Python file.
5. Migration 027 applied twice cleanly; `mcp` healthy after restart; `tests/test_rls_coverage.py` passes.
6. Live: `/opt/anaconda3/bin/python3 scripts/verify_library.py` 24/24,
   `scripts/verify_workspaces.py` 78/78, `scripts/verify_connections.py` 24/24,
   `scripts/verify_deployed.py` only the 2 evaluator failures (rebuild `parser`, `mcp`,
   `orchestrator`, `frontend` by name as needed).
7. Shipped flags restored: `scripts/shipped_flags.py` passes; the new flags are in
   SHIPPED_DEFAULTS at today's behaviour.
8. Web: if any web file changed, `cd web && npm run lint && npx tsc -b --noEmit && npx
   playwright test e2e/mocked` pass.
9. `git status --porcelain` lists only files this spec names or obviously requires.

Report back: files changed; each criterion's evidence; the Phase F table; anything you
deviated from and why. Do not claim an improvement the numbers do not show.
