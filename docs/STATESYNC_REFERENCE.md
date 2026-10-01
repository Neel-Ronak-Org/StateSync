# StateSync — Complete Technical Reference

**Status:** as of 2026-10-01, branch `v3-phase3-workspaces` (258+ commits since 2026-08-13).
**Purpose:** everything needed to explain, defend, and write about StateSync: what it does,
how every part works, why each choice was made, what was measured, which papers it draws on,
and what is known to be weak. Every claim points to a file so it can be checked.
**Conventions:** paths are relative to the repository root. Numbers carry their date and
source. "Shipped" means the default configuration (`config/flags.toml`). Items marked
**(open)** are known gaps; **(fixed YYYY-MM-DD)** were found and fixed.

How it was assembled: three independent read-only reviews of the code, documents and git
history (architecture; methodology and results; frameworks, models, papers and datasets),
cross-checked by the lead against the running system. Papers cited in §16 were verified
against arXiv or publisher pages on 2026-10-01.

---

## Contents

1. One-page summary
2. Problem, motivation and positioning
3. What StateSync does (user journeys and features)
4. System architecture (services, networks, trust boundaries)
5. The question pipeline, step by step
6. Text-to-SQL methodology
7. Documents and retrieval (RAG)
8. The trust layer: evidence tiers, deterministic checks, figure grounding
9. Workspaces and the knowledge store
10. Dashboards, tiles and live data
11. Connecting your own Postgres
12. Security model
13. Data model (schemas, tables, migrations)
14. Frontend
15. Models and the LLM provider layer
16. Frameworks and libraries: what and why
17. Research lineage: papers, benchmarks and what was taken from each
18. Datasets
19. Evaluation methodology
20. Measured results (complete ledger)
21. Design decisions (with reasons)
22. Project history and process
23. The research study (publication plan)
24. Known issues, limitations and open questions
25. Writing a paper about StateSync: claims you can and cannot make
26. Reviewer FAQ
27. Glossary
28. File map

---

## 1. One-page summary

StateSync is a **self-hosted AI data analyst**. A user asks a question in plain English (or
Hindi/Hinglish); StateSync answers from a Postgres database, from uploaded documents, or
both, and shows **how much to trust the answer**.

- **Questions → answers.** A LangGraph pipeline routes each question to SQL, documents, or a
  hybrid of both. SQL is written by an LLM, executed through a three-layer read-only gate,
  corrected on failure (error-classified retries), and summarised in prose with a chart.
- **Trust, computed from evidence rather than model confidence.** Every answer carries an
  **evidence tier** (Verified / Compiled / Checked / Unchecked), a plain-English "How I got
  this" reading of the SQL, and **figure grounding**: every number in the prose is traced in
  code to the result cell, total, question or cited passage it came from. Deterministic
  checks catch join fan-out (double counting), truncated results and NULL-heavy aggregates.
- **Teaching it a business.** Workspaces hold the owner's business description, approved
  column descriptions, business definitions (including ones mined from uploaded documents
  with verified quotes), and approved question→SQL examples that are reused verbatim
  (Verified) or as templates (Compiled). Approved answers re-check themselves for schema drift.
- **Dashboards** pin the SQL (not the question) and refresh with no model call; a simulator
  keeps a `live` dataset moving so refresh is visible.
- **Security by architecture.** The LLM-facing orchestrator holds no database credential and
  cannot reach the database network; a separate MCP service is the only credential holder and
  runs every query through a parser gate, a read-only transaction, and a read-only role.
  Row-level security isolates tenants. Uploaded files are parsed in an isolated container.
  Owner databases are accepted only with a provably read-only login.
- **Measured.** On a 30-question baseline with Groq gpt-oss-120b: 25/30 correct, 4.7 model calls
  and ~5,000 tokens per question, 2.6 s median latency (2026-09-29). Retrieval: 110-question
  frozen test set, hit@5 77% at baseline (2026-10-01). Figure grounding over 944 stored answers:
  97.5% of figures traced (in-sample pilot).
- **Research angle.** Can checks computed from the data flag wrong answers as well as, and more
  cheaply than, model-based confidence (LLM judges, self-consistency)? A pre-registered
  study on BIRD Mini-Dev (corrected annotations) is planned (§23).

Stack: Python 3.12 (FastAPI, LangGraph, Pydantic, psycopg 3, pglast), Postgres 16 + pgvector
+ PostGIS, Ollama (mxbai-embed-large embeddings), Groq-hosted LLMs, React 19 + Vite + Tailwind +
Vega-Lite, Docker Compose (9 services).

---

## 2. Problem, motivation and positioning

**The problem.** Natural-language analytics tools ("chat with your data") fail in a specific,
dangerous way: when the generated query is wrong, the answer is still well-formed — a number, a
sentence, a chart. A join that repeats rows inflates a sum; a follow-up counts at the wrong
grain; a sentence restates a figure the result does not contain. Users cannot tell. In the dbt
Labs 2026 survey, 71% of respondents named incorrect or hallucinated outputs reaching
stakeholders as a top concern (cited in `docs/research/paper_facts.md`).

**What the field does.** (a) Add context: semantic layers, verified query repositories,
glossaries (Snowflake Cortex Analyst, Databricks Genie, Power BI verified answers). (b) Predict
correctness from the model: self-consistency, log-probabilities, an LLM judge. Richardson
(2026, arXiv:2607.06799) reports black-box signals at 0.61–0.68 AUROC, single LLM judges at
0.72–0.78, a two-provider ensemble at 0.82.

**StateSync's position.** It does (a) — workspaces, approved examples, definitions — and adds a
third route: **check the answer against the data, without a model**. Many errors leave traces
in the executed result (a count that differs from a distinct count; a figure in the prose that
appears nowhere in the result; a result cut at the row limit). Such checks cost no tokens and
are exact rules; their weakness is coverage. The evidence tier then tells the user which path
produced the answer: an owner-approved query run as is, a template filled in code, new SQL that
passed the checks, or new SQL that did not.

**Other differentiators** (compared with typical tools, see §12 and §11): security that does not
depend on prompt behaviour (credentials never reach the model side; three SQL layers);
self-hosted by default (D-v3-18); owner databases only through a provably read-only login;
everything runs on a laptop.

**Project goals.** (1) A polished, end-to-end AI-engineering portfolio project
(`memory: statesync-portfolio-goal`); (2) a modest research publication (workshop / regional
venue) on data-level answer checks (§23). Originally a B.Tech capstone (NMIMS MPSTME,
Semester VII, AY 2026-27; `docs/StateSync_Strategy_Document.md` header).

---

## 3. What StateSync does

### 3.1 User journeys (`docs/V3_PLAN.md` §4)

| # | A user can… | Status |
|---|---|---|
| J1 | Try a read-only demo on sample data without sign-up | Hosted demo deferred (D-v3-18); local demo account exists (`docs/DEMO.md`) |
| J2 | Set up a workspace from a database: business description, scan, approve AI drafts, 10-question setup test | Built |
| J3 | Ask questions answered from the database, documents, or both, with follow-ups, history, citations | Built |
| J4 | Pin a chart/number to a dashboard; tiles refresh from saved SQL | Built |
| J5 | Upload documents (PDF, text, Markdown) | Built (account-level library, D-v3-17) |
| J6 | Connect their own Postgres read-only | Built (self-hosted) |
| J7 | Developer mode: SQL, traces, per-call tokens and latency | Built |

### 3.2 Feature list

**Ask (chat).** Natural-language questions; follow-ups rewritten into standalone questions
(last 3 turns); routing to SQL / documents / hybrid; charts (Vega-Lite, 11 kinds chosen in the
browser from the result shape); result table with CSV/PNG export; evidence chip; caveats;
"How I got this"; figures marked with their source on hover; thumbs up (turns the answer into an
approved example) / thumbs down (withdraws a verified one); details sheet (SQL, model calls).
Conversations are per-user, pinned to one dataset.

**Datasets.** Built-in: `olist` (real Brazilian e-commerce, 1.55M rows), `public` (synthetic
ONDC-style logistics), `live` (simulated shop, new orders every 5 s). Plus any owner database.

**Workspaces.** Business description (versioned), catalog scan, AI-drafted descriptions and
definitions, owner edits, approval, setup test (graded against reference SQL on sample
datasets; owner-graded elsewhere), approved examples (fast path + templates), self-testing of
approved answers, definitions found in documents (quote-verified), document folders to search.

**Library.** Account-level folders of uploaded documents (upload once, use in any workspace);
own-database connections (Databases tab).

**Dashboards.** Multiple dashboards per dataset; drag-and-drop 12-column grid; KPI, chart,
table, breakdown and sparkline cards; "Build with AI" (plans 4–8 insights and pins them);
live tiles refresh every 30 s on `live`; tiles flip to "needs review" if the schema they read
changes.

**Developer mode.** Every model call with role, model, tokens (incl. cached), retries, wait time,
latency; the state trace.

---

## 4. System architecture

### 4.1 Services (`docker-compose.yml`; 9 services)

| Service | Role | Networks | Credentials held | Port |
|---|---|---|---|---|
| `postgres` | Postgres 16.4 + PostGIS 3.4.3 + pgvector 0.8.5; pinned by digest (glibc collation incident, V3 §0.5) | db_net only | migrator (owner) | 5432 (not published) |
| `qdrant` | v1.9.4; trajectory memory only (shipped off) | db_net only | none | — |
| `ollama` | `mxbai-embed-large` embeddings (1024-d) | db_net + app_net | none | 11434 |
| `mcp` | JSON-RPC tool server; **the only DB credential holder** | db_net + app_net | reader, history, connection_key | 8080 |
| `orchestrator` | FastAPI + LangGraph pipeline; auth; LLM keys | app_net + parser_net | LLM API keys, token hashes; **no DB credential** | 8090 |
| `parser` | Upload parser (pypdf; pdfplumber from RAG v2) | parser_net only (internal) | none | 8095 (unpublished) |
| `frontend` | nginx serving the React bundle | app_net | none | 3000 |
| `simulator` | Feeds the `live` schema | db_net only | simulator role (writes `live`, `live_sim` only) | — |
| `evaluator` | Hourly golden-set runs (kept off: spends quota) | app_net | none | — |

Networks: `db_net` (internal: no external routing), `app_net` (bridge), `parser_net`
(internal). **The orchestrator is not attached to `db_net`**, so a direct database connection
from the model-facing side fails at the network layer (verified by `scripts/verify_b.sh` B-V3),
and its image ships no Postgres driver.

### 4.2 Trust boundaries

