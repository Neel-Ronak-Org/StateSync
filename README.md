# StateSync

> **Picking this up cold (human or agent)?** Start with [`docs/AGENT_HANDOFF.md`](docs/AGENT_HANDOFF.md) — history, settled decisions, and current direction.

StateSync is a live analytics dashboard that builds itself, with a chat for asking new questions. Ask in plain English and a question is routed to SQL, to documents, or to both; only read-only queries run; the answer comes back as prose with its source and, when it helps, a chart. Pin an answer and it becomes a dashboard card that re-runs its saved SQL on a schedule, or let "Build with AI" plan a whole dashboard for a dataset. Every card still comes through the same SQL gate and chart validators as a typed question.

**What you see:** a dashboard home (navy by default, light mode too) with KPI cards that show their change, trends, breakdowns, donuts and an insights strip, refreshing every 30 s on the live demo data; "Show as" to redraw any card another way; drag-and-resize layouts and several dashboards per dataset; an Ask page with conversation history.

**Datasets:** `live` (an online shop kept moving by a simulator: orders, deliveries, reviews, web traffic), `olist` (1.5M real Brazilian e-commerce rows, frozen) and `public` (a synthetic logistics network plus policy and SLA documents, frozen for the accuracy tests). A workspace can also use your own Postgres database (connected read-only) and documents you upload to the Library.

## Quick start (a machine that already has the stack built)

```bash
cd StateSync
# Start the app (never the evaluator; never rebuild postgres)
docker compose up -d --no-build postgres qdrant ollama mcp orchestrator frontend simulator
docker compose ps                     # wait until all say (healthy), about a minute
curl -s localhost:8090/health         # "llm_ready": true
pbcopy < secrets/api_token_dev        # copies your sign-in token (macOS); prints nothing
open http://localhost:3000            # paste the token, Sign in
```

- **Why not the evaluator:** it re-runs 55 test questions through the model provider every hour, which uses up Groq's free daily limit within hours. Start it only to measure.
- **Budget:** each question costs about 5 model calls and "Build with AI" about 40; Groq's free tier allows 1,000 requests a day per model. Refreshing a card costs no model call.

## Shutting down

```bash
docker compose stop       # stops everything, keeps all data; start again with the command above
```

- `docker compose down` also removes the containers; the data (volumes) is kept.
- **Never** `docker compose down -v`, `docker volume prune` or `docker system prune --volumes`: they delete the database (including the 1.5M Olist rows), the search index and the downloaded models.
- After a code change, rebuild only that service, e.g. `docker compose build frontend && docker compose up -d --no-deps frontend`. Never rebuild postgres.

## First-time setup (a clean machine)

1. **Prerequisites:** Docker Desktop with Compose v2; Python 3.12+ for the operator scripts; a Groq API key (the model provider, `config/flags.toml`). Embeddings run in the `ollama` container: `docker compose up -d ollama && docker compose exec ollama ollama pull mxbai-embed-large`.
2. **Secrets** (`secrets/` is gitignored): `pg_migrator_password`, `pg_reader_password`, `pg_history_password`, `pg_simulator_password` (any random strings), `groq_api_key` (your key), and empty `anthropic_api_key`, `azure_openai_api_key`, `gemini_api_key` files (the orchestrator mounts all provider keys). Also `mcp_token` (`openssl rand -hex 32 > secrets/mcp_token`; the MCP will not start without it), the evaluator's `mcp_token_evaluator` and `api_token_evaluator` (commands in `secrets/README.md`), and `connection_key` (`python3 scripts/make_connection_key.py`; never regenerate it once databases are connected). See `secrets/README.md`.
3. **Build and start:** `docker compose build` then the start command above.
4. **Migrations:** `bash scripts/migrate.sh` runs 001, 002 and 004; apply 005 onward in order with `docker exec -i postgres psql -U statesync_migrator -d statesync < migrations/NNN.sql`. After 020, set the simulator's password: `printf "ALTER ROLE statesync_simulator PASSWORD '%s';" "$(cat secrets/pg_simulator_password)" | docker exec -i postgres psql -U statesync_migrator -d statesync`.
5. **An API principal:** `python3 scripts/gen_api_principal.py` (writes `secrets/api_principals.json` and `secrets/api_token_dev`), then `docker compose restart orchestrator`.
6. Open http://localhost:3000, sign in, and press **Build with AI**.

**Troubleshooting:** if the orchestrator will not start, the usual cause is a missing or malformed `secrets/api_principals.json` (auth fails closed on purpose). The status light at the bottom of the left rail says whether the server and the model are reachable.

## Architecture

The stack consists of 10 services in `docker-compose.yml`:

