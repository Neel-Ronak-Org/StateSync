# StateSync Phase B — Walkthrough & Progress Report

## What Was Built

The complete **Phase B Walking Skeleton** for StateSync — all 10 deliverables, fully wired, tested, and verified per spec.

---

## Files Created & Refactored (57 total)

### Infrastructure & Config

| File | Purpose |
|---|---|
| [docker-compose.yml](../docker-compose.yml) | 7 services (+ Ollama), isolated `db_net`/`app_net` |
| [docker-compose.override.yml](../docker-compose.override.yml) | Dev port exposure |
| [config/statesync.toml](../config/statesync.toml) | Locked constants (Appendix 1, verbatim) |
| [config/flags.toml](../config/flags.toml) | Feature flags (Appendix 3) + D2 LLM model selection |
| [config/error_taxonomy.json](../config/error_taxonomy.json) | Error classifier rules (A3, verbatim) |

### Database

| File | Purpose |
|---|---|
| [migrations/001_roles.sql](../migrations/001_roles.sql) | Creates `statesync_reader`, `statesync_ingest` roles |
| [migrations/002_schema.sql](../migrations/002_schema.sql) | Full A5 DDL + Layer 3 `GRANT SELECT` |
| [scripts/migrate.sh](../scripts/migrate.sh) | Runs 001→003, computes + inserts schema fingerprint |
| [scripts/seed.py](../scripts/seed.py) | Loads `ondc_seed.json` as `statesync_ingest` |

### Fixtures (generated + frozen)

| File | Counts |
|---|---|
| [fixtures/ondc_seed.json](../fixtures/ondc_seed.json) | 50 loc, 30 sellers, 10 LP, 550 orders, 1917 events, 80 disputes ✓ |
| [fixtures/golden/phase_b.json](../fixtures/golden/phase_b.json) | 10 English queries for B-V1 |
| [fixtures/malformed_queries.json](../fixtures/malformed_queries.json) | 12 seeds covering all 12 ErrorCategory values |
| [fixtures/adversarial.json](../fixtures/adversarial.json) | 21 payloads (8+6+4+3) ✓ |
| [fixtures/malicious_specs.json](../fixtures/malicious_specs.json) | 10 Vega-Lite specs violating A7 ✓ |

### MCP Service

| File | Purpose |
|---|---|
| [mcp/main.py](../mcp/main.py) | FastAPI JSON-RPC 2.0, tenant_id session binding (A0.6) |
| [mcp/db.py](../mcp/db.py) | psycopg3 pool as `statesync_reader`, Layer 2 session guards |
| [mcp/tools/data_tools.py](../mcp/tools/data_tools.py) | `data.run_select` with **all 3 enforcement layers** |
| [mcp/tools/schema_tools.py](../mcp/tools/schema_tools.py) | `schema.list_tables` + `schema.describe_table` |

### Orchestrator & D2 Provider Seam