```
 Browser ──HTTPS/HTTP──▶ frontend (nginx, CSP) 
    │ bearer token
    ▼
 orchestrator  (LLM keys; NO DB credential; app_net + parser_net)
    │ JSON-RPC (tenant header)            │ raw upload bytes
    ▼                                     ▼
 mcp (ONLY credential holder)          parser (no network, no secrets,
    │ reader role: READ ONLY txn          child process per file,
    │ history role: app state writes      memory/CPU/time limits)
    ▼
 postgres (RLS on app tables)   ollama (embeddings)   qdrant (memory, off)
    ▲
 simulator (writes live/live_sim only)
```

### 4.3 Database roles (migrations 001, 002, 007, 011, 020, 021, 024, 026)

| Role | Can | Used by |
|---|---|---|
| `statesync_migrator` | Everything (owner; bypasses RLS) | Migrations, operator only |
| `statesync_reader` | SELECT on `public`, `olist`, `live`, `workspace_docs` (+ `search` from RAG v2); **nothing** on `session_state` | MCP for all model-influenced SQL and retrieval |
| `statesync_history` | DML on application state (`session_state.*`, `workspace_docs.*`) | MCP tools that persist state; never runs model SQL |
| `statesync_simulator` | Writes `live`, `live_sim` | simulator |
| `statesync_ingest` | Writes the document corpus | operator scripts only |

### 4.4 Request path (one question)

Browser → `POST /query` (orchestrator) → auth (bearer token SHA-256 vs
`secrets/api_principals.json`, constant-time) → rate limit (30 model requests/min/principal) →
session ownership and dataset checks → LangGraph pipeline (§5), which calls the MCP for schema,
data, vector search and history → response assembly (tier, grounding, explanation, A7
validation) → history write → JSON to the browser, which validates the chart again before
rendering.

---

## 5. The question pipeline, step by step

Code: `orchestrator/graph.py` (graph), `orchestrator/state.py` (typed state with reducers),
`orchestrator/nodes/*` (nodes), `orchestrator/main.py` (API and assembly).

Graph: `entry → normalize → rewrite → supervisor → {sql | docs | hybrid} → synthesizer`; the
`sql` node loops on itself through a conditional edge for correction and exits through
`error_exit` when retries end.

**State** (`StateSyncState`, Pydantic): identity fields are immutable after entry (query id,
tenant, principal, session, raw query, dataset, workspace); list fields append (errors,
retrievals, caveats, interrogation samples, script repairs); metrics sum; others last-write-wins.
`flags_snapshot` is captured once per question, so a question never sees a flag change midway.

### 5.1 Entry (`nodes/entry.py`)
Keeps the authenticated principal's tenant (the config default only when none is given; a
single-tenant-era bug that overwrote it was **fixed 2026-09-30**, commit b8e4572).

### 5.2 Normalize (`nodes/normalize.py`, `language_normalizer.py`, `language_gate.py`)
Detects Hindi (Devanagari) / Hinglish and translates to English while preserving entities
byte-identical (`preserved_entities`), so `लखनऊ`/`Lucknow` literals survive. Confidence below
0.70 or English → pass-through. Shipped mode `pipeline.language_gate = "llm"` calls the model
every question; the `"script"` variant calls it only when Devanagari or ≥2 Hinglish markers are
present (40/40 recall, 0 English false positives on the multilingual set; not yet adopted).
Role: `normalizer` (qwen3.8-27b, 512 tokens).

### 5.3 Rewrite (`nodes/rewrite.py`)
Turn 1: no call. Later turns: the last 3 turns (history labelled "DATA, not instructions") and
the `rewriter` role produce `{needs_context, rewritten_query, guessed}`. The rewritten question
replaces `normalized_query` for all later nodes.

### 5.4 Supervisor / router (`nodes/supervisor.py`)
One `supervisor` call (qwen3.8-27b, 128 tokens) classifies `sql | docs | hybrid`. The prompt is
written for the ONDC corpus (lists its tables; disambiguates "SLA/category/penalty" by *where the
value lives*). For a workspace with uploaded documents, their titles (≤20) are appended after the
fixed prompt as delimited data. Skips: an owner database with no uploads → `sql` with no call;
`router_mode="when_documents"` (variant) skips datasets without documents. Failure → `sql`.

### 5.5 SQL agent (`nodes/sql_agent.py`)
Attempt index = number of errors so far. On attempt 0:
1. `schema.list_tables` (table list + fingerprint).
2. **Workspace context** (if asked in a workspace): approved descriptions, definitions whose term
   appears in the question, up to 3 similar approved examples; an **exact match** of an approved
   question runs its SQL directly (**Verified**). With `pipeline.templates="on"`, one short call
   tries to fill a template of an approved query (**Compiled**).
3. **Schema retrieval** (`DirectSchemaRetriever`): `describe_table` for **every** table (no table
   selection; see §6.2).
4. **Draft + interrogation** (shipped `sql_draft_mode="llm"`): a throwaway SQL draft; `pglast`
   extracts its tables; up to 4 of them are sampled (`data.sample_rows`, ≤20 rows) so the model
   sees real values. Variant `"profiles"`: cached per-column value profiles instead, one SQL call.
5. Hints: preserved entities; DIGIPIN spatial predicate (synthetic datasets only).
6. **Generation** (`sql` role, gpt-oss-120b, 1,024 tokens, reasoning_effort "low"): one SELECT,
   qualified columns, LIMIT, only the requested columns.
7. Deterministic **script repair** (`_repair_script_column_mismatch`): a Latin literal compared to
   a `*_local` column (or the reverse) is rewritten via the parse tree.
8. `data.run_select` (≤1,000 rows).
9. Outcomes: rows → optional **answer checks** (§8.2); empty → one `empty_result` retry; engine
   error → classified (`config/error_taxonomy.json`, SQLSTATE/regex → 13 categories);
   `permission_denied` (incl. gate rejections) and `unclassified` are terminal; otherwise retry
   with a correction prompt showing the full error history + taxonomy hints (`sql.retry_max=3`,
   i.e. ≤4 executions).

### 5.6 Documents and hybrid (`nodes/unstructured_knowledge.py`)
**Docs route (no LLM):** `vector.semantic_search` top 8 → the documents found are expanded to all
their chunks (second search restricted to those documents, top 25), ordered document-major so a
table chunk stays next to its narrative. **Hybrid route:** one `decomposer` call splits the
question into a SQL sub-question and a document sub-question; a single-shot SQL sub-task (no
correction loop, no workspace knowledge); if its result has `seller_id`/`provider_id`, a fused
`vector.hybrid_select` narrows documents to those parties in one SQL statement using
`ss_embed('<text>')` (Text2VectorSQL pattern, §17); else the docs route.

### 5.7 Synthesizer (`nodes/synthesizer.py`)
One `synthesizer` call (gpt-oss-120b, 1,024 tokens, JSON): answer prose + a chart **shape**
proposal. The real rows are attached in code (`build_chart`, ≤5,000 values); the spec is validated
against the **A7 contract** (JSON Schema Draft 7 + an expression audit that forbids `expr`,
`signal`, `calculate` etc.) and never repaired — a failing chart is dropped. Citations are built in
code (an SQL citation only when a statement reached the engine; the first 3 chunks with title and
page). Code in prose is stripped. Variant `synth_style="short"`: ≤3 sentences, no chart proposal.

### 5.8 Response assembly (`main.py`)
Number grounding (§8.3) → caveats → evidence tier (§8.1) → `explain_sql` (deterministic
plain-English reading) → second A7 validation on the exact payload → `history.append_turn`
(best-effort; rows preview 100, full rows ≤1,000 / 1 MiB, 30-day retention) → `state_trace`
(route, SQL, rows, errors, caveats, tier, grounding, retrievals, per-call records).

### 5.9 Cost per question (shipped flags)
First-turn SQL question: 5 model calls (normalize, route, draft, SQL, answer); follow-ups +1
(rewrite); each retry +1. Measured mean 4.7 calls, 4,652 input / 356 output tokens (Groq baseline,
2026-09-29). Per-call records are captured by `llm/recording.py` (`RecordingModel`).

---

## 6. Text-to-SQL methodology

### 6.1 Principles
1. **Context before cleverness.** Accuracy comes mostly from what the model is shown: schema with
   real values (interrogation), owner knowledge (descriptions, definitions, examples). Industry
   practice agrees (verified query repositories and semantic layers at Snowflake, Databricks,
   Power BI; review of 2026-10-01).
2. **Reuse what a human approved.** Exact approved question → approved SQL, no model (Verified);
   a close variant → template filled in code (Compiled); otherwise generate.
3. **Fail loudly, fix with evidence.** Errors are classified deterministically and fed back with a
   fixed remediation hint; retries are bounded.
4. **Security outside the model.** The model's SQL is untrusted input; three layers decide what
   runs (§12).

### 6.2 Components and their sources
| Component | What | Lineage |
|---|---|---|
| Interrogation sampling | Sample the tables a draft uses before final generation | APEX-SQL (Cao et al. 2026), "active interrogation" |
| Error taxonomy + bounded retry | 13 categories from SQLSTATE/regex; one correction call per retry | SQL-of-Thought (Chaturvedi et al. 2025), simplified to a deterministic classifier |
| Trajectory memory | Abstract insights keyed by schema fingerprint (off by default) | AgentSM (Biswal et al. 2026) |
| Schema retrieval seam | All tables described; selection deferred until schema >8,000 tokens or EX drops >10pp | CSR-RAG (Singh et al. 2026), deferred (D7) |
| Verified examples + templates | Approved SQL reused; literals → typed slots via pglast | Industry "verified queries" (Genie, Cortex); TeCoD (Jivani et al. 2026) related |
| Column profiles (variant) | Cached distinct values per text column | Industry value-sampling practice |
| Script repair | Parse-tree fix of Latin/Devanagari literal vs column mismatch | Own (cycle B1) |

### 6.3 What is not done (vs 2026 industry practice; review of 2026-10-01)
- No **table selection** before generation (every table described each time) — fine for 9–14
  tables, not for large owner databases.
- No **value index** (matching question words to stored values), beyond sampling.
- **Single SQL candidate** (no multi-candidate generation + selection).
- No **clarifying questions** for ambiguous questions.
- No **cost check** (EXPLAIN) before execution.

---

## 7. Documents and retrieval (RAG)

### 7.1 Two corpora
1. **Sample corpus** (`public.documents`, `public.document_chunks`; tenant `t_capstone_default`):
   18 synthetic ONDC PDFs (4 policies, 4 SOPs, 4 SLA annexures, 6 supplier agreements; 36 chunks)
   + 2,567 Olist per-seller review digests (20,209 chunks) = **20,245 chunks**. Built by the
   operator job `scripts/ingest.py` (role `statesync_ingest`).
2. **Uploads** (`workspace_docs.*`): the account-level library (folders), searched only by
   workspaces that chose the folder.

### 7.2 Ingestion — sample corpus (`scripts/ingest.py`)
- **Layout-aware for tables:** `pdfplumber.find_tables()` → each table becomes exactly one
  Markdown chunk (`is_table=true`); narrative text is taken from *outside* the table bounding
  boxes, so table cells are never duplicated into narrative chunks.