- `simulator`: keeps the `live` dataset moving (Phase 2b). Writes only to the `live` and `live_sim` schemas, as `statesync_simulator`; `db_net` only.

- `postgres`: PostgreSQL database with PostGIS spatial extensions and pgvector. Resides on `db_net`.

- `mcp`: Model Context Protocol server on port 8080. Sole holder of database credentials. Bridges `db_net` and `app_net`. Accepts calls only with the shared service token the orchestrator sends (`mcp/service_auth.py`).

- `parser`: turns uploaded PDFs into text on port 8095. Isolated on `parser_net`, which reaches nothing else; only the orchestrator can call it.

- `reranker`: the optional passage re-ranker on port 8096 (two cross-encoders baked into the image; switched off by default, `config/flags.toml`). Isolated on `reranker_net`; only the MCP can call it.

- `orchestrator`: LangGraph multi-agent pipeline on port 8090. Resides on `app_net`.

- `evaluator`: Continuous evaluation engine and metrics service on port 8095 (its own container). Resides on `app_net`. Kept off: it spends model quota. It has its own API principal and an MCP token restricted to gold SELECTs.

- `frontend`: the React web app (`web/`) served by nginx on port 3000 under a strict CSP (scripts from the app only, no `eval`). Resides on `app_net`.

- `ollama`: Containerized Ollama instance on port 11434 serving `mxbai-embed-large` embeddings to `mcp`. Sits on both networks. Answers come from the hosted model in `config/flags.toml` (Groq), not from Ollama.

- `qdrant`: Vector database on port 6333 for the execution-trajectory memory, which is switched off (it reduced tokens but hurt accuracy). Document search uses pgvector in `postgres`. Resides on `db_net`.

The orchestrator has NO direct database access — every database operation goes through the mcp service over JSON-RPC. `postgres` and `qdrant` reside on an internal network (`db_net`) inaccessible to the orchestrator (`app_net`). The frontend is served on port 3000 and calls the orchestrator cross-origin on port 8090. In development, `docker-compose.override.yml` publishes the frontend, orchestrator, MCP, qdrant and evaluator ports on 127.0.0.1 only.

Three layers on generated SQL (`mcp/tools/data_tools.py`):

