# StateSync — Multi-Agent Analytics Infrastructure for ONDC

> **Picking this up cold (human or agent)?** Start with [`docs/AGENT_HANDOFF.md`](docs/AGENT_HANDOFF.md) — history, settled decisions, and current direction — then [`docs/PROJECT_DIRECTION.md`](docs/PROJECT_DIRECTION.md).


StateSync is an enterprise-grade multi-agent analytics platform for open network (ONDC) e-commerce data. It provides natural language analytics, schema-guided SQL generation, deterministic security guarantees, and interactive Vega-Lite visualizations.

---

## 🚀 Development Quick Start

### 1. Prerequisites
- **Docker Desktop** (with Compose v2)
- **Python 3.12+** (for running migration & seeding tools locally)
- *(Optional)* **Anthropic API Key** (only needed if running in `anthropic` provider mode; local dev runs fully offline using Ollama)

---

### 2. Set Up Environment & Local Secrets
Create local secrets files (ignored by Git):
```bash
mkdir -p secrets
echo "statesync_migrator_pass" > secrets/pg_migrator_password
echo "statesync_reader_pass"   > secrets/pg_reader_password
echo "statesync_ingest_pass"   > secrets/pg_ingest_password

# Optional: Only needed if config/flags.toml sets provider = "anthropic"
echo "sk-ant-your-key-here"    > secrets/anthropic_api_key
```

---

### 3. Choose Your LLM Provider Mode
StateSync features a **D2 Provider Seam** allowing seamless switching between local open-weight models (Ollama) and cloud APIs (Anthropic Claude).

Edit `config/flags.toml`:
```toml
[llm]
provider = "local"    # "local" for offline dev, "anthropic" for graded metrics & evaluation runs
ollama_url = "http://host.docker.internal:11434"
```

> ⚠️ **`ollama_url` governs GENERATION only — embeddings are a separate,
> fixed path. Get this wrong and you get a connection error on some calls
> but not others, which is more confusing than an outright failure.**
> **Corrected 2026-08-23 (cycle 006 D2, audit 006 F-006-6)** — this note
> used to present two mutually exclusive topologies for "the models,"
> plural, singular. There is only one topology for generation; embeddings
> are never part of the choice.
>
> **Embeddings (`mxbai-embed-large`) always go to the Dockerized `ollama`
> service**, `http://ollama:11434` — `mcp`'s `OLLAMA_URL` is set directly in
> `docker-compose.yml` and does not read `ollama_url` or any other flag.
> **This is true regardless of which generation topology you pick below.**
> Whichever topology you choose, `mxbai-embed-large` still needs to be
> pulled into the *container*, even if you never touch it for anything
> else — `docker compose exec ollama ollama pull mxbai-embed-large`.
>
> **Generation (`qwen2.5:7b`) follows `ollama_url`, and there are two valid
> settings:**
>
> | You are running generation on… | Set `ollama_url` to | Pull `qwen2.5:7b` with |
> |---|---|---|
> | The same Dockerized `ollama` service the embeddings already use | `http://ollama:11434` | `docker compose exec ollama ollama pull qwen2.5:7b` |
> | A **native** Ollama app on the host (macOS/Windows desktop install) | `http://host.docker.internal:11434` | `ollama pull qwen2.5:7b` on the host |
>
> **The value currently committed is `http://host.docker.internal:11434`**, because this repo's development machine runs a native Ollama app for generation — **and pulls `mxbai-embed-large` into the container regardless**, per above. **If you'd rather generate from the container too**, pull `qwen2.5:7b` there and change `ollama_url` to `http://ollama:11434`; `docker-compose.yml`'s own comment above the `ollama` service spells out exactly this. `host.docker.internal` resolves only from inside a container — it is never reachable from the host itself, so host-side scripts needing a real completion must run via `docker compose exec orchestrator …`.

> 💡 **Developer Note:** When `provider = "local"`, no Anthropic API key is required and the system starts completely offline.

---

### 4. Pull Local LLM & Embedding Models (First Time Only)