- Narrative chunking: ~512 "tokens" (whitespace words) with 64 overlap.
- Embeddings: Ollama `mxbai-embed-large` (1024-d). Over-length chunks are halved and retried
  (≤4 times). Review digests are pre-split at review boundaries and embedded in batches of 50 via
  `/api/embed` (which silently truncates over-length input — measured; hence pre-bounding).
- Every digest chunk carries its seller id (addressability rebuild, cycle 006).

### 7.3 Ingestion — uploads (`orchestrator/library_api.py`, `parser/server.py`,
`orchestrator/library_ingest.py`, `mcp/tools/library_tools.py`)
1. Orchestrator checks content type (PDF, text, Markdown), streams the body (abort at 20 MiB →
   413), computes SHA-256.
2. Bytes go to the isolated **parser** (§12.6) over `parser_net`; it re-checks size, parses in a
   limited child process; refusals: too_large, unsupported_type, too_many_pages (>300), encrypted,
   unreadable, timeout, no_text, limits_unavailable.
3. `start_document` (ownership, ≤500 docs/account, duplicate SHA-256 in folder) → `processing`.
4. Background job chunks the text and calls `add_chunks` in batches of 24; the MCP embeds each
   batch with one Ollama call (~0.3 s per chunk on CPU; 88 chunks in 28 s).
5. `finish_document` → `ready` (or `failed`, chunks deleted). Stale `processing` > 30 min shows as failed.

Chunker today (`chunker="paragraph"`): 1,400 characters, 200 overlap, ≥40 chars, never across
pages, paragraph-packed, sentence-aware splits. **RAG v2** (§7.6) adds layout-aware parsing and
structure-aware chunking.

### 7.4 Retrieval (`mcp/tools/vector_tools.py`)
- **Dense, exact search.** Cosine distance (`<=>`) with index scans switched off for the
  transaction (`retrieval.exact_vector_search=true`). Reason (D-v3-2, 2026-09-26): the HNSW index
  was silently approximate — for an LP003 question the exact top match (cosine 0.77) was absent from
  HNSW's top 5; raising `ef_search` found it at 100 and 200 and lost it at 400. Exact scan over 20,245
  chunks: 71 ms (HNSW 2.6 ms).
- **Workspace uploads** are searched in the same call (`X-Workspace-ID`), restricted to ready
  documents in the workspace's folders, merged with the sample corpus by cosine score.
- **Two-round expansion** (orchestrator): find relevant documents, then pull all their chunks.
- Router sees up to 20 ready document titles of the workspace.
- **Citations** built in code: kind `chunk`, chunk id, page, title (uploads).
- **Fused SQL+vector** (hybrid route): `vector.hybrid_select` executes SQL that may call
  `ss_embed('<text>')`; the MCP embeds the text and binds the vector in a transaction-local setting;
  `ss_embed()` (migration 004) is a STABLE function that reads it and makes no network call.

### 7.5 Measured retrieval quality
- **2026-10-01 small benchmark** (`docs/eval/retrieval_2026-10-01.md`; 35 labelled questions):
  dense MRR 0.410; ID probes 0/20; RRF hybrid with Postgres `ts_rank_cd` keyword leg MRR 0.499,
  chunk recall@8 76.7% → 86.7%; mxbai query instruction did not help (e1 hit@1 93.3% → 80.0%).
- **RAG v2 frozen test set** (`fixtures/golden/retrieval_v2.json`, 110 questions, SHA-256
  `652f11dd…cf46c`; baseline `docs/eval/retrieval_v2_baseline.json`, 2026-10-01): overall hit@1
  60.0%, hit@5 77.0%, hit@10 79.0%, MRR@10 0.670; sample corpus hit@5 69.3%, uploads 100%;
  ID questions hit@5 30% (Olist seller-ID probes 0/20); tables hit@5 93.9%; paraphrase 100%;
  unanswerable vs answerable top-1 cosine overlap (0.565–0.732 vs 0.549–0.870); p50 145 ms.
- Older M6 metrics (cycles 003–006) were measured under the approximate HNSW index and are **not
  comparable** (§20).

### 7.6 RAG v2 (in progress; spec `docs/specs/rag_v2.md`)
Phases, each measured against the frozen test set: (A) test set + harness [done]; (B) layout-aware
parsing of uploads with pdfplumber — headings (font size/bold/numbering), paragraphs, lists, whole
tables; (C) structure-aware chunking — sections never mixed, pages never mixed, tables whole (large
ones split by row groups with the header repeated), the sentence introducing a table kept with it,
breadcrumb prefix "Title › Section › Subsection", ~150-char overlap only within a section;
(D) hybrid retrieval — BM25 computed in SQL over `tsvector` (Robertson & Zaragoza) fused with dense
by Reciprocal Rank Fusion (Cormack et al. 2009), no database rebuild; (E) documents scoped by
dataset and model-free abstention ("I couldn't find this in the documents…"); (F) measured
comparison and report. Out of scope (need approved downloads): re-ranker model, new embedding model
(e.g. Qwen3-Embedding-0.6B), Docling/OCR. Status and results: see §20.8 when Phase F completes.

---

## 8. The trust layer

### 8.1 Evidence tiers (`orchestrator/evidence.py`)
A pure function of what actually ran (no model):

| Tier | Meaning | Condition |
|---|---|---|
| **Verified** | An owner-approved query ran as is | exact match of an approved example |
| **Compiled** | An approved query's template, values filled in code | `pipeline.templates="on"` and a template matched |
| **Checked** | New SQL that passed the deterministic checks | `pipeline.answer_checks="on"` and no caveat |
| **Unchecked** | New SQL with a caveat, or checks off | otherwise (with SQL) |
| (none) | No SQL ran (documents-only or failure) | — |

Shipped flags: `answer_checks="off"`, `templates="off"` → in the default configuration only Verified
and Unchecked occur (deliberate until measured; D-v3-16).

### 8.2 Deterministic answer checks (`orchestrator/answer_checks.py`)
- **Join fan-out probe:** for SUM/AVG over a join, compare `COUNT(key)` with `COUNT(DISTINCT key)` on
  the joined rows (key = a single-column key found by suffix rules); a mismatch means rows were
  repeated → one corrective retry (`join_cardinality`), then a caveat. **Gap (open):** COUNT(*) fan-out
  is not covered (a baseline miss, mt_03_t2, was exactly this).
- **Truncation:** the result hit the row cap → caveat.
- **NULL-heavy aggregate:** aggregates over mostly-NULL columns → caveat.

### 8.3 Figure grounding (`orchestrator/number_grounding.py`; flag `pipeline.number_grounding`, shipped "on")
Every number in the prose is matched, **within the rounding its own digits imply** ("1.2M" covers
1.15M–1.25M; "45%" matches 0.4461), to: a result **cell** (a fraction written as a percent counts);
part of a **text** value ("Seller Store 28"); a column **aggregate** (sum/avg/min/max) or the row
count; a number in the **question**; a number in a cited **passage**; or **derived** arithmetic
(ratio/difference) of two figures already traced. Excluded: years, dates (incl. Unicode hyphens),
times, identifiers (S001, order_12), numbered-list markers. Unmatched figures → a caveat ("The
explanation mentions figures not found in the result (…)"), which also keeps the answer out of the
Checked tier. The UI marks each figure (dotted underline; source on hover; unmatched in the warning
colour; "All N figures in this answer match the result").
**Pilot** (2026-09-30, no model calls, 944 stored answers): first run 91.8% of 2,024 figures traced,
29 answers flagged — 21 were false alarms from two checker defects (numbers inside text values;
Unicode-hyphen dates), fixed; after: 97.5% of 1,939 traced, 8 answers flagged (0.85%), all genuinely
unsupported by their own result (6 follow-ups repeating earlier-turn figures, 1 hybrid quoting
documents, 1 non-answer). **In-sample** (defects found on the same data).

### 8.4 "How I got this" (`orchestrator/sql_explain.py`)
Deterministic plain-English reading of the executed SQL from its parse tree (tables, filters,
grouping, ordering, limit). No model.

### 8.5 Self-testing approved answers (`workspaces.manage check_examples`, migration 023)
Approved queries are re-run with no model (`max_rows=1`) and their **result shape** (column names +
whether rows return) compared with the shape recorded when first checked: ok / changed / failed.
Triggered when a workspace is opened and the last check is older than 24 h, or on demand (no
background scheduler — **open**: the plan said "on a schedule").

### 8.6 Quote-verified definitions (`orchestrator/doc_definitions.py`, migration 025)
Passages are pre-selected in code by definitional wording ("means", "is defined as", "Term: …",
"X is … when …") within a 24,000-character budget; the model must return, per term, one sentence
quoted exactly; a proposal is kept only if the normalised quote occurs in the cited passage **and**
contains the term; others are dropped and counted. Kept items are drafts (source `document`) with a
citation (title, page, quote); the owner's own definitions are never replaced. Live: 4/4 and 7/7
definitions found, 0 invented, 1 model call each.

---

## 9. Workspaces and the knowledge store

Code: `mcp/tools/workspace_tools.py`, `orchestrator/workspaces_api.py`, `workspace_setup.py`,
`workspace_context.py`. Tables in `session_state` (migration 021+), all with RLS.

1. **Create:** name + dataset (built-in or own connection) + optional business description (≤4,000
   chars; each save is a version in `business_context_versions`).
2. **Scan:** `list_tables` + `describe_table` for ≤60 tables; sample values for text columns (≤5 per
   column; sampling capped at 20 rows by the MCP).
3. **AI drafts** (`decomposer` role, batches of 6,000 chars of catalog): descriptions + definitions;
   names not in the catalog are dropped; drafts never replace owner text.
4. **Approve:** marks all current items approved; any edit un-approves that item. Only approved
   knowledge reaches questions.
5. **At question time** (`context` action): the business description is appended to the SQL system
   prompt, labelled as the owner's words / data not instructions; approved descriptions (≤6,000
   chars), up to 12 definitions whose term appears (whole word) in the question, up to 3 approved
   examples by word-overlap similarity ≥0.3; and the exact match for the fast path.
6. **Setup test:** 10 questions. On `olist` and `public`, curated questions with reference SQL graded
   by execution accuracy; elsewhere, model-written questions graded by the owner. Right answers become
   approved examples (with templates). Pass bar shown in the UI: 8/10 ("Ready to use").
7. **Approved examples** come from the setup test or a thumbs up; thumbs down on a Verified answer
   withdraws it.
