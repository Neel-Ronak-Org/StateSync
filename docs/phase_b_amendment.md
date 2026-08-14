# Phase B Amendment — Build-Level Decisions

Standalone BD log (Appendix 5 equivalent) for this repo. `StateSync_Build_Spec.md`
is not yet checked in under `docs/`; when it is, migrate these entries into its
Appendix 5 verbatim, per the format A0.3 defines: Decided / Alternative considered
/ Why / Hook.

## BD1 — LLM provider seam implemented in Phase B (not deferred to D)

**Decided**: get_agent_llm(role) factory in orchestrator/llm/provider.py,
dispatching on flags.llm.provider. Implemented as part of the Phase B
walking skeleton commit (31d314d).
**Alternative**: Defer to Phase D per A8 interface registry.
**Why**: The seam was cheaper to build alongside the node wiring than to
retrofit later. The A8 registry and Appendix 3 do not list this interface
because they were written before Phase B code was committed — this BD entry
closes that documentation gap per A0.3.
**Hook**: flags.llm.provider — add "anthropic" entry to [llm.models.anthropic]
in flags.toml and set ANTHROPIC_API_KEY when the key is available.

## BD2 — Trajectory embedding model: mxbai-embed-large via the mcp-only Ollama container

**Decided**: `query_text` vectors for the `agentsm_trajectories` collection (A6)
are computed by mcp calling `mxbai-embed-large` on the Ollama instance already
provisioned in docker-compose.yml (`OLLAMA_URL=http://ollama:11434`, db_net-only,
`depends_on: ollama: condition: service_healthy`). Model identifier lives in
`config/statesync.toml` as `memory.embedding_model` (A0.2: no model literals in
source). `memory.embedding_dim = 1024` already matched this model's output
dimension before this decision was recorded, and the model was already pulled
into the container.
**Alternative**: A separate embedding provider/model (e.g. an Anthropic-adjacent
embedding API, or a different local model), configured independently of the
LLM provider seam.
**Why**: A6 requires an embedding of `query_text` but never names a model, and
`config/statesync.toml`'s `[memory]` block previously had no field for one —
an A0.1 gap. The docker-compose infrastructure (ollama container, network
placement, comment "Serves mxbai-embed-large (1024 dims) for trajectory store
embeddings (A6). Only mcp needs it.") already anticipated this exact answer and
the model was already pulled, so this BD entry names the config key rather than
reopening the choice.
**Hook**: `memory.embedding_model` in `config/statesync.toml`. Changing the
model requires re-embedding every stored trajectory (dimension/space changes)
and is itself an A0.3-weight decision, not a config edit.
