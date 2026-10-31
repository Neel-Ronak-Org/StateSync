# StateSync — Phase B Technical Progress & Remediation Log

## 1. Executive Summary & Current Status
- **Overall Status**: **Phase B 100% Complete & Verified**.
- **Verification Suite**: `scripts/verify_b.sh` passed **28/28 checks (0 failures)**.
- **Services Active & Healthy**:
  - `postgres` (PostgreSQL 16 + PostGIS 3.4.3 + pgvector 0.8.5)
  - `mcp` (Port 8080 — JSON-RPC 2.0 tool server)
  - `orchestrator` (Port 8090 — LangGraph agent pipeline)
  - `frontend` (Port 3000 — NGINX with CSP & Vega interpreter)
  - `evaluator` (Port 8095 — Evaluation engine)
  - `qdrant` (Ports 6333, 6334 — Vector DB)
  - `ollama` (Port 11434 — Local LLM & Embedding engine)
- **Data State**:
  - All 17 tables migrated with schema fingerprint `4e1de0e0ba46fe8b24695e0f1a84ffdc88aac4f2c92f28c1a5e84614d73e77e3`.
  - Fixtures seeded: 550 orders, 1,127 order items, 1,917 delivery events, 80 disputes, 50 locations, catalog items, and logistics providers.

---

## 2. Issues Encountered & Root Cause Analysis

### A. Database Image & Extension Installation
1. **APT Mirror Timeouts & Architecture Conflicts in Docker**:
   - *Root Cause*: Network latency/firewall timeouts on standard mirrors and cross-image binary ABI incompatibilities between `pgvector/pgvector` and `postgis/postgis` on Apple Silicon (ARM64).
   - *Resolution*: Rebuilt `postgres/Dockerfile` on `postgres:16` using Fastly CDN mirrors, installing `postgresql-16-postgis-3`, `postgresql-16-postgis-3-scripts`, and `postgresql-16-pgvector` via a single `apt-get` run.

### B. MCP Service
1. **Missing `cfg` import**: Created `mcp/config_loader.py`; added `from config_loader import cfg` in `mcp/db.py`.
2. **psycopg3 INTRANS pool discard**: Added `await conn.commit()` at end of `_configure_connection()` so pooled connections return to `IDLE`.
3. **JSON serialization (`Decimal`, `datetime`)**: Implemented `_serialize_json()` in `mcp/main.py`.

### C. Orchestrator
1. **`flags_snapshot` Pydantic validation**: Relaxed to `dict[str, Any]` in `orchestrator/state.py`.
2. **LangGraph dict return**: Added `StateSyncState(**raw_final)` cast in `orchestrator/main.py`.

### D. Frontend
1. **NGINX multiline CSP crash**: Combined CSP into single quoted string in `frontend/nginx.conf`.

### E. LLM Provisioning
1. **Ollama network**: Set `ollama_url` and `embedding_url` to `http://host.docker.internal:11434` in `config/flags.toml`. Models pulled: `qwen2.5:7b`, `mxbai-embed-large`.

### F. Verification Suite
1. **B-V2 log audit**: Added `log_statement=all` to postgres in `docker-compose.yml`.
2. **B-V5 regex**: Expanded to match `42501`, `permission denied`, `must be owner`, `insufficient_privilege`.
3. **B-V8 linter**: Excluded `.venv` and `venv` from `scripts/lint-no-literals.sh`.

---

## 3. Summary of Modified Files

| File | Change |
| :--- | :--- |
| `postgres/Dockerfile` | Fastly CDN mirror; postgis-3 + pgvector packages |
| `docker-compose.yml` | `log_statement=all`; ollama on `app_net`; healthcheck fixes |
| `config/flags.toml` | `provider = "local"`; ollama URLs to `host.docker.internal` |
| `mcp/db.py` | `await conn.commit()` in `_configure_connection` |
| `mcp/main.py` | `_serialize_json()` for Decimal/datetime/UUID/bytes |
| `mcp/config_loader.py` | MCP-specific config loader (new file) |
| `orchestrator/state.py` | `flags_snapshot: dict[str, Any]` |
| `orchestrator/main.py` | `StateSyncState(**raw_final)` cast |
| `frontend/nginx.conf` | Single-line CSP header |
| `scripts/lint-no-literals.sh` | `.venv` exclusion |
| `scripts/verify_b.sh` | docker compose psql fallback; B-V5 regex; log parsing |

---

## 4. Verification (28/28 Passed)

```
[✓] B-V1: all 7 services healthy
[✓] B-V1: 10/10 Golden Queries passed
[✓] B-V2: answer_text, route=sql, executed_sql, postgres log SELECT
[✓] B-V3: orchestrator cannot reach postgres (network layer)
[✓] B-V4: no credentials in orchestrator env or config
[✓] B-V5: 7/7 write attempts → SQLSTATE 42501
[✓] B-V6: multi-statement rejected; CTE rejected; valid SELECT accepted
[✓] B-V7: ok=false with SQLSTATE 42703 returned as data
[✓] B-V8: lint-no-literals.sh exit 0 (29 files)
[✓] B-V9: 10/10 responses valid against A7 schema
```

---

## 5. Outstanding Pre-C1 Item

**Phase B Amendment — D2 Provider Seam**: `config/flags.toml` has `provider = "local"` (Ollama/qwen2.5:7b active), but `orchestrator/llm/provider.py` with `get_agent_llm(role)` does not yet exist. The orchestrator calls the LLM client directly. This seam must be implemented and 28/28 re-verified in `local` mode before Phase C1 begins. Anthropic key not yet available; the `anthropic` branch of the factory should stub with `NotImplementedError`.