8. **Templates** (`mcp/tools/sql_templates.py`, `orchestrator/templates.py`, migration 022):
   derived when an example is added — literals on one side of a comparison, IN-lists, BETWEEN bounds
   and the LIMIT become typed slots (integer/number/date/timestamp/text); a later question is matched
   by one short call that returns template + values; values are validated per type, rendered as quoted
   literals in one pass, and the result must parse as exactly one SELECT, then goes through the gate.
9. **Documents tab:** the workspace chooses library folders to search.

---

## 10. Dashboards, tiles and live data

Code: `mcp/tools/tile_tools.py`, `mcp/tools/dashboard_tools.py`, `orchestrator/tiles_api.py`,
`dashboards_api.py`, `dashboard_builder.py`, `builder_api.py`; migrations 017, 019.

- **Pin:** from a stored turn the user owns (session + turn index) — never raw SQL from the client.
  Stores version 1: SQL, chart template (data removed), columns, and a **fingerprint of the columns
  the SQL reads** (name, type, nullability per table).
- **Refresh:** re-runs the saved SQL when stale, **no model call**; default interval 3,600 s
  (15 s–7 days); `live` tiles 30 s. Failed refresh keeps the last good result and shows the error.
- **Schema drift:** fingerprint mismatch → `needs_review`; only a person's **confirm** creates a new
  version. Replace = new version from a new answer; versions are kept.
- **Insights** (KPI change, leading share) are computed in the browser from the rows; no model writes
  a number.
- **Build with AI:** a `decomposer` call plans 4–8 insights (title, question, chart kind) from a
  schema summary; each question runs through the real pipeline and is pinned (~20–40 model calls).
- **Simulator** (`simulator/`): every 5 s, generates orders and web traffic for an Indian online store
  (demand by hour in IST, weekend uplift; seed 20260927) and applies planned lifecycle steps (pack,
  ship, deliver on time/late, cancel, return, review); 30-day backfill, 35-day retention; role writes
  `live`/`live_sim` only.
- Quotas: 60 tiles/principal, 24/dashboard, 24 dashboards; 12-column grid.

---

## 11. Connecting your own Postgres

Code: `mcp/connections.py`, `mcp/tools/library_tools.py` (connection actions), migration 026,
`scripts/verify_connections.py`.

- A connection's id `cn_<16 hex>` **is its dataset name**, so every existing path (questions, scan,
  tiles, dashboards, example checks) reaches the right database without new plumbing.
- **Saved only if the login is provably read-only:** refused if superuser / createrole / createdb /
  replication / bypassrls; a member of `pg_write_all_data`, `pg_write_server_files`,
  `pg_read_server_files`, `pg_execute_server_program`; or holding INSERT/UPDATE/DELETE/TRUNCATE on any
  non-system table. Warns if it can CREATE in a schema.
- **Password** encrypted with Fernet (key `secrets/connection_key`, MCP only; made by
  `scripts/make_connection_key.py`); never returned or logged.
- **Never a target:** StateSync's own service names and anything they resolve to; loopback,
  link-local, multicast, unspecified. The checked address is the one connected to (`hostaddr`), so DNS
  cannot change in between. Local databases: `host.docker.internal`.
- Sessions forced `default_transaction_read_only`, 15 s statement and idle timeouts; each use is a
  READ ONLY transaction, rolled back.
- **One database per query:** SQL may name `cn_….table` or unqualified tables (search_path = the
  chosen schema); the qualifier is replaced by the real schema at **parse-tree positions** (never in
  string literals); any other schema, a second connection, or a function qualified with the
  connection is refused; the mapped SQL is gated again. SQL for a built-in dataset may not name a
  connection.
- Only through the caller's own workspace; per-principal ownership checks; tenant-scoped resolution.
- Dataset kind (`orchestrator/datasets.py`): own databases get neutral prompt framing (not "ONDC"),
  no DIGIPIN handling, and no router call when the workspace has no uploads.
- Live: 24/24 checks against a throwaway Postgres (2026-09-29); one real question answered correctly.

---

## 12. Security model

### 12.1 Threats → controls → proof
| Threat | Control | Verified by |
|---|---|---|
| Model side reaches the DB / holds credentials | Network separation; no driver; secrets only in MCP | `scripts/verify_b.sh` B-V3, B-V4 |
| Model-written writes / multi-statement / data-modifying CTE | Layer 1 pglast gate; Layer 2 READ ONLY rolled-back txn; Layer 3 read-only role | B-V5 (7/7 writes → SQLSTATE 42501), B-V6; `scripts/verify_security.sh` |
| Prompt/tool poisoning via DB text or documents | Data labelled as data; no tool surface for the model; call-log scans | `verify_security.sh` (adversarial suite, 43/44; `adv_14_strict` fails by design: a fabricated DSN echoed in a debug field) |
| Executable output / XSS | Declarative Vega-Lite only; A7 validated twice (server + browser); CSP without `unsafe-eval`; vega-interpreter; ESLint bans HTML sinks | `scripts/verify_e2.py`, `web/e2e/*/chart-safety|security.spec.ts`, `tests/test_nginx_header_checks.py` |
| Cross-tenant / cross-user reads | Tenant bound as an RLS setting per transaction (fails closed); principal filters on every app query; session ownership | `scripts/verify_workspaces.py`, `tests/test_rls_coverage.py`, `verify_tiles.py`, `verify_dashboards.py`, `verify_library.py` |
| Hostile upload | Isolated parser (§12.6) | `tests/test_parser.py`, probes (bomb, 400-page, garbage) |
| Owner-DB misuse | Read-only proof, host blocking, query mapping | `scripts/verify_connections.py` 24/24 |
| Denial of wallet | Per-principal rate limits (30 model req/min, 300 other); one active background job per principal | `tests/test_rate_limits.py` |
| Stale deployment | Source-parity hashes; web build-info | `scripts/verify_deployed.py` |

### 12.2 The three-layer SQL gate (`mcp/tools/data_tools.py`, `mcp/db.py`)
- **Layer 1 (parser):** ≤8,000 chars; exactly one statement; a `SelectStmt`; no data-modifying
  statements anywhere incl. inside CTEs; must read a real table. (`SELECT … INTO` / `FOR UPDATE` are not
  rejected here but fail at Layer 2 with SQLSTATE 25006.)
- **Layer 2 (session):** every request `BEGIN READ ONLY` … `ROLLBACK`; 15 s statement timeout; 60 s
  idle-in-transaction timeout; tenant bound for RLS.
- **Layer 3 (role):** the reader role has SELECT only — the actual guarantee.
- **Function denylist** (file I/O, admin, `pg_sleep`, `dblink`, `set_config`, …): in Layer 1, so every
  model-SQL path (run_select, hybrid_select, tile refresh, examples, templates) is covered
  **(fixed 2026-10-01; previously hybrid_select only)**.
- **Internal schemas:** SQL for a built-in dataset may not name `session_state`, `workspace_docs`,
  `search`, `live_sim`, `pg_catalog`, `information_schema`, `pg_toast` (`[mcp].internal_schemas`)
  **(fixed 2026-10-01)**; owner databases are exempt (their schemas may have any name).

