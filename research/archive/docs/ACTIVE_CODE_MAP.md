# Active code map

What runs in the review demo, folder by folder and file by file. Files that exist but are switched off or
unused in the demo are listed once, at the end, so you know to skip them.

"Active" means switched on in the demo's settings (`research/paperb/demo/flags_demo.toml` and
`statesync_demo.toml`) or used by a screen you will show.

---

## 0. What runs where

**The data**

| Data | What it is | Where it lives |
|---|---|---|
| **SEC figures database** | The companies' official reported numbers (revenue, net income, dividends, assets…) from the SEC's XBRL data, for the FinanceBench companies (31 in the paper's question sets; the `companies` table lists 32). Tables: `companies`, `facts` (every reported value, 880,000 rows), `metric_tags`, and two views: `annual_metrics` (one value per company, metric and year, as first reported) and `annual_metrics_restated` (the latest restated value). | Its own Postgres container, `statesync-sec-db`, on port 5435, built by `demo/sec_db.sh`. StateSync connects to it as a **connected database** ("your own database"), through a read-only login. |
| **Filing library** | FinanceBench's open sample: 84 annual and quarterly reports (10-K, 10-Q) of the same companies, split into about 45,000 passages, plus 3 small demo PDFs (87 documents, 44,922 passages in total). | Uploaded through the app's Library. The text and vectors are stored in the main Postgres, in the table `workspace_docs.chunks`. |
| **BIRD** | A public text-to-SQL benchmark, used only for the paper's SQL experiments. | Its own container, `statesync-bird-db`. Not used in the demo. |
| Sample datasets | `live`, `olist`, `public`, from the earlier product phase. | The main Postgres. Not used in the review demo. |

**The vector database: PostgreSQL with the pgvector extension (version 0.8.5).** There is no separate vector
database.
- Each passage's vector is a `vector(1024)` column next to its text, page and document id.
- The search is an exact cosine-distance scan, written in SQL (`mcp/tools/vector_tools.py`).

Why: one database holds the data, the documents and the vectors, so there is one security model (one
read-only login, one set of access rules) and nothing extra to run. At about 45,000 passages an exact scan is
fast.

(Qdrant is in `docker-compose.yml`, but only for the "memory" feature, which is switched off.)

**Where it all runs**

| Part | Runs on |
|---|---|
| Everything in Docker: web app, orchestrator, MCP, Postgres (with pgvector), the SEC database, Ollama (embeddings), the PDF parser | Your MacBook Air (Apple M5, 24 GB), inside Docker Desktop's Linux VM (10 CPUs, 8 GB) |
| The re-ranker (bge-reranker-v2-m3) | The same Mac, **outside** Docker, on the Mac's GPU (Docker on a Mac cannot use the GPU). Started by a login service; script `scripts/run_reranker_host.sh`. |
| The LLMs (gpt-oss-120b, Qwen 3.8 27B) | Remote, through an OpenAI-compatible API provider |
| Jev | Remote, through OpenCode Zen's API |
| The demo copy | Its own containers: `statesync-frontend-demo` (web, port 3090), `statesync-orchestrator-paperb` (API, port 8091), `statesync-mcp-paperb`. They share Postgres, Ollama and the SEC database with the main app. |

Nothing runs in a cloud of ours. Only the model calls leave the machine: the question and the passages go to
the LLM and Jev APIs. The documents themselves, the embeddings and the database stay local.

---

## 1. `orchestrator/`: the brain (Python, FastAPI + LangGraph)

It receives the question, runs the pipeline and returns the answer. It holds the model API keys and **no**
database password.

**The API and the pipeline**

| File | What is in it |
|---|---|
| `main.py` | The web API (FastAPI). `POST /query` runs the pipeline and assembles the response: answer, citations, chart, and the trace the Inspector reads. Also registers all the other routes. |
| `graph.py` | The pipeline as a LangGraph graph: which step follows which, including "SQL failed → try again" (`build_graph` at line 340) |
| `state.py` | The shared record of one question as it moves through the steps (typed with Pydantic) |
| `config_loader.py` | Reads `config/flags.toml` and `config/statesync.toml`; takes a snapshot of the switches for each question |
| `auth.py` | Sign-in tokens (stored only as hashes), rate limiting |
| `session_api.py` | Browser sign-in and sign-out (HttpOnly cookie) |
| `mcp_client.py` | The only way the orchestrator reaches data: calls to the MCP service (run SQL, search, read schema…) |

**The steps of the pipeline**

| File | Step |
|---|---|
| `nodes/entry.py` | First step: sets up the question's record |
| `front_door.py` | **The router.** Gathers the evidence (database card, best-matching documents, recent turns) and asks Jev one choice question (`decide_jev_choice`, line 723). Falls back to the LLM router if Jev fails. |
| `card_coverage.py` | The coverage line on the database card (which companies and years the data holds) |
| `jev_client.py` | The one HTTP call to Jev |
| `context_carry.py` | Follow-up questions: carries the company and year from the previous turn, with no model call |
| `nodes/sql_agent.py` | **The SQL agent:** draft → sample real rows → final SQL → run → fix errors (`sql_agent_node`, line 626) |
| `schema_retriever.py` | Reads the database's tables and columns for the SQL prompt |
| `workspace_context.py` | Adds the workspace's approved knowledge (business description, column descriptions, definitions, approved examples) to the SQL prompt |
| `classifiers/error_classifier.py` | Puts a failed query's error into a category with a fixing hint, without a model |
| `sql_outcomes.py` | How the SQL step ends when the database cannot answer |
| `spatial_resolver.py` | Map-code resolution for the sample datasets. Runs on every SQL attempt but does nothing for the SEC data. |
| `nodes/error_exit.py` | Where the SQL loop stops when it runs out of retries |
| `nodes/unstructured_knowledge.py` | **The documents engine:** two-round search (`_semantic_search_expanded`, line 118), which 8 passages the writer reads (`order_passages`, line 82), the two-source question split (`hybrid_plan_node`, 518) and narrowed document search (`hybrid_docs_node`, 546). Also records the search stage by stage for the Inspector. |
| `passage_jev.py` | Jev scores each passage and re-orders them |
| `nodes/synthesizer.py` | **The answer writer:** one LLM call; citations built by code; chart proposal checked |
| `number_grounding.py` | Checks every number in the answer against the result or a cited passage |
| `evidence.py` | The trust label: Verified / Checked / Unchecked |
| `sql_explain.py` | "How I got this": the plain-English reading of the SQL, made by code |
| `failures.py` | The plain-words message when something fails |
| `profiles.py` | Which prompt texts and switches a dataset gets (the neutral profile for the SEC data) |
| `datasets.py` | Tells a built-in dataset from a connected database |
| `routing/registry.py` | The table of routes (sql, docs, hybrid) and which step handles each |

**The model layer**

| File | What is in it |
|---|---|
| `llm/provider.py` | `get_agent_llm(role)`: the one function every LLM call goes through. Picks the model for the role. |
| `llm/adapters/openai_compat_adapter.py` | The adapter the demo uses (any OpenAI-compatible API). Refuses an answer from a model other than the one requested. |
| `llm/adapters/base.py` | The interface every adapter follows |
| `llm/recording.py` | Records every call: tokens, time, which model answered |

**The screens' back ends**

| File | Screen |
|---|---|
| `workspaces_api.py`, `workspace_setup.py` | Workspaces: create, AI-drafted descriptions, approve, test, approved examples |
| `doc_definitions.py` | Definitions found in uploaded documents (kept only if the quote is really there) |
| `example_timer.py` | Re-checks approved examples on a schedule |
| `library_api.py`, `library_ingest.py` | Library: folders, uploads (sent to the parser), chunking (`chunk_pages`, line 149) |
| `inspect_api.py`, `schema_graph.py` | The Schema tab and the Inspector's schema diagram |
| `tiles_api.py`, `tile_render.py`, `dashboards_api.py` | Dashboards and pinned tiles (refresh = re-run the stored SQL, no model) |
| `builder_api.py`, `dashboard_builder.py` | "Build with AI" for dashboards |

---

## 2. `mcp/`: the data service (the only one with database access)

A JSON-RPC server offering fixed "tools". It holds the database logins; the AI side can only call these
tools.

| File | What is in it |
|---|---|
| `main.py` | The server: receives a tool call, checks the service token and the account, runs the tool |
| `service_auth.py` | Only the orchestrator, with its secret token, may call the MCP |
| `db.py` | Database connection pools; every query session is read-only, with a 15-second timeout, and always rolled back |
| `connections.py` | Connected databases (like the SEC one): proves the login is read-only, keeps its password encrypted |
| `embeddings.py` | Turns text into a vector by calling Ollama (`embed_text`, line 46) |
| `session.py`, `serialization.py`, `fingerprint.py` | Request context, turning database values into JSON, a schema fingerprint |
| `tools/data_tools.py` | **The SQL gate and runner:** `_check_layer1` (line 128) allows only one SELECT, parsed with pglast; `run_select` (line 393) runs it; `sample_rows` gives the agent a few real rows |
| `tools/schema_tools.py` | List tables, describe a table (columns, types, foreign keys) |
| `tools/vector_tools.py` | **The vector search** in SQL over pgvector (`semantic_search`, line 256), and the call to the re-ranker (line 307) |
| `tools/history_tools.py` | Saves and reloads conversations; the audit log |
| `tools/workspace_tools.py` | The workspace knowledge store (descriptions, definitions, examples) |
| `tools/library_tools.py` | Documents and folders: start, add chunks (embeds them), finish |
| `tools/tile_tools.py`, `tools/dashboard_tools.py` | Tiles and dashboards |

---

## 3. The other services

| File | What it is |
|---|---|
| `parser/server.py` | Turns an uploaded PDF into text, page by page (pypdf). No network, no passwords; each file in its own limited process. |
| `reranker/server.py` | The cross-encoder service: `POST /rerank` with a question and passages returns a score for each. The demo uses the copy running on the Mac GPU. |
| `docker-compose.yml` | Every container, its networks and its secrets |
| `migrations/*.sql` | The database structure, in order: roles and permissions (001, 002), the conversations and audit log (011–016), tiles and dashboards (017, 019), workspaces with row-level security (021), the library (024), connected databases (026)… |

---

## 4. `web/src/`: the web app (React + TypeScript)

| Folder or file | What is in it |
|---|---|
| `main.tsx`, `App.tsx` | Start-up, sign-in state, which page is shown |
| `api/client.ts`, `api/types.ts` | Every call to the orchestrator, and the shapes of its answers |
| `features/auth/` | The sign-in card |
| `features/shell/` | The left rail, the dataset picker, the connection light |
| `features/ask/` | The Ask page and the conversation list |
| `features/chat/` | The conversation: `useConversation.ts` sends questions; `TurnItem.tsx` shows one answer and its buttons; `figures.ts` makes numbers hoverable to their source; `DetailsSheet.tsx` is the details panel |
| `features/inspect/` | **The Pipeline Inspector:** `InspectorDialog.tsx` (the step cards), `PipelineFlow.tsx` (the flow diagram), `SchemaDiagram.tsx` and `SchemaTab.tsx` (the database diagram), `inspect.ts` (the logic, tested in `inspect.test.ts`) |
| `features/workspaces/` | Workspace screens: Business, Knowledge, Test, Documents and Schema tabs |
| `features/library/` | Documents and databases: folders, upload, connect a database |
| `features/dashboard/` | Dashboards, tiles, Build with AI |
| `charts/VegaChart.tsx`, `charts/a7.ts` | The only place a chart is drawn; the chart spec is checked again in the browser before drawing |
| `viz/` | Picks a sensible chart for a result |
| `components/ui/` | Ready-made buttons, dialogs, tables (shadcn/ui) |
| `lib/`, `hooks/` | Small helpers: number formatting, routes, storage |
| `web/entrypoint.sh`, `web/nginx.conf` | Serving the built app, security headers, runtime settings and the demo's sample questions |

---

## 5. `config/` and the demo settings

| File | What is in it |
|---|---|
| `config/flags.toml` | The switches (on/off for every feature) and which model each role uses. The shared app's defaults. |
| `config/statesync.toml` | Numbers and limits: top-k, chunk size, timeouts, row caps, Jev settings |
| `research/paperb/demo/flags_demo.toml` | The demo's switches: Jev routing, coverage line, Jev passage order, entity narrowing, follow-ups, Inspector recording |
| `research/paperb/demo/statesync_demo.toml` | The demo's settings |
| `research/paperb/demo/demo_stack.sh` | `build`, `up`, `down` for the demo copy |
| `research/paperb/demo/suggestions.json` | The six sample questions on the empty chat screen |
| `secrets/` | Passwords and keys (never committed, never printed) |

---

## 6. Tests and tools

| Folder | What is in it |
|---|---|
| `tests/` | About 1,280 Python tests. Each new piece has its own, for example `test_passage_jev.py`, `test_card_coverage.py`, `test_inspect_trace.py`. |
| `web/src/**/*.test.ts` | 74 web tests |
| `scripts/` | Tools run by hand, never inside the app: `eval_*` experiments, `build_*` question sets, `paperb_*` the paper's analysis, `verify_*` deployment checks, `run_reranker_host.sh` |

---

## Present but not used in the demo

These are switched off, or only used by the older pipeline or by research scripts. Skip them in the review.

**Older router path** (replaced by `front_door.py` when the front door is on):
- `nodes/supervisor.py`, `nodes/normalize.py`, `nodes/rewrite.py`, `language_gate.py`, `language_normalizer.py`

**Switched off:**

| File | Feature |
|---|---|
| `memory_store.py`, `mcp/qdrant_store.py`, `mcp/tools/memory_tools.py` | Memory |
| `templates.py`, `mcp/tools/sql_templates.py` | Templates |
| `answer_checks.py` | Answer checks |
| `repair_loop.py` | Repair loop |
| `query_rewrite.py` | Query rewrite |
| `column_profiles.py` | The profiles SQL mode |
| `table_selection.py` | The "linked" schema mode |

**Research only:**
- `self_consistency.py`, `scorers.py` (the evaluator), `vision.py`.

**Not the demo's provider:**
- The other adapters in `llm/adapters/` (Anthropic, Azure, Gemini, Groq, Ollama).

**Built but off in search** (inside the active files):
- Keyword search (BM25) and the layout chunker; their switches are `retrieval_mode` and `chunker`.

**For the ONDC sample dataset only:**
- `profiles_ondc.py`.
