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

## BD3 — join_cardinality is out of scope for the deterministic classifier

**Decided**: `error_taxonomy.json` gets a new rule for `aggregation_misuse`
(SQLSTATE `42803`, a real, deterministic Postgres grouping error) but **not**
for `join_cardinality`. `join_cardinality` (a fan-out join silently
double-counting an aggregate) produces `ok:true` with a plausible non-empty
result — there is no sqlstate, message, or empty-result signal to match on.
The mq_10 seed (Appendix 2.4) is kept in `fixtures/malformed_queries.json`
exactly as specified, but is documented and reported as a seed that never
reaches the classifier at all, not as a repair-rate failure.
**Alternative considered**: add a heuristic (e.g. compare a `COUNT(DISTINCT
...)` against the raw join row count, or route suspiciously-round aggregate
results back through the LLM for a second look) that would let the loop
detect and correct this class of error.
**Why not**: A3 defines the classifier as "deterministic and non-LLM: a
mapping from engine error codes and regex patterns" — a cardinality heuristic
is neither. Building one would be inventing a new detection mechanism the
Strategy Document is silent on, which A0.1 says to flag, not fill. It would
also change the loop's control flow (what counts as "worth retrying"), which
A3's own rule for adding categories explicitly rules out ("never means
changing the loop's control flow").
**Hook**: if a future phase wants join_cardinality genuinely detected, it
needs its own decision (likely a `Scorer`-registry-style semantic check, not
a taxonomy rule) — this BD only closes the C2 gap, it does not propose that
mechanism.

## BD4 — permission_denied must also match Layer 1 rejections, not only Layer 3's 42501

**Decided**: added two taxonomy rules for Layer 1 failures, not one --
`message_regex: "(?i)^\[Layer 1 Gate\] Rejected"` -> `permission_denied`
(statement-type/multi-statement/data-modifying-CTE rejections: a genuine
attempt to do something other than SELECT), and separately `message_regex:
"(?i)^\[Layer 1 Gate\] SQL parse error"` -> `syntax` (pglast couldn't parse
the SQL at all -- a retriable mistake, not a security-boundary probe).
First implemented as a single blanket `^\[Layer 1 Gate\]` rule; caught by
bench_correction.py (C2-V2) misclassifying mq_04 (`SELECT FROM orders
WHERE;`, an unparseable-but-innocent syntax error) as terminal
`permission_denied` instead of retriable `syntax`, since pglast rejects
that string at parse time with the same `[Layer 1 Gate]` prefix as a
statement-type rejection, before Postgres's own engine ever assigns a real
`42601`. Split into two rules by message shape once this was found.
**Alternative considered**: leave it as-is, accept that write attempts
classify as `unclassified` (the fallback).
**Why**: mq_07 (Appendix 2.4, `UPDATE orders SET order_value = 0;`) and
C2-V3 both require this to classify as `permission_denied`. But a write
statement submitted through `data.run_select` is caught by Layer 1 (the
pglast statement gate, `mcp/tools/data_tools.py`) before it ever reaches the
engine — Layer 1's rejection carries `sqlstate: null` and a
`"[Layer 1 Gate] ..."` message, never SQLSTATE `42501` (that code is Layer
3's signal, reached only by bypassing Layer 1/2 directly against the
database role, per B-V5). Through the normal SQL Agent path, Layer 3's
`42501` is architecturally unreachable for a write attempt — Layer 1 always
fires first. Without this rule, every write attempt (and every
multi-statement or data-modifying-CTE submission) would silently fall
through to `unclassified`, which is also terminal but obscures the actual
cause and would make C2-V3 (and F-V1 Class 2's re-run of write-attempt
queries) unable to assert the specific category the spec names.
**Hook**: `config/error_taxonomy.json`. If Layer 1's rejection message
format ever changes, this regex must change with it — there is no
compile-time link between `data_tools.py`'s `f"[Layer 1 Gate] {rejection}"`
prefix and this rule.