### 12.3 Tenancy and RLS
Every table in `session_state` and `workspace_docs` has RLS with policy
`tenant_id = current_setting('statesync.tenant_id', true)`; unset → no rows (fails closed).
`tests/test_rls_coverage.py` fails if a migration adds a table without RLS. The business datasets
(`public`, `olist`, `live`) are shared across tenants (the A5 fingerprint pins `public`'s shape).

### 12.4 Authentication
Bearer tokens; only SHA-256 hashes stored (`secrets/api_principals.json`), compared constant-time;
startup fails closed if the file is missing/malformed. Principals: `p_dev_local`, `p_dev_second`
(tenant `t_capstone_default`), `p_demo` (tenant `t_demo`).

### 12.5 Known security findings (review of 2026-10-01)
1. **MCP unauthenticated and published on all interfaces** in the dev override (`0.0.0.0:8080`);
   it trusts the `X-Tenant-ID` header → LAN exposure on a laptop. **(fixed 2026-10-01: dev ports bound
   to 127.0.0.1; Ollama's container port no longer published; verified unreachable from the LAN
   address.)** The MCP itself still has no authentication — it relies on not being reachable.
2. **Model SQL could read `workspace_docs`** (the reader role needs it for retrieval; RLS is per tenant,
   not per user). **(fixed 2026-10-01: internal-schema rule above; `tests/test_sql_gate_hardening.py`.)**
3. Function denylist only on `hybrid_select`. **(fixed 2026-10-01.)**
4. Owner-DB password transits the orchestrator in memory on create (not stored/logged); private
   ranges allowed, so connection errors could act as a scan oracle for an authenticated user.
5. Postgres runs with `log_statement=all` (needed by B-V2): user questions/answers appear in the
   container log.

### 12.6 Parser isolation (`parser/server.py`, compose)
Internal network shared only with the orchestrator; no secrets; non-root; read-only filesystem;
`cap_drop: ALL`; `no-new-privileges`; 1 GB / 64 processes; each PDF parsed in a fresh spawned child
with 768 MB address space, 60 s CPU, 60 s wall clock; fails closed if limits cannot be set. Probes:
3 GB compression bomb refused in 0.1 s; memory-starved child detected in 0.4 s.

---

## 13. Data model

### 13.1 Schemas
| Schema | Content | Notes |
|---|---|---|
| `public` | Synthetic ONDC corpus: network_participants 45, locations 51 (PostGIS geography), sellers 30, logistics_providers 10, catalog_items 100, orders 550, order_items 1,127, fulfillments 550, delivery_events 1,917, disputes 80; documents 2,585; document_chunks 20,245; schema_versions | Shape pinned by the A5 fingerprint (`mcp/fingerprint.py`: tables, columns, types, nullability, FKs; indexes/comments excluded); mismatch at startup is fatal |
| `olist` | Real e-commerce, 9 tables, 1,551,698 rows, no declared keys (on purpose) | Migration 007; CC BY-NC-SA 4.0 |
| `live` / `live_sim` | Simulated orders 172k+, deliveries, reviews, web_traffic (5-min buckets) / simulator plans | Migration 020; reader cannot read `live_sim` |
| `session_state` | 16+ app tables (below), RLS | Migrations 011–026 |
| `workspace_docs` | Library folders, documents, chunks (vector(1024)), workspace↔folder links, RLS | Migrations 021, 024 (+027 in RAG v2) |
| `search` | (RAG v2) `sample_terms` tsvector side table for BM25 | Migration 027 |

### 13.2 Application tables (`session_state`)
`conversation_turns` (011/012/014/018), `turn_rows` (015), `audit_log` (013/016), `tiles`,
`tile_versions`, `tile_results` (017), `dashboards` (019), `workspaces`,
`business_context_versions`, `kb_columns`, `kb_definitions` (+citations, 025), `kb_examples`
(+self-check columns, 023), `setup_runs`, `setup_results` (021), `kb_templates` (022),
`connections` (026).

### 13.3 Migrations (one line each)
001 roles · 002 schema · 003 fingerprint placeholder · 004 `ss_embed` · 005/006 column comments and
revert (−19 points on the 7B) · 007 olist · 008/009 join graph and revert (−11.1pp) · 010
review_digest type · 011 conversation history · 012–016 history/auth/audit extensions · 017 tiles ·
018 turn dataset · 019 dashboards · 020 live schema · 021 workspaces + RLS · 022 templates · 023
example checks · 024 library · 025 definition citations · 026 connections · (027 RAG v2). Migrations
from 005 are applied by hand; `scripts/migrate.sh` runs only 001/002/004.

---

## 14. Frontend (`web/`)

- **Stack:** React 19, TypeScript 5.8, Vite 7, Tailwind 4, shadcn/ui (Radix), TanStack Query 5,
  Vega 5 / Vega-Lite 5 / vega-embed / **vega-interpreter**, react-grid-layout, motion, sonner, lucide.
  Hash routing (no router library): `#/dashboards`, `#/workspaces[/id]`, `#/library`, `#/ask`.
- **Design contract:** `web/DESIGN.md` + the mocked Playwright suites (`web/e2e/mocked/*.spec.ts`,
  whose mocks mirror server rules).
- **Security rules (enforced, not advised):** CSP at server level only (`script-src 'self'`, no
  `unsafe-eval`, `connect-src` = orchestrator origin, `frame-ancestors 'none'`); charts drawn only
  through `src/charts/VegaChart.tsx`, which validates A7 first and renders with `ast:true` +
  `vega-interpreter` + `tooltip:false` (vega-tooltip writes innerHTML); ESLint fails the build on
  `innerHTML`, `dangerouslySetInnerHTML`, `insertAdjacentHTML`, `document.write`, `eval`, `Function`;
  self-hosted fonts (CSP `font-src 'self'`). Runtime config `/config.js` written by
  `web/entrypoint.sh` (validated URL); build parity via `/build-info.json`.
- **Presentation decided in the browser** (D-v3-8): chart kind, sort and labels come from the rows
  (`src/viz/*`, unit-tested); the server's chart proposal is only a hint, so the measured chart
  score (M7) is not moved by UI choices.
- **Tests:** vitest unit tests (60+), mocked Playwright (~150), live Playwright (9).

---

## 15. Models and the LLM provider layer

### 15.1 Provider seam (`orchestrator/llm/provider.py`, `llm/adapters/*`)
`get_agent_llm(role)` returns a model for one of 8 roles from `config/flags.toml`
`[llm.models.<provider>]`; providers: `local` (Ollama), `anthropic`, `azure`, `gemini`, **`groq`
(shipped since 2026-09-26, D-v3-3)**. Every model is wrapped in `RecordingModel`
(`llm/recording.py`) which records per call: role, model, tokens in/out, cached tokens, retries,
waited ms, latency, remaining rate-limit tokens. Temperature 0.0 everywhere.

### 15.2 Roles → models (Groq, shipped)
| Role | Model | Used for |
|---|---|---|
| supervisor | qwen/qwen3.8-27b | routing (128 tokens) |
| normalizer | qwen/qwen3.8-27b | language gate (512) |
| rewriter | qwen/qwen3.8-27b | follow-up rewrite (512) |
| decomposer | qwen/qwen3.8-27b | hybrid split, dashboard planner, workspace drafts, setup questions, template matcher, definition mining |
| memory_summarizer | qwen/qwen3.8-27b | trajectory insight (memory off) |
| sql | openai/gpt-oss-120b | SQL draft + generation (1,024 + 1,024 reasoning headroom; `reasoning_effort="low"`) |
| synthesizer | openai/gpt-oss-120b | answer prose + chart proposal |
| judge | openai/gpt-oss-120b | `scripts/bench_grounding.py` only |

**Why this split** (`flags.toml`): each Groq model has its own budget (free tier: 30 requests/min,
8K tokens/min, 200K tokens/day per model, ~55 questions/day); the two roles whose output users read
(SQL, answer) get the strongest model, short classification roles a fast one — two budgets instead
of one. Groq adapter: waits out 429s using the server's retry-after (≤60 s per wait, ≤6 retries,
≤420 s total), SDK retries off.

**Prompt caching:** automatic on gpt-oss; in the baseline 13,568 of 95,169 gpt-oss input tokens were
cached (~14%; `docs/eval/groq_baseline_2026-09-29.json`). No caching code of our own.

### 15.3 Model history
2026-08-13 seam (BD1), local `qwen2.5:7b` default; Anthropic mapping never run (no key);
2026-08-17 Azure blocked (student subscription), Gemini free tier adopted for probes (+20pp paired,
n=15); larger local models rejected: 14B general +0.0pp, coder-14B −8.0pp, all-14B −8pp and ~3x
slower; 2026-09-26 Groq adopted after the configured local model turned out not installed.
**Every accuracy number before 2026-09-29 is on other models** and is not comparable to Groq.

### 15.4 Embeddings
`mxbai-embed-large` (Mixedbread; 1024-d; ~512-token context) on the Ollama container, called only by
the MCP. Chosen because the container and model were already provisioned (BD2); no alternative was
measured. Changing it requires re-embedding all chunks. MTEB retrieval average 54.39 (model card);
Qwen3-Embedding-0.6B 61.83 on MTEB English v2 (different benchmark version) is a candidate (RAG v2 follow-up).

---

## 16. Frameworks and libraries: what and why

| Component | Version | What it does here | Why (repo evidence) |
|---|---|---|---|
| **FastAPI** + uvicorn | 0.14x / 0.52 | HTTP for orchestrator, MCP, evaluator | Not stated; standard async Python API stack |
| **LangGraph** | 1.2.x | The question graph: typed state, reducers, conditional edges, correction loop | Strategy D3: "typed shared state with reducer-governed merges, conditional edges, and a cycle for self-correction — exactly what the framework provides"; hand-rolling would be "a side quest". `langchain-core` is only a transitive dependency (never imported) |
| **Pydantic** | 2.x | Typed state (A2), output models | Spec A2 |
| **httpx** | 0.28 | Orchestrator→MCP, →parser, MCP→Ollama | — |
| **psycopg 3** + psycopg-pool | 3.3 | Async pools (reader; history writer) in the MCP only | — |
| **pglast** | 8.4 (GPL-3.0+) | Real PostgreSQL parser: Layer-1 gate, template slots, fan-out keys, "How I got this", connection SQL mapping | A4.5: "a real PostgreSQL parser, not a regex … the data-modifying-CTE case is what a regex gate misses" |
| **Postgres 16** | 16.4, digest-pinned | All data and app state | Pinned after a glibc collation incident (V3 §0.5) |
| **pgvector** | 0.8.5 | Embeddings, exact cosine search, `ss_embed` | One database, one isolation model; adequate below ~10M vectors |
| **PostGIS** | 3.4.3 | DIGIPIN radius filters (`ST_DWithin`) | Spatial questions on the ONDC corpus |
| **Qdrant** | 1.9.4 | Trajectory memory only (off) | Kept until measured (D-v3-13); planned retirement into pgvector |
| **Ollama** | 0.32.x | Local embeddings | Nothing leaves the machine; already provisioned (BD2) |
| **Groq** (OpenAI-compatible API via `openai` SDK) | — | Hosted LLMs | Free tier, fast; D-v3-3 |
| anthropic / google-genai | — | Alternative adapters (lazy) | BD1 provider seam |
| **jsonschema** | 4.x | A7 synthesizer contract (Draft 7) | "Schema, not convention" (A7) |
| **digipin** | 2.1.1 | India Post DIGIPIN encode/decode | Spec: do not hand-implement the grid |
| **pypdf** | 6.19 | Upload text extraction (parser) | D-v3-11: Docling ~2 GB of models; PyMuPDF is AGPL |
| **pdfplumber** | 0.11.10 | Table-aware parsing (sample corpus; uploads from RAG v2) | `find_tables` + `outside_bbox` keep tables whole and unduplicated |
| **cryptography** (Fernet) | 50.x | Encrypts owner-DB passwords | Key held by the MCP only |
| vl-convert-python | 1.9 | Vega-Lite → PNG for a half-built vision scorer | "Pure Rust wheel: no browser, no network" |
| APScheduler + SQLite | — | Evaluator schedule and history | BD8: evaluator must not need a Postgres write grant |
| reportlab | 5.0 | Generates synthetic and demo PDFs | — |
| React / Vite / Tailwind / shadcn / TanStack Query / Vega-Lite | see §14 | Web app | V3 §6: replace a plain-JS UI with a designed product; keep Vega-Lite |
| nginx | 1.27 | Serves the SPA with security headers | Headers at server level (inheritance trap, audit 005) |
| pytest, vitest, Playwright, ruff | — | Tests | — |

**"MCP" is a pattern, not the SDK:** `mcp/main.py` is a hand-written JSON-RPC 2.0 server (no
`tools/list` handshake); the name reflects the Model Context Protocol idea of a tool server that holds
the data access. Tool ceiling 20 (all used).

**Alternatives rejected** (with reasons): Docling (size), PyMuPDF (AGPL), LangChain SQL agent (relies on
DB credentials, "may attempt DROP TABLE"), hand-rolled state machine (D3), CSR-RAG hypergraph (no
problem to solve at 9–14 tables; trigger pre-committed), Pyodide/WASM chart sandboxes (declarative
charts chosen; no execution path), cookies (need HTTPS cross-origin; bearer tokens instead), Postgres
for evaluator history (BD8), trajectory memory on (10/10 → 7/10), Olist join graph (−11.1pp), schema
comments in prompt (−19 points on 7B), larger local models (no gain), mxbai query instruction (no gain),
SQLite backend for BIRD (loses the role layer).

**Version hygiene (open):** dependencies are floating `>=` ranges resolved at build; `uv.lock` files are
stale and unused; base images `python:3.12-slim`, `uv:latest`, `ollama/ollama:latest` float.

---

## 17. Research lineage: papers, benchmarks and what was taken

Status key — **Implemented**: code follows the idea · **Evaluated against**: used as a metric or
benchmark · **Motivation**: cited to justify a design · **Seam**: interface only · **Planned**.
All citations verified 2026-10-01.