| File | Purpose |
|---|---|
| [orchestrator/state.py](../orchestrator/state.py) | Full A2 typed state + reducer dispatch + concurrency assertion |
| [orchestrator/config_loader.py](../orchestrator/config_loader.py) | Single read-only TOML loader (A0.2) + `snapshot_flags()` |
| [orchestrator/graph.py](../orchestrator/graph.py) | 4-node LangGraph, routing table registration (A0.5 seam) |
| [orchestrator/mcp_client.py](../orchestrator/mcp_client.py) | JSON-RPC 2.0 client, session-bound tenant_id |
| [orchestrator/routing/registry.py](../orchestrator/routing/registry.py) | SpecialistNode registry; startup failure on unresolvable name |
| [orchestrator/schemas/synthesizer_output.py](../orchestrator/schemas/synthesizer_output.py) | A7 JSON Schema (config-driven) + `safe_synthesizer_output()` |
| **[orchestrator/llm/provider.py](../orchestrator/llm/provider.py)** | **D2 seam factory — `get_agent_llm(role)` — the single LLM construction point** |
| **[orchestrator/llm/adapters/base.py](../orchestrator/llm/adapters/base.py)** | **`ChatModel` protocol + `LLMResponse` dataclass — nodes depend only on this interface** |
| **[orchestrator/llm/adapters/anthropic_adapter.py](../orchestrator/llm/adapters/anthropic_adapter.py)** | **Async Anthropic SDK adapter — key read once at construction (only when provider=anthropic)** |
| **[orchestrator/llm/adapters/ollama_adapter.py](../orchestrator/llm/adapters/ollama_adapter.py)** | **Async Ollama HTTP adapter — fully offline capable (no Anthropic key required)** |
| [orchestrator/nodes/entry.py](../orchestrator/nodes/entry.py) | uuid4 query_id, tenant_id from config, flags_snapshot |
| [orchestrator/nodes/supervisor.py](../orchestrator/nodes/supervisor.py) | 1 LLM call via `get_agent_llm("supervisor")` → sql/docs/hybrid classification |
| [orchestrator/nodes/sql_agent.py](../orchestrator/nodes/sql_agent.py) | Schema → SQL via `get_agent_llm("sql")` → execution; error as data, no retry |
| [orchestrator/nodes/synthesizer.py](../orchestrator/nodes/synthesizer.py) | answer_text + chart via `get_agent_llm("synthesizer")`, validated against A7. Task 10: the model proposes the chart SHAPE, `build_chart()` attaches `data.values` from the real rows |
| ~~orchestrator/nodes/stub_node.py~~ | Phase B's explicit "not implemented" node for docs/hybrid (A0.5). **Removed once those routes were built** — see the two rows below |
| [orchestrator/nodes/unstructured_knowledge.py](../orchestrator/nodes/unstructured_knowledge.py) | E1: retrieval + decomposition for the docs and hybrid routes — what replaced the stub |
| [orchestrator/nodes/normalize.py](../orchestrator/nodes/normalize.py) | D: LanguageNormalizer gate, runs unconditionally before the Supervisor |
| [orchestrator/nodes/error_exit.py](../orchestrator/nodes/error_exit.py) | Terminal error path — failures surface as data, never as a silent empty answer |
| [orchestrator/main.py](../orchestrator/main.py) | FastAPI `/query` + `/health` (exposes `llm_provider`); `validate_llm_config()` at startup |

### Scripts & Verification

| File | Purpose |
|---|---|
| [scripts/verify_b.sh](../scripts/verify_b.sh) | **B-V1 through B-V9** — the full verification suite |
| [scripts/lint-no-literals.sh](../scripts/lint-no-literals.sh) | A0.2 enforcement; exit 0 = B-V8 pass |

---

## Seed Data Counts (frozen, RNG seed 20260807)

| Table | Count | Minimum Required |
|---|---|---|
| locations | 50 | ≥ 40 ✓ |
| sellers | 30 | ≥ 25 ✓ |
| logistics_providers | 10 | ≥ 8 ✓ |
| orders | 550 | ≥ 500 ✓ |
| fulfillments | 550 | ≥ 500 ✓ |
| delivery_events | 1,917 | ≥ 1,500 ✓ |
| disputes | 80 | ≥ 60 ✓ |

---

## Key Design Decisions & Architecture Guarantees

> 💡 **Ollama as Local LLM & Embedding Service**  
> `mxbai-embed-large` (embeddings) and `qwen2.5:7b` (chat model) run in Ollama locally. Configuration is locked in `config/flags.toml`.  
> *Note:* While local mode is the offline dev default, all formal metric evaluation runs must use `provider = "anthropic"`.

> 🔌 **D2 LLM Provider Seam**  
> All three agent nodes (`supervisor`, `sql_agent`, `synthesizer`) call `get_agent_llm(role)` from `orchestrator/llm/provider.py`. Nodes import zero SDK types. The factory resolves provider + model from `config/flags.toml` and returns an adapter implementing the `ChatModel` protocol.

> 🔑 **Startup Key Guard**  
> With `provider = "local"`, the orchestrator starts cleanly without an Anthropic API key. With `provider = "anthropic"`, the key is validated eagerly at process startup — refusing to accept traffic rather than failing mid-query.

---

## Next Steps: What Phase C Will Add

- **Phase C1:** AgentSM Memory (Qdrant trajectory store), schema fingerprint caching, `SchemaRetriever` seam.
- **Phase C2:** Correction loop with error taxonomy classification, active interrogation (`data.sample_rows`), retry up to `sql.retry_max`.