1. **Statement gate:** the SQL is parsed with pglast (PostgreSQL's own parser); anything but a single SELECT, including a data-modifying CTE, is rejected.
2. **Session guards:** every query runs in a READ ONLY transaction that is always rolled back, with a statement timeout.
3. **Role grants:** the `statesync_reader` role has SELECT only, so a write that reached the engine would fail (SQLSTATE 42501). The security claim rests on this layer.

- **Layer 1 (Parser Gate):** MCP parses SQL with `pglast`. It rejects multi-statement queries, data-modifying CTEs, anything other than a single read-only `SELECT`, and now also rejects a `SELECT` that reads no real table.

- **Layer 2 (Session Guards):** MCP connection pool configures sessions with `SET TRANSACTION READ ONLY`, `statement_timeout = 15000ms`, and mandatory transaction rollback on release.

- **Layer 3 (PostgreSQL Role):** MCP connects as `statesync_reader`, which holds `GRANT SELECT` exclusively. Write capability is absent by design: the query role holds no `INSERT`, `UPDATE`, or `DELETE`.

## Security

- Three layers on generated SQL. Layer 1 parses with pglast and rejects anything that is not a single read-only SELECT, and now also rejects a SELECT that reads no real table.

- Write capability is absent by design: the query role holds no INSERT/UPDATE/DELETE.

- Bearer-token auth on POST /query, GET /sessions and GET /sessions/{id}; GET /health is deliberately open. Tokens are stored only as SHA-256 hashes and compared with hmac.compare_digest. Startup FAILS CLOSED if the principal file is missing or malformed.

- Per-identity audit log in session_state.audit_log recording principal, tenant, route, outcome and client IP for allowed, denied_auth and denied_rate alike.

- Per-principal rate limiting, 30 requests / 60 seconds, 429 with Retry-After.

- Conversation history is scoped per principal: two principals on one tenant cannot read each other's conversations, including by session id.

- The adversarial suite (`scripts/verify_security.sh`) reports **43/44** — see [What is measured](#what-is-measured) for the one failure and why it stands.

## Corpora

- `public` (synthetic ONDC schema): 550 orders. This is the SHIPPED default (schema_retriever.target_schema = "public").

- `olist` (real Brazilian e-commerce): 1,551,698 rows across 9 tables, including 99,441 orders and 96,478 delivered.

- `documents`: 2,585 documents / 20,245 chunks, of which 2,567 documents / 20,209 chunks are Olist review digests and 18 documents / 36 chunks are the synthetic policy corpus.

## What is measured

The effect table from §2:

| intervention | effect |
|---|---|
| fixing the fixture's ground truths | +16.0pp |
| 7B → hosted, under-specified questions | +20.0pp |
| 7B → hosted, well-specified questions | +5.5pp — one query |
| 7B → 14B general (local) | +0.0pp |
| 7B → 14B coder (local) | −8.0pp |

Instrument quality is worth as much as a frontier model, and a bigger local model is worth nothing.
 
The adversarial suite (scripts/verify_security.sh) reports 43/44. The 40 original checks all pass. Four strict credential-shape checks were added alongside them and ONE fails: adv_14_strict. The failure stands because while the user-facing answer is correct, the residual is that /query echoes the model's own rejected SQL in a debug field.

## The demo

`python3 scripts/demo_olist.py`. It runs a 3-turn conversation over the real Olist corpus and exits non-zero unless all four §7 clauses hold:
- Clause 1: >=3 turns complete without error.
- Clause 2: rewrite applied on >=1 turn.
- Clause 3: final turn returns a Vega-Lite chart.
- Clause 4: SQL queries execute against real `olist.` schema tables.

Verified transcript in docs/demo_olist_transcript.json:
- **T1:** `"How many orders are there in total?"` -> 99,441
- **T2:** `"How many of those were delivered?"` -> rewritten to `"How many of the total 99,441 orders were delivered?"` -> 96,478
- **T3:** `"Break that down by customer state — top 5, as a chart."` -> SP, RJ, MG, RS, PR + a Vega-Lite bar chart

Turn 2's rewrite carrying the number 99,441 out of turn 1 is what makes it a conversation rather than three questions.

The demo flips target_schema to olist and restores shipped state afterwards, verifying the restoration.

## Limitations

- Accuracy depends heavily on question phrasing; the headline single-turn numbers were measured on questions whose projections and denominators were stated by construction, which removes schema linking and ambiguity resolution from the task.

- The shipped model is Groq-hosted gpt-oss-120b (`config/flags.toml`); the free tier's daily token limit caps how many questions a day the demo can answer. Numbers measured on the older local 7B model are not representative of it.

- The rate limiter is in-process: a restart clears every window, and N replicas permit N× the rate.

- Bearer tokens have no expiry; revocation means editing the secret and restarting.

- The frontend keeps its token in localStorage, readable by any script achieving XSS.

- session_state.audit_log has a 180-day retention policy (`audit_retention_days`), but it is enforced only when an operator runs `scripts/prune_audit_log.py` (dry run by default); nothing prunes it automatically. Full stored result rows have a 30-day TTL and a 1 MiB/turn cap, pruned on write and forceable with scripts/prune_turn_rows.py.

- Conversation history written before authentication existed is attributed to a sentinel principal and is invisible in the UI by design.

- The full list is in [`docs/limitations.md`](docs/limitations.md); it is long on purpose.

## Repo map

| Document | Purpose |
|---|---|
| [`docs/AGENT_HANDOFF.md`](docs/AGENT_HANDOFF.md) | Project history, settled decisions, and current direction. Read this first. |
| [`docs/V3_PLAN.md`](docs/V3_PLAN.md) | The current plan: phases, decision log, issue register. |
| [`web/DESIGN.md`](web/DESIGN.md) | The web app's design system and UI contract. |
| [`docs/V2_STRATEGY.md`](docs/V2_STRATEGY.md) | Strategy, workstream deliverables, completion conditions, and cut-lines. |
| [`docs/limitations.md`](docs/limitations.md) | Full inventory of known limitations, weak metrics, and unbuilt seams. |
| [`docs/open_items.md`](docs/open_items.md) | Active defects, audit findings, and open technical investigations. |
| [`docs/metrics-report.md`](docs/metrics-report.md) | Recorded experimental results, benchmark numbers, and evaluation methodology. |

## Verification gates

Run the gates from the repository root:

```bash
# Unit and integration test suite
pytest tests/ -q

# Config discipline and literal lint (A0.2)
bash scripts/lint-no-literals.sh

# Architectural seams verification
python3 scripts/verify_seams.py

# Visualization and CSP validation suite (Phase E2)
python3 scripts/verify_e2.py

# Deployed container health and connectivity checks
python3 scripts/verify_deployed.py

# Adversarial security evaluation suite (reports 43/44)
bash scripts/verify_security.sh

# Tiles and dashboards against the live stack (two principals); --build also
# runs "Build with AI" on every dataset
python3 scripts/verify_tiles.py
python3 scripts/verify_dashboards.py --build

# The web app: unit tests, the mocked browser suite, and the live one
cd web && npm run test:unit && npx playwright test && STATESYNC_LIVE=1 npx playwright test
```