### 17.1 Papers the architecture draws on (Strategy appendix, 15 sources)
| Paper | Status | What StateSync took |
|---|---|---|
| Biswal, Lei, Qin, Li, Narayanaswamy, Kraska. *AgentSM: Semantic Memory for Agentic Text-to-SQL.* arXiv:2601.15709 (2026) | Implemented, then off | Abstracted-insight trajectory memory keyed by tenant + schema fingerprint, validated on read; never stores SQL. Measured −39.2% tokens, −73.1% steps, but consistency failed (M2) and accuracy fell 10/10 → 7/10 → shipped off |
| Cao, Liao, Sun, Fang, Li, Lam. *APEX-SQL.* arXiv:2602.16720 (KDD 2026) | Implemented | Active interrogation: sample the draft's tables before final generation (≤4 tables, ≤20 rows) |
| Chaturvedi, Chadha, Bindschaedler. *SQL-of-Thought: Multi-agentic Text-to-SQL with Guided Error Correction.* arXiv:2509.00581 (2025) | Implemented (simplified) | Error taxonomy + bounded correction loop; deterministic classifier instead of the paper's LLM correction-plan agent. (Older docs cite "Chadha et al." — first author is Chaturvedi) |
| Pham et al. *Multilingual Text-to-SQL: Benchmarking the Limits of Language Models with Collaborative Language Agents (COLA).* arXiv:2509.24405 (2025) | Implemented (single-prompt gate) | Normalise language first, preserving entities byte-identical |
| Dawar, Karanth, Goyal, Kumar. *IndicDB.* arXiv:2604.13686 (2026) | Motivation | The "Indic gap" (~9% EX drop English→Indic) |
| Kattamuri et al. *Bridging the Semantic Gap: Contrastive Rewards for Multilingual Text-to-SQL with GRPO.* arXiv:2510.13827 (2025) | Seam only | A fine-tuned normaliser (not trained). **Older docs misquote it as "+27.43pp"; the abstract gives +26pp EX over zero-shot and +7.43pp over a zero-shot 8B model** |
| Rodrigues, Vas. *MCP Server Architecture Patterns for LLM-Integrated Applications.* arXiv:2606.30317 (2026) | Motivation; not reproduced | The 10–15 tool ceiling; StateSync's over-exposure experiment found no degradation (the router never sees MCP tools); ceiling kept to curb API sprawl (raised to 20). (Older docs: "Anonymous, 2026") |
| Maloyan, Namiot. *Breaking the Protocol.* arXiv:2601.17549 (2026) | Motivation | MCP is not secure by default → defence in depth (D9) |
| Wang, Li, Yao, Zhang. *Text2VectorSQL.* arXiv:2506.23071 (2025) | Implemented | Vector search inside SQL: `ss_embed()` + `vector.hybrid_select` (fused SQL+vector query on the hybrid route); their trained models not used |
| Marketsmüller, Martin, Schlippe. *Evaluating RAG Variants for NL-Based SQL and API Call Generation.* arXiv:2602.07086 (2026) | Partly implemented | Decomposition gated to the hybrid route only (single decomposer call) |
| Singh et al. *CSR-RAG: An Efficient Retrieval System for Text-to-SQL on the Enterprise Scale.* arXiv:2601.06564 (2026) | Seam only (D7) | Schema retrieval deferred with a pre-committed trigger (schema >8,000 tokens or EX −10pp) |
| Yu, Jian, Chen. *TableRAG: A RAG Framework for Heterogeneous Document Reasoning.* arXiv:2506.10380 (EMNLP 2025) | Partial | Tables kept whole as Markdown chunks; SQL-over-document-tables not built |
| Jiao et al. *Text2DB.* ACL Findings 2024 | Influence only | — |
| Ling Wang et al. *HEAR: Hypergraph Enterprise Agentic Reasoner.* arXiv:2605.14259 (2026) | Seam only | Route-registry slot "rca"; no user need |
| Hostnik, Kurbanov, Sokolov, Trofimov. *VegaChat.* arXiv:2601.15385 (2026) | Half implemented | Deterministic recall-weighted Spec Score for charts (mark/encoding/transform 0.40/0.45/0.15; recall 0.70); vision score rendering only |

### 17.2 Benchmarks, metrics and evaluation sources
| Source | Status | Use |
|---|---|---|
| Li et al. *BIRD.* NeurIPS 2023, arXiv:2305.03111 | Evaluated against (metric) | Execution accuracy definition (`ExecutionAccuracyScorer`) |
| Yu et al. *Spider.* arXiv:1809.08887 (2018) | Evaluated against (metric) | Same; BIRD/Spider never run as benchmarks (`docs/benchmark_readiness.md`) |
| BIRD Mini-Dev (500 q, 11 DBs, CC BY-SA 4.0) | Planned | Study dataset (§23) |
| Jin, Choi, Zhu, Kang. *Pervasive Annotation Errors Break Text-to-SQL Benchmarks and Leaderboards.* arXiv:2601.08778 (2026) | Planned | Corrected gold (52.8% of BIRD Mini-Dev, 62.8% of Spider 2.0-Snow had errors) |
| Lei et al. *Spider 2.0.* ICLR 2025, arXiv:2411.07763 | Cited | Enterprise realism (o1-preview agent 21.3%) |
| Richardson. *What Predicts Correctness in Text-to-SQL? A Selective-Prediction Study.* arXiv:2607.06799 (2026) | Planned baseline | Model-based confidence (judges 0.72–0.78 AUROC, ensemble 0.82) |
| Jivani, Maheshwari, Sarawagi. *TeCoD: Template Constrained Decoding.* arXiv:2604.28028 (2026) | Related work | Closest to the Compiled tier (not compared) |
| Wang et al. *Self-Consistency.* ICLR 2023, arXiv:2203.11171 | Planned baseline | k=5 execution-agreement |
| Yu et al. *SParC* (arXiv:1906.02285), *CoSQL* (arXiv:1909.05378) | Evaluated against (metrics) | Multi-turn QM / IM metrics (`scripts/bench_multiturn.py`) |
| Es et al. *RAGAS.* arXiv:2309.15217 (2023) | Evaluated in its sense | M6 context precision / faithfulness, implemented in-house |
| Cormack, Clarke, Büttcher. *Reciprocal Rank Fusion.* SIGIR 2009 | Prototype → RAG v2 | Hybrid fusion (k=60) |
| Robertson, Zaragoza. *The Probabilistic Relevance Framework: BM25 and Beyond.* FnTIR 2009 | RAG v2 | BM25 keyword leg in SQL (k1=1.2, b=0.75) |
| Satyanarayan, Moritz, Wongsuphasawat, Heer. *Vega-Lite.* IEEE TVCG 2017 | Foundation | Declarative charts → A7 contract |
| Anthropic, *Contextual Retrieval* (2024 blog) | RAG v2 motivation | Breadcrumb/context prefix on chunks (misses −35% in their study) |

### 17.3 Own techniques (no external source)
Three-layer SELECT-only gate; A7 contract + expression audit (BD7); evidence tiers; join fan-out probe;
figure grounding; quote-verified definition mining; templates from approved SQL; schema fingerprint;
RLS coverage test (motivated by CVE-2025-48757: missing RLS at scale); isolated upload parser;
read-only login proof and parse-tree query mapping for owner databases; shipped-flags guard;
deployment parity checks.

---

## 18. Datasets

| Dataset | Source | Size | Licence | Used for |
|---|---|---|---|---|
| **olist** | Kaggle `olistbr/brazilian-ecommerce` via the `mara/mara-olist-ecommerce-data` mirror; SHA-256 verified (`fixtures/olist_checksums.txt`) | 9 tables, **1,551,698 rows** (older docs say 1,451,414 — wrong) | CC BY-NC-SA 4.0 (non-commercial) | Real-data accuracy, demos, review digests |
| **public** (synthetic ONDC) | `scripts/gen_seed_fixture.py` (seed 20260807), 20 real Indian cities, Devanagari names, DIGIPINs | 550 orders etc. (§13.1) | Own | Default dataset, adversarial suite, A5 fingerprint |
| **live** | `simulator/` (seed 20260927) | ~172k orders and growing | Own | Moving dashboards |
| Document corpus | `scripts/gen_document_corpus.py` (18 PDFs, every table has numbers found nowhere in the DB) + review digests (`scripts/gen_olist_review_digests.py`) | 20,245 chunks | Own / Olist-derived | Retrieval, grounding |
| Demo kit | `demo/make_documents.py` (3 fictional policy PDFs for olist), `demo/local_shop_db.sh` (furniture shop: 64 products, 600 customers, 4,000 orders) | — | Own | Demos |
| Golden sets | `fixtures/golden/*` (see §19.2) | — | Olist-derived carry its licence | Evaluation |
| BIRD Mini-Dev + corrected annotations | GitHub bird-bench / uiuc-kang-lab | 500 q | CC BY-SA 4.0 | Planned study (download needs owner approval) |

Olist data is deliberately left dirty (no keys; 8 delivered orders without delivery date; 775 orders
without items; 611 seller-city spellings; 610 NULL categories; the source's misspelled column
`product_name_lenght` kept verbatim).

---

## 19. Evaluation methodology

### 19.1 Metric map (Strategy §7)
M1 memory efficiency · M2 consistency · M3 multilingual lift (D-V1 accuracy, D-V2 entity
preservation, D-V3 English untouched) · M4 query correctness · M5 self-correction · M6 grounding
(RAG) · M7 chart quality · M8 security (binary) · M9 routing · M10 reproducibility. Plus v3: Groq
baseline, retrieval hit@k/MRR, figure grounding, live verification suites.

### 19.2 Evaluation sets (`fixtures/golden/`)
| Set | n | Composition / gold |
|---|---|---|
| phase_b | 10 | English SQL; row counts frozen |
| phase_c1 | 30 | 10 base, 10 repeats, 10 paraphrases (memory A/B) |
| phase_c2 / phase_c2_v2 | 25 / 25 | Synthetic SQL; 12/25 originals had instrument problems; v2 is the audited copy (original kept) |
| phase_d_multilingual | 40 | 20 Devanagari + 20 Hinglish |
| phase_d_spatial / digipin_reference | 15 / 20 | Radius queries; decode pairs |
| phase_e1 | 25 | 8 docs, 8 hybrid, 9 SQL; expected chunks |
| phase_e2 | 30 | Chart queries with authored reference specs |
| phase_f_routing | 60 | 20 per route |
| olist / olist_hard | 18 / 16 | Real data; ground truth executed at generation |
| multi_turn | 12 conv / 36 turns | Anaphora, ellipsis, refinement; 1 cross-modal, 1 ambiguous |
| review_probe_set | 20 | Seller-ID retrieval probes |
| groq_baseline | 30 | Rule-selected from the above |
| **retrieval_v2** | **110** | RAG v2 frozen set (evidence-text gold; 80 sample + 30 upload; 10 unanswerable) |
| adversarial / malicious_specs / malformed_queries | 21 / 10 / 12 | Security and error-class seeds |

### 19.3 Scorers
- **Execution accuracy (BIRD definition):** rows → tuples of stringified values; column names
  ignored; multiset, order-insensitive; empty vs empty = correct. A stricter variant (values sorted
  within rows + column count) is kept alongside, never replacing it.