**`mxbai-embed-large` always goes into the Dockerized `ollama` container** —
`mcp` reaches it there unconditionally (see step 3's note), regardless of
your `ollama_url` setting:
```bash
docker compose up -d ollama
docker compose exec ollama ollama pull mxbai-embed-large
```

**`qwen2.5:7b` (generation) goes wherever `ollama_url` points** (see the
table in step 3):

**Dockerized Ollama** (`ollama_url = "http://ollama:11434"`):
```bash
docker compose exec ollama ollama pull qwen2.5:7b
```

**Native host Ollama** (`ollama_url = "http://host.docker.internal:11434"`):
```bash
ollama pull qwen2.5:7b
```

Confirm the right model is present in the right place — a missing model
surfaces as an HTTP 404 (`model ... not found`) on the first query or
ingest, not at startup:
```bash
docker compose exec ollama ollama list   # MUST show mxbai-embed-large always;
                                          # also qwen2.5:7b if generating here
ollama list                              # native host — only relevant if
                                          # ollama_url points here for generation
```

---

### 5. Launch StateSync Stack & Seed Database
```bash
# 1. Start all 7 microservices
docker compose up -d

# 2. Run Database Migrations & Fingerprint Calculation
#    (includes ss_embed, the Part E1 vector.hybrid_select function)
bash scripts/migrate.sh

# 3. Seed Database with Frozen Synthetic ONDC Fixtures
python scripts/seed.py
```

If `docker compose build` stalls indefinitely with no output (a known Docker
Desktop BuildKit issue on some hosts, unrelated to this repo), fall back to
the classic builder:
```bash
DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0 docker compose build
```

---

### 6. Ingest the Document Corpus (Part E1 — Hybrid Knowledge)
The `docs` and `hybrid` routes answer from a corpus of supplier agreements,
SLA annexures, and policy/SOP documents. This is a **one-time, operator-run
step** — never invoked by the orchestrator or any agent (A1) — using its own
`statesync_ingest` database role, distinct from the `statesync_reader` role
every live query uses.
```bash
# 1. Generate the synthetic PDF corpus (frozen once generated; re-run only to regenerate)
python scripts/gen_document_corpus.py

# 2. Parse, chunk, embed, and load into documents/document_chunks
python scripts/ingest.py
```

---

### 7. Seed the Adversarial Test Corpus (Part F — Security Suite)
`scripts/verify_security.sh` (F-V1 / M8) needs specific adversarial payloads
already present in `delivery_events.note` / `disputes.narrative` before it
runs — this seeds them (also via `statesync_ingest`, same isolation as step 6):
```bash
python scripts/seed_adversarial.py
```

---

### 8. Access Applications & Dashboards
- **Frontend Dashboard:** [http://localhost:3000](http://localhost:3000)
- **Orchestrator API:** [http://localhost:8090/health](http://localhost:8090/health)
- **MCP Service API:** [http://localhost:8080](http://localhost:8080)
- **Evaluator API:** [http://localhost:8095/health](http://localhost:8095/health) (`GET /history`, `POST /run`)

---

### 9. Run the Verification Suites
```bash
# Config discipline & literal lint (A0.2) — orchestrator/, mcp/, evaluator/
bash scripts/lint-no-literals.sh

# Phase B structural + golden-query suite (B-V1..B-V9)
bash scripts/verify_b.sh

# Phase E2 — A7 dual validation, CSP/vega-interpreter, malicious-spec suite
python scripts/verify_e2.py

# Phase F — seams exist (A0.5/Appendix 4), vision-scorer registration seam
python scripts/verify_seams.py

# Phase F — M8 adversarial security suite (binary; run step 7 first)
bash scripts/verify_security.sh

# Phase F — M9 routing determinism + tool-ceiling over-exposure experiment
# (writes docs/f_v2_report.json; refuses to overwrite an existing report --
#  pass --out <new path> to record a re-run alongside it)
python scripts/experiment_tool_ceiling.py

# Phase E2 — E2-V1: run all 30 chart queries live, validate each response
# against A7 (binary), then score the generated specs for M7.
python scripts/run_e2_v1.py --out docs/e2_v1_generated_<label>.json
python scripts/score_specs.py --score docs/e2_v1_generated_<label>.json
```
Model-sensitive re-verification (M2/M4/M6/M7 and related checks) reuses each
phase's own bench script unchanged — see `docs/metrics-report.md` for the
full list and `docs/rebaseline_runbook.md` for the graded (Anthropic)
re-baseline procedure.

---

## 🏗️ System Architecture & Service Network Isolation

StateSync strictly isolates database infrastructure (`db_net`) from public-facing/app services (`app_net`). The **Model Context Protocol (MCP)** service bridges the network boundary as the single credential-holding data gateway.

```
┌─────────────────── db_net (internal, non-routable) ───────────────────┐
│  postgres:5432            qdrant:6333                                 │
└───────────────────────────────────────────────────────────────────────┘
                                ▲
                                │ (MCP is the ONLY bridge; sole credential holder)
                                ▼
┌─────────────────── app_net (application network) ────────────────────┐
│  mcp:8080    orchestrator:8090    frontend:3000    evaluator:8095     │
└───────────────────────────────────────────────────────────────────────┘

        ollama:11434 sits on BOTH networks (it holds no credentials
        and fronts no data): db_net so mcp can embed, app_net so the
        orchestrator can generate. postgres and qdrant remain
        db_net-only — an orchestrator process attempting a direct
        database connection fails at the network layer (B-V3).
```

### Microservice Directory

| Service | Port | Description |
|---|---|---|
| `postgres` | `5432` | PostgreSQL + PostGIS spatial extension + pgvector |
| `qdrant` | `6333` | Vector database storing AgentSM execution trajectories |
| `ollama` | `11434` | Local LLM host. Always serves `mxbai-embed-large` embeddings to `mcp`; `qwen2.5:7b` generation only if `llm.ollama_url` points here instead of a native host Ollama — see step 3/4 above |
| `mcp` | `8080` | Model Context Protocol server — sole database credential holder |
| `orchestrator` | `8090` | LangGraph multi-agent pipeline (`supervisor`, `sql_agent`, `synthesizer`) |
| `frontend` | `3000` | Nginx-served stateless UI with Vega-Embed & Vega-Interpreter (CSP compliant) |
| `evaluator` | `8095` | Continuous evaluation engine & metric calculation service |

---

## 🛡️ 3-Layer SELECT Security Model (A4.5)

StateSync enforces absolute `SELECT`-only execution to prevent data modification or exfiltration:

| Layer | Component | Mechanism |
|---|---|---|
| **Layer 1: Parser Gate** | MCP Service | Parses raw SQL AST with `pglast` (PostgreSQL parser engine). Rejects multi-statement queries, data-modifying CTEs (`WITH x AS (INSERT...)`), and non-SELECT statements prior to DB dispatch. |
| **Layer 2: Session Guards** | MCP Connection Pool | Configures connection session with `SET TRANSACTION READ ONLY`, `statement_timeout = 15000ms`, and mandatory transaction rollback on release. |
| **Layer 3: PostgreSQL Role** | Postgres Engine | Connects as restricted `statesync_reader` role possessing `GRANT SELECT` privileges exclusively. Any write attempt causes PostgreSQL error `SQLSTATE 42501 (permission denied)`. |

---

## 📊 Current Progress & Phase B Deliverables Status

All Phase B core deliverables and D2 provider seam refactorings are **fully implemented, tested, and verified**:

- [x] **Deliverable 1:** Docker Compose orchestration across 7 isolated containers.
- [x] **Deliverable 2:** Configuration system (`config/statesync.toml`, `config/flags.toml`, `config/error_taxonomy.json`) with zero hardcoded code literals.
- [x] **Deliverable 3:** Schema migrations, role privileges (`statesync_reader`, `statesync_ingest`), and dynamic schema fingerprint computation.
- [x] **Deliverable 4:** Frozen synthetic ONDC data fixtures (50 locations, 30 sellers, 10 LPs, 550 orders, 1,917 delivery events, 80 disputes).
- [x] **Deliverable 5:** Model Context Protocol (MCP) JSON-RPC 2.0 service with 3-layer security enforcement.
- [x] **Deliverable 6:** LangGraph Orchestrator pipeline with Entry, Supervisor, Specialist SQL Agent, and Synthesizer nodes.
- [x] **Deliverable 7:** D2 LLM Provider Seam (`orchestrator/llm/provider.py`) supporting local Ollama models and cloud Anthropic APIs.
- [x] **Deliverable 8:** Stateless frontend UI with strict CSP headers (no `unsafe-eval`) and Vega-Interpreter support.
- [x] **Deliverable 9:** Evaluator service stub for phase transition readiness.
- [x] **Deliverable 10:** Comprehensive verification suite (`scripts/verify_b.sh`) validating tests B-V1 through B-V9.

---

## 🤝 Developer Partner Collaboration Guide

### 1. Code & Configuration Discipline (A0.2)
- **No Hardcoded Literals:** Never place numeric constants, model names, or timeouts inside Python application code. Add them to `config/statesync.toml` or `config/flags.toml` and load them via `config_loader.py`.
- **Run Literal Lint:** Verify compliance anytime you touch orchestrator or MCP code:
  ```bash
  bash scripts/lint-no-literals.sh
  ```

### 2. Working on LLM Nodes
- All nodes (`supervisor.py`, `sql_agent.py`, `synthesizer.py`) call the LLM factory:
  ```python
  from llm.provider import get_agent_llm
  llm = get_agent_llm("sql")
  response = await llm.complete(system=..., messages=..., max_tokens=...)
  ```
- **Never import `anthropic` or vendor SDKs directly in nodes.** Keep provider logic inside `orchestrator/llm/adapters/`.

### 3. Testing Local Changes
1. Run local stack with `provider = "local"`.
2. Run `bash scripts/verify_b.sh` to verify end-to-end routing, database isolation, security enforcement, and schema compliance.
3. Check `walkthrough.md` or `docs/spec-gaps.md` for specification details or open questions.

---

## 📅 Roadmap & Upcoming Development Phases

- 🔄 **Phase C:** AgentSM Memory (Qdrant trajectory storage), schema fingerprint caching, active interrogation, and C2 correction loop.
- 🗺️ **Phase D:** Spatial analytics engine, PostGIS queries, and DigiPin reference integration.
- 📈 **Phase E:** Vega-Lite chart synthesis engine and malicious spec sanitizer.
- 🎯 **Phase F:** Evaluator metrics calculation engine and automated benchmark reporting.

---

## 📄 License & Project Standards
Internal project repository for ONDC multi-agent analytics development. All contributions must adhere to specification guidelines defined in `statesync_phase_B.md`.
