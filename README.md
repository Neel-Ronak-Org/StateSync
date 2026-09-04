# StateSync

> **Picking this up cold (human or agent)?** Start with [`docs/AGENT_HANDOFF.md`](docs/AGENT_HANDOFF.md) — history, settled decisions, and current direction.

StateSync is a conversational analytics agent over e-commerce data. A natural-language question is routed to SQL, to documents, or to both; read-only queries run; the answer comes back as prose plus a citation and, when it helps, a Vega-Lite chart. Multi-turn: follow-ups keep context.

## Quick start

Follow these numbered steps to set up and run StateSync on a clean machine:

1. **Prerequisites**
   - Docker Desktop with Compose v2.
   - The native Ollama app running on the host (`config/flags.toml` points generation at `http://host.docker.internal:11434`).
   - Python 3.12+ for the operator scripts.

   Pull the required models:
   ```bash
   # Embeddings always run inside the container
   docker compose up -d ollama
   docker compose exec ollama ollama pull mxbai-embed-large

   # Generation runs via native host Ollama
   ollama pull qwen2.5:7b
   ```

2. **Create secrets**
   Create the four secrets the stack needs to boot (`secrets/` is gitignored):
   ```bash
   mkdir -p secrets
   echo "changeme_migrator" > secrets/pg_migrator_password
   echo "changeme_reader"   > secrets/pg_reader_password
   echo "changeme_history"  > secrets/pg_history_password

   # Provider API keys. The orchestrator ATTACHES all three, so the FILES must
   # exist even when you only run the local provider -- `docker compose up`
   # fails on a missing secret file, not at the point the key is used. Leave
   # them empty unless you switch provider in config/flags.toml.
   touch secrets/anthropic_api_key secrets/azure_openai_api_key secrets/gemini_api_key
   ```
   These cover database administration (`pg_migrator_password`), read-only analytics
   queries (`pg_reader_password`), session history and audit writes
   (`pg_history_password`), and the three hosted-provider keys. Only
   `anthropic_api_key` needs a real value, and only when
   `config/flags.toml` sets `provider = "anthropic"` — but all three files must be
   present or the orchestrator will not start.

3. **Start the services**
   Launch the 7 Docker Compose services:
   ```bash
   docker compose up -d
   ```

4. **Run migrations**
   Apply the database schema and compute the fingerprint:
   ```bash
   bash scripts/migrate.sh
   ```
   `migrate.sh` only runs 001, 002 and 004; there is no ledger, and migrations 005 onward are applied by hand with psql.

5. **Generate an API principal**
   Generate a bearer token and configure the orchestrator:
   ```bash
   python3 scripts/gen_api_principal.py
   docker compose restart orchestrator
   ```
   `python3 scripts/gen_api_principal.py` prints a bearer token ONCE and writes `secrets/api_principals.json` (as well as `secrets/api_token_dev`). Then `docker compose restart orchestrator`, because the principal file is read at startup.

6. **Open the web interface**
   Open http://localhost:3000 and paste the token into the login gate.

7. **Run the demo**
   Run `python3 scripts/demo_olist.py` to see the 3-turn Olist conversation.

**Troubleshooting:** If the orchestrator will not start, the usual cause is a missing or malformed `secrets/api_principals.json` — auth fails closed on purpose.

## Architecture

The stack consists of 7 services in `docker-compose.yml`:

- `postgres`: PostgreSQL database with PostGIS spatial extensions and pgvector. Resides on `db_net`.

- `mcp`: Model Context Protocol server on port 8080. Sole holder of database credentials. Bridges `db_net` and `app_net`.

- `orchestrator`: LangGraph multi-agent pipeline on port 8090. Resides on `app_net`.

- `evaluator`: Continuous evaluation engine and metrics service on port 8095. Resides on `app_net`.

- `frontend`: Nginx web interface served on port 3000. Resides on `app_net`.

- `ollama`: Containerized Ollama instance on port 11434 serving `mxbai-embed-large` embeddings to `mcp`. Sits on both networks.

- `qdrant`: Vector database on port 6333 storing execution trajectories. Resides on `db_net`.

The orchestrator has NO direct database access — every database operation goes through the mcp service over JSON-RPC. `postgres` and `qdrant` reside on an internal network (`db_net`) inaccessible to the orchestrator (`app_net`). The frontend is served on port 3000 and calls the orchestrator cross-origin on port 8090.

Three layers on generated SQL (stated plainly, no adjectives):

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

- The local 7B model is the shipped default and is NOT representative of hosted-model quality.

- The rate limiter is in-process: a restart clears every window, and N replicas permit N× the rate.

- Bearer tokens have no expiry; revocation means editing the secret and restarting.

- The frontend keeps its token in localStorage, readable by any script achieving XSS.

- session_state.audit_log has no retention policy and grows without bound. Full stored result rows DO have one (30-day TTL, 1 MiB/turn cap), pruned on write and forceable with scripts/prune_turn_rows.py.

- Conversation history written before authentication existed is attributed to a sentinel principal and is invisible in the UI by design.

- Point at [`docs/limitations.md`](docs/limitations.md) for the full list and say that it is long on purpose.

## Repo map

| Document | Purpose |
|---|---|
| [`docs/AGENT_HANDOFF.md`](docs/AGENT_HANDOFF.md) | Project history, settled decisions, and current direction. Read this first. |
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
```