- **M3:** D-V2 requires the entity byte-identical in the SQL and self-mapped.
- **M6:** context precision over cited chunks, recall, LLM-judge faithfulness with pinned rubrics.
- **M7:** weighted F over mark/encoding/transform (0.40/0.45/0.15; recall 0.70 / precision 0.30).
- **Multi-turn:** QM (per turn) and IM (whole conversation) always reported together.
- **Retrieval:** evidence-text hit@k / MRR (RAG v2), chunk recall.
- **Figure grounding:** model-free (§8.3).

### 19.4 Measurement discipline
- **Shipped-flags guard** (`scripts/shipped_flags.py`): harnesses refuse to measure off the shipped
  flags unless told to, and record flag provenance; checks the running container started after the
  last flags change (born from three contaminated runs).
- **Deployment parity** (`scripts/verify_deployed.py`): repo files vs files inside the running
  containers; web via `/build-info.json` (born from "committed but never shipped" incidents).
- **Never edit a fixture, scorer or check to move a number**; add a corrected one alongside
  (V2 §9). Paired runs with one variable; predictions written before runs; frozen criteria
  (cycle protocol: SHA-256 of acceptance criteria).
- **Live verification suites** (counts as last run): verify_workspaces 78/78, verify_library 24/24,
  verify_connections 24/24, verify_tiles 36/36, verify_dashboards --build 66/66, seams 26/26,
  E2 46/46, tenancy 12/12, M8 43/44.
- **CI** (`.github/workflows/verify.yml`): literal lint, pytest, seams, provider seams, shipped-flag
  self-test, web lint/build/unit/mocked browser tests; does not run the live stack.

---

## 20. Measured results (complete ledger)

Local results are qwen2.5:7b at temperature 0 unless stated; they are **not comparable** to the Groq
numbers. Sources in brackets.

### 20.1 v3 on Groq (current models)
- **Baseline, 2026-09-29** (`docs/eval/groq_baseline_2026-09-29.json`, commit 9800de1, shipped flags,
  30 questions, 45 s pacing): **25/30 = 83.3%**. SQL 16/18 (olist 5/5, olist_hard 5/5 [all join3],
  synthetic 6/8); documents 5/6; follow-ups 4/6; routing 30/30. Mean 4.7 calls, 4,652 in / 356 out
  tokens; p50 2,632 ms, p90 3,490 ms; 0 retries. gpt-oss 78 calls (13,568 of 95,169 input tokens
  cached), qwen 63 calls.
  Misses: c2g_08 ambiguous denominator (all vs delivered fulfillments); c2g_09 under-specified
  projection (fixture reworded in phase_c2_v2); e1_doc_004 retrieval cited LP003/LP007 instead of LP001;
  mt_02_t2 counted payment rows instead of orders; mt_03_t2 reviews duplicated by a join under COUNT(*).
  Two of the five are known fixture issues.
- Workspace AI drafts: 61/61 olist tables and columns in 5 s; connected shop DB 20/20 in 4.2 s.
- Library ingestion: ~0.3 s/chunk (88 chunks in 28 s); demo PDFs ready in 2.1 s.
- Definitions from documents: 4/4 and 7/7, 0 invented, 1 call, 2.6 s.
- Own-Postgres gate: 24/24 live checks.
- Script language gate: 40/40 multilingual questions caught, 0 English false positives.
- **Never accuracy-measured yet:** lean flags, answer checks, templates (all built, off); per-tier
  accuracy; Phase 3 exit (setup test ≥8/10 on olist and public).

### 20.2 Figure grounding pilot (2026-09-30; `docs/research/paper_facts.md`)
1,268 stored turns → 944 analysable. First run: 2,024 figures, 91.8% traced, 29 flagged (21 false
alarms). After fixes: 1,939 figures, **97.5% traced** (cell 1,399; question 366; aggregate 65; text 61;
unmatched 48), **8 answers flagged (0.85%)**, all genuine. In-sample. (The pilot script was run ad hoc;
**open:** commit it for reproducibility.)

### 20.3 Retrieval
- 2026-10-01 small benchmark (35 q): dense MRR 0.410, hybrid (ts_rank) 0.499; ID probes dense 0/20.
- **RAG v2 baseline** (110 q): hit@1 60.0%, hit@5 77.0%, hit@10 79.0%, MRR@10 0.670; IDs hit@5 30%;
  tables 93.9%; uploads 100%; p50 145 ms.
- Historical (cycles 003–006, under approximate HNSW — not comparable): M6 precision 0.364 over retrieved
  chunks (ceiling 0.778), faithfulness 0.896 (docs-only); seller-ID query ranked the right digest
  2,365th of 13,161 before an addressability rebuild; interference test: 365x more chunks, every metric
  unchanged.

### 20.4 Synthetic SQL (local 7B)
M4 cold 29.2% → after projection prompt EX 44% → corrected fixture strict 52.0%, EX 60.0% (+16.0pp from
fixing ground truths alone). Model matrix on the corrected fixture: 7B 60.0% EX; 14B general 60.0%;
coder-14B 52.0% (and ~3x slower). Gemini vs 7B (15 paired): EX 40.0% → 60.0%. **Headline lesson:**
instrument quality was worth as much as a model upgrade (+16pp from fixing fixtures; 0pp from 7B→14B).

### 20.5 Real data (Olist)
Easy (18): 7B 88.9%, Gemini 94.4% (one query apart; questions name their columns). Hard (16): 7B 50.0%,
Gemini 100.0%. Declared join graph: easy 88.9% → 77.8% → reverted. Data-plane: 1M-row scan 0.09 s;
1M×99K join 0.93 s.

### 20.6 Multilingual and spatial
Shipped M3: control 15/40 → treatment 25/40 (+25.0pp); English untouched 20/20 in every run; D-V2 entity
preservation 27/32. Devanagari 75% vs Hinglish 55% (Latin-script half weaker). Script-repair (B1) fix
lifted the English line 11 → 13/20. DIGIPIN round trip 200/200 (max 2.5 m); D-V5 20/20; D-V6 fail (41 vs
30, missing join/filter).

### 20.7 Memory, correction, routing, security, charts, multi-turn
- Memory: −39.2% tokens, −73.1% steps, 9/10 paraphrase hits; but consistency failed (1.8 distinct result
  sets vs 1.0) and accuracy 10/10 → 7/10 → **shipped off**.
- M5 correction: 11/12 categories classified; repaired: unknown_table, ambiguous_column, syntax,
  type_mismatch, aggregation_misuse, spatial_invalid, empty_result, timeout.
- M9 routing: 59/60 (0.983) hardened set; tool over-exposure (9–40 tools): 1.000 (non-reproduction).
- M8 security: 43/44 (adv_14_strict fails by design); 7/7 direct writes → 42501.
- Charts: A7 validity 30/30; 10/10 malicious specs rejected by each validator; 30/30 render via the
  interpreter with 0 eval attempts; M7 spec score 0.485 (0.633 over produced charts).
- Multi-turn (cycle 007, local): QM 47.2%, IM 16.7%, reference resolution 23/23, rewrite fidelity 87.0%.

### 20.8 Reverted / refuted (keep for honesty)
Schema comments (−19 points), join graph (−11.1pp), coder-14B (−8pp), memory on (−3/10), HNSW (approximate),
the B1 sampling hypothesis, "JSON fix recovered 7/8 nulls" (actually 1), the 313/741 document split
(actually 349/705), mxbai query instruction (−13pp hit@1 on e1).

---

## 21. Design decisions (with reasons)

### 21.1 Architecture invariants (Build Spec A0–A8)
A0.2 no configurable literals in source (lint-enforced) · A0.6 tenant threaded everywhere · **A1** the
orchestrator holds no DB credentials; MCP is the sole holder; `db_net` internal · **A2** typed state with
reducers; flags snapshotted at entry · **A3** deterministic error taxonomy; `permission_denied` and
`unclassified` terminal · **A4** SELECT-only in three layers · **A5** schema fingerprint · **A6** memory
filtered by tenant and fingerprint · **A7** declarative Vega-Lite only, two independent validators, no
repair · **A8** seam registry (every stretch item has a real seam).

### 21.2 Strategy decisions D1–D15
D1 four reasoning nodes; DIGIPIN inline; language gate before routing · D2 fine-tuned normaliser = seam
only · D3 keep LangGraph · D4 memory staleness by fingerprint, validated on read · D5 HEAR/Text2DB
influence only · D6 single-tenant with a tenant seam · D7 Text2VectorSQL + layout-aware ingestion core,
decomposition only on hybrid, CSR-RAG deferred · D8 declarative charts (no execution path) · D9 MCP
security = defence in depth · D10 spatial folded into hybrid · D11 deterministic chart scorer core, vision
gated · D12 exact cited statistics · D13 control paths are deliverables · D14 classify errors into state ·
D15 prove security first.

### 21.3 Build decisions BD1–BD10
BD1 provider seam · BD2 mxbai via the MCP-only Ollama · BD3 `join_cardinality` outside the classifier
(no engine signal) · BD4 gate rejections → `permission_denied` · BD5 `ss_embed` reads a session setting;
SQL never rewritten · BD6 local-model ceiling (unresolved) · BD7 expression audit beside A7 · BD8 evaluator
history in SQLite · BD9 `target_schema` flag · BD10 rewriter role.

### 21.4 v3 decisions D-v3-1…18 (`docs/V3_PLAN.md` §8a)
1 tool ceiling 15→20 · 2 exact vector search · 3 Groq · 4 dashboard-first · 5 dashboards/history methods ·
6 `live` schema + simulator · 7 tile refresh floor 15 s · 8 presentation in the browser · 9 planner uses the
decomposer role · 10 workspace + library methods (ceiling full) · 11 pypdf not Docling · 12 setup test
graded by execution accuracy · 13 keep cleanup items until measured · 14 measure first; lean variants off ·
15 delegate from specs, lead audits · 16 four lean flags · 17 account-level library · 18 v1 self-hosted.

---

## 22. Project history and process

- **v1 (2026-08-13 → 08-18):** spec-driven build, Parts B–F: walking skeleton with the security
  architecture (verify_b 28/28), memory, correction loop, multilingual + DIGIPIN, ingestion + hybrid route,
  charts, evaluator, adversarial suite; tags v1.0.0-phase-f, v1.1.0, v1.2.0.
- **v2 (08-18 → 09-05):** conversational analytics over real data (Olist); **cycle protocol** 000–007
  (audit first, frozen SHA-256 acceptance criteria, sealed briefings, owner picks the workstream); ~51
  audit findings, recurring theme "gates that did not measure their name"; auth, audit log, rate limits,
  per-principal history, full result rows, citation provenance.
- **v3 (09-26 →):** a finished product: Phase 0 (Groq, collation pin, exact search), Phase 1 tiles,
  Phase 2/2b React app and dashboard-first with live data, Phase 3 workspaces (RLS, setup wizard), 3b
  instrumentation + Groq baseline + lean flags, 3c evidence tiers, answer checks, templates, self-testing,
  library + isolated parser, definitions from documents, own Postgres, number grounding; study protocol and
  WIP paper (09-30, shown to the mentor); RAG v2 (10-01→).
- **Process now:** plan → written spec with machine-checkable completion criteria → Sonnet subagent
  implements → lead audits by execution (tests, live checks, reading code) and commits (D-v3-15).
  Design, debugging and security-sensitive code stay with the lead. Audits caught real defects in
  delegated work several times.

---

## 23. The research study (publication plan)

**Question.** Can checks computed from the executed result (no model) tell when a text-to-SQL answer is
wrong as well as, and more cheaply than, model-based confidence?
**Protocol** (`docs/research/evidence_tiers_study.md`, written 2026-09-30 before any measurement):
- RQ1 data checks vs self-consistency (k=5), one LLM judge, two-provider judge ensemble — on accuracy of
  the warning and cost. RQ2 which check catches which error type. RQ3 false alarms on correct answers.
- H1 checks win on join/aggregation errors at lower cost, lose on filter-value/column-meaning errors.
  H2 checks + one judge beat either alone. H3 figure grounding flags <5% of correct answers.
- Data: BIRD Mini-Dev PostgreSQL (500 q) scored against Jin et al.'s corrected annotations; a hand-built
  fan-out set (60–100 q); the 30-question set as a case study.
- Metrics: precision/recall/F1 of warnings, AUROC, risk–coverage, selective accuracy at 80/90% coverage,
  false-alarm rate, extra calls/tokens (checks: zero); bootstrap CIs; manual review of every disagreement.
- Budget: ~15–25M tokens (paid credits), ~4–6 weeks. Venue: workshop / regional / short track.
**Draft** (`docs/research/paper/main.pdf`, 5 pages, WIP): system, baseline, grounding pilot, plan,
limitations. **LLM-as-a-judge is the baseline, not the contribution.**
**Prerequisites:** owner approval to download BIRD Mini-Dev + corrections; a second provider for judges;
answer checks on (`pipeline.answer_checks="on"`); COUNT(*) fan-out coverage; ideally metric reconciliation.

---

## 24. Known issues, limitations and open questions

**Security:** the three review findings of 2026-10-01 are fixed (§12.5); the MCP still has no
authentication of its own (it relies on network isolation); the frontend port 3000 is published on all
interfaces in `docker-compose.yml` (static UI; the API it calls is bound to localhost).
**Accuracy / coverage:** no table selection for large schemas; no value index; single SQL candidate; no
clarifying questions; fan-out probe misses COUNT(*); router prompt and hybrid prompts are ONDC-specific;
follow-ups are the weakest category (4/6); hybrid route has no correction loop or workspace knowledge.
**RAG:** dense-only (ID questions fail); no re-ranker; uploads parsed as flat text (until RAG v2); documents not
scoped by dataset; no abstention; small labelled sets until RAG v2.
**Measurement:** no external benchmark yet; most samples small (16–36); historic numbers on other models;
M6/E1 predate exact search; pilot script not committed; BD6 unresolved.
**Product:** self-testing runs on open, not on a timer; pinning does not create an approved example (plan
said it would); Build-with-AI ignores workspace knowledge; evaluator would get 401 under current auth (kept
off); in-process rate limiter; tokens in localStorage; Olist licence is non-commercial.
**Hygiene:** floating dependency versions; stale `uv.lock`; stale comments/README sections (service count,
default model, retention); MCP timeouts read from Dockerfile ENV rather than config; dead config keys
(`retrieval.decomposition_max_hops`, `models.seed`).
**Out of scope (by decision):** hosting (D-v3-18), MySQL, OCR, alerts, billing, SSO, write access, fine-tuned
normaliser, CSR-RAG until triggered.

---

## 25. Writing a paper about StateSync: claims you can and cannot make

**You can claim (with the stated evidence):**
- A text-to-SQL + RAG system whose trust signals are computed from the executed result; architecture and
  rules as in §5, §8, §12.
- Security properties proved by tests (no credentials on the model side; 7/7 writes refused by the role;
  adversarial suite 43/44 with the one failure explained).
- 25/30 on the authors' 30-question set with Groq gpt-oss-120b, 4.7 calls, ~5k tokens, p50 2.6 s
  (one run, small, includes synthetic data).
- Figure grounding pilot: 97.5% traced, 8/944 flagged, all genuine — **in-sample**.
- Retrieval baseline on a frozen 110-question set; dense retrieval fails on ID questions (0/20 seller probes).
- Methodological lessons with numbers: fixing fixtures (+16pp) mattered as much as a bigger model; memory
  reduced tokens but hurt consistency and accuracy; an approximate vector index silently missed documents.

**You cannot (yet) claim:**
- Any external benchmark accuracy (BIRD/Spider never run).
- That the checks detect wrong answers better than LLM judges (that is the study).
- Generalisation of the grounding pilot (in-sample).
- Any per-tier accuracy (not measured).
- Any comparison with commercial tools (no head-to-head).

**Suggested paper structure:** Introduction (wrong-but-plausible answers; model vs data confidence) →
System (pipeline; tiers; checks; grounding) → Experimental setup (BIRD Mini-Dev corrected, fan-out set,
baselines) → Results (warning precision/recall, AUROC, risk–coverage, cost; per-error-type matrix) →
Analysis (what checks miss) → Related work (§17) → Limitations → Conclusion.

---

## 26. Reviewer FAQ

**Why not just use an LLM judge?** It costs extra calls per answer, has known biases (self-preference,
position, verbosity), agrees imperfectly with humans, and degrades on unseen schemas (Richardson 2026). Our
checks cost nothing and are exact where they apply; the study measures where each wins and whether combining
helps.
**Why LangGraph?** Typed shared state with reducers, conditional edges and a correction cycle are exactly what
it provides (D3).
**Why is the "MCP" not the MCP SDK?** It is the pattern — a separate tool server that alone holds data access —
implemented as JSON-RPC 2.0 over FastAPI; the security property is the network/credential split, not the protocol.
**How do you stop the model from writing to the database?** Three layers: a real Postgres parser rejects anything
but one SELECT (including data-modifying CTEs); every query runs in a READ ONLY transaction that is rolled back;
the role has SELECT only. The role is the guarantee; tests show 7/7 writes refused with SQLSTATE 42501.
**Why exact vector search?** The HNSW index silently missed the right document; exact search over 20k chunks
costs 71 ms, negligible next to a model call.
**Why pgvector and not a vector database?** Adequate below ~10M vectors; one database and one isolation model;
dedicated stores add scale and built-in hybrid search we do not need yet.
**Why Groq?** Hosted, fast, free tier sufficient for development; two models with separate quotas.
**Why is trajectory memory off?** It cut tokens 39% but made answers less consistent and dropped accuracy
10/10 → 7/10.
**Why pypdf / pdfplumber, not Docling?** Docling needs ~2 GB of models; pdfplumber gives tables and headings for
text PDFs; scanned PDFs are out of scope.
**How are tenants isolated?** RLS on every app table with a per-transaction tenant setting that fails closed;
principal filters on every query; a test fails if a table lacks RLS. Business datasets are shared by design.
**Is the setup test circular?** On olist/public it uses curated questions that are also evaluation fixtures, and
right answers become approved examples for that workspace — a known limitation (§24).
**What happens to my database password?** Encrypted with a key only the MCP holds; never returned; the login
must be provably read-only.
**How reproducible are the numbers?** Flags guarded (`shipped_flags.py`), deployment parity checked, fixtures
frozen, runs record provenance; most samples are small.
**What are "Jev" and "Laya"?** A proposition the owner mentioned (2026-10-01), not yet described; nothing in
the system corresponds to it.

---

## 27. Glossary

**A5 fingerprint** hash of the `public` schema's shape · **A7** the synthesizer output contract (declarative
Vega-Lite) · **Compiled** tier: template of an approved query filled in code · **Evidence tier** trust label of
an answer · **Execution accuracy (EX)** result rows match the reference as multisets · **Fan-out** a join that
repeats rows, inflating aggregates · **Figure grounding** tracing every number in the prose to its source ·
**Flags snapshot** flag values frozen at the start of a question · **Hybrid route** SQL + documents ·
**Interrogation** sampling real rows before writing final SQL · **MCP** the tool server holding all data access ·
**MRR** mean reciprocal rank · **RLS** row-level security · **RRF** reciprocal rank fusion · **Shipped flags** the
default configuration · **Tenant** isolation unit for app data · **Verified** tier: an approved query run as is ·
**Workspace** a dataset plus what the owner taught about it.

---

## 28. File map

| Area | Files |
|---|---|
| Plan, decisions | `docs/V3_PLAN.md`, `docs/StateSync_Strategy_Document.md`, `docs/StateSync_Build_Spec.md`, `statesync_phase_B.md` |
| Pipeline | `orchestrator/graph.py`, `state.py`, `main.py`, `nodes/*.py` |
| Trust layer | `orchestrator/evidence.py`, `answer_checks.py`, `number_grounding.py`, `sql_explain.py`, `doc_definitions.py` |
| Workspaces | `mcp/tools/workspace_tools.py`, `orchestrator/workspaces_api.py`, `workspace_setup.py`, `workspace_context.py`, `templates.py`, `mcp/tools/sql_templates.py` |
| Library / RAG | `orchestrator/library_api.py`, `library_ingest.py`, `parser/server.py`, `mcp/tools/library_tools.py`, `mcp/tools/vector_tools.py`, `scripts/ingest.py` |
| Own databases | `mcp/connections.py`, `orchestrator/datasets.py` |
| SQL gate, DB access | `mcp/tools/data_tools.py`, `mcp/db.py`, `mcp/tools/schema_tools.py`, `mcp/fingerprint.py` |
| Dashboards | `mcp/tools/tile_tools.py`, `dashboard_tools.py`, `orchestrator/tiles_api.py`, `dashboards_api.py`, `dashboard_builder.py`, `simulator/` |
| Models | `orchestrator/llm/*`, `config/flags.toml` |
| Config | `config/statesync.toml`, `config/flags.toml`, `config/error_taxonomy.json` |
| Data | `migrations/*.sql`, `fixtures/`, `demo/` |
| Evaluation | `scripts/eval_groq.py`, `scripts/eval_retrieval.py`, `scripts/verify_*.py`, `scripts/shipped_flags.py`, `docs/eval/` |
| Research | `docs/research/evidence_tiers_study.md`, `paper_facts.md`, `paper/main.tex`, `docs/specs/rag_v2.md` |
| Web | `web/src/**`, `web/DESIGN.md`, `web/e2e/**` |
| Demo | `docs/DEMO.md`, `demo/*` |
