# Phase B Amendment — Build-Level Decisions

Running append log of BD entries, in the format A0.3 defines: Decided /
Alternative considered / Why / Hook. `StateSync_Build_Spec.md` is now checked
in under `docs/`, and BD1–BD7 below have been migrated into its Appendix 5,
which is the **consolidated record** later Parts should treat as
authoritative. This file remains the historical trail — each entry recorded
once, at the Part where it was decided — and new BD entries should still land
here first, then be copied into Appendix 5. See `docs/open_items.md` for a
tracked summary of what's still open across BD7 and the other carried-forward
items.

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

## BD5 — ss_embed reads a session-local GUC set by mcp, rather than any
## SQL-text rewriting

**Decided**: `ss_embed(query_text TEXT) RETURNS VECTOR(1024)` is a
`STABLE SQL` function (`migrations/004_ss_embed.sql`) that ignores its own
argument's value and instead reads `current_setting('statesync.query_vector')`.
`vector.hybrid_select`'s handler (`mcp/tools/vector_tools.py`) parses the
submitted SQL with pglast (the same parser Layer 1 already uses), extracts
the literal string argument passed to `ss_embed(...)`, embeds *that text* via
Ollama, then runs `SELECT set_config('statesync.query_vector', '<vector>', true)`
inside the same transaction — before executing the original, **unmodified**
SQL text. `set_config(..., true)` is `SET LOCAL`'s parameterizable
equivalent; it's transaction-scoped and is cleared by the `ROLLBACK` every
mcp request already performs (Layer 2, A4.5), so nothing can leak into the
next pooled use of that connection.

**Alternative considered**: have mcp textually substitute each
`ss_embed('literal')` call in the submitted SQL with a bound parameter
placeholder before execution.

**Why**: A4.7 requires ss_embed to make no network call, with "the
query-side vector... bound as a request-scoped parameter by mcp." Text
substitution would ALSO satisfy that constraint, but it means rewriting
LLM-generated SQL by string surgery — matching and replacing an exact
substring inside text that was not written by mcp itself, with all the
quoting/escaping edge cases that implies — before the SQL is ever executed.
The GUC approach never touches the submitted SQL text at all: Layer 1
validates the *original* SQL (so the security-relevant check runs against
exactly what will execute), and `ss_embed` is a real, genuinely-invoked
function at query time, not a placeholder papered over before submission.
This also keeps the literal argument meaningful to a human or LLM reading
the query (it documents intent) without it being load-bearing for
correctness.

**Hook**: `mcp/tools/vector_tools.py`'s `_check_hybrid_gate` (literal
extraction + the dangerous-function denylist) and
`migrations/004_ss_embed.sql` (the GUC name `statesync.query_vector`) must
stay in sync — there is no compile-time link between them, same caveat as
BD4's regex/message-prefix coupling.

## BD6 — Local-model ceiling is the recorded disposition for C1-V3, C2-V1, and D-V6 (provisional, re-baseline-gated)

**Decided**: Three Verification steps with written pass-conditions that were not
met on the local model are recorded as **model-ceiling limitations** — caused by
`qwen2.5:7b` SQL-generation quality, a single shared root cause — rather than as
defects in StateSync's own logic, **conditional on re-verification under the
graded (Anthropic) configuration per the re-baseline runbook**:

- **C1-V3 (M2 consistency)**: treatment mean distinct-result-sets 1.5 > control
  1.0; five base queries regressed. Root cause: hit-path (compact-insight) SQL is
  less reliable than miss-path (full-schema) SQL on this model — the same finding
  as the golden-rate 10/10→7/10 drop with `memory.enabled=true`.
- **C2-V1 conditions 3–4**: the correction loop never redirects the 2-hop join
  through `orders`; verified unchanged after prompt reordering + more directive
  FK instructions.
- **D-V6 exact count**: generated SQL omitted the `fulfillments` join +
  `status='delivered'` filter (returned 41 vs hand-computed 30); confirmed by
  reproducing 41 from the ground-truth query with that filter removed.

The **structural / mechanism** halves of all three are independently confirmed
passing (memory hit/miss wiring and tenant+fingerprint scoping; error-history
accumulation and terminal-category handling; digipin decode, predicate
construction, GIST index usage, and English/Hinglish parity).

**Alternative considered**: treat each as a hard A0.4 block ("do not open the
next Part with a prior Part's Verification failing") and halt until repaired on
the local model.

**Why not**: all three reproduce as the same 2-hop-join / filter-omission class
already measured across C2 (M4 ≈ 30%) and D-V6, and ≥1 documented, verified
prompt-improvement attempt per case did not move them. Halting would gate the
whole build on local-model capability the design never claimed to require: the
Strategy Document's evaluation philosophy (D13) is a live A/B on StateSync's own
system with one flag toggled, and every A/B *mechanism* works. The absolute
generation quality is explicitly a model property (BD1 seam; config-selected per
A0.2), not a StateSync-logic property.

**Hook**: this BD is **PROVISIONAL and self-voiding**. It is void if the
re-baseline on the graded model (see `docs/rebaseline_runbook.md`) does not
materially improve these three checks. On void, they revert to open A0.4 blocks
and the phases that depend on them are re-opened. Record the graded numbers
against this entry when the key lands; do not report M2/M4 as final until then.

## BD7 — A7's `transform` escape hatch is closed by a separate expression audit,
## not by editing the A7 schema

**Decided**: `validate_synthesizer_output` runs two checks, not one: the A7
JSON Schema exactly as written, and then a deterministic
`_audit_expression_strings` pass over `chart.transform` that rejects
expression-bearing properties (`expr`, `signal`, `calculate`, `test`, `init`,
`update`, `on`) plus `filter` when its value is the expression-string form
rather than the predicate-object form. The frontend validator implements the
same rule independently. A rejection is a rejection: `chart: null` plus a
logged reason, never a repair.

**Why this is needed at all**: A7 states its allowlist "excludes every one" of
Vega-Lite's expression-string properties, and `additionalProperties: false`
delivers that at every level except one — A7 itself declares `transform` items
`{"type": "object", "additionalProperties": true}`. So
`{"calculate": "window.location='https://evil.example.com'"}` satisfies the
literal schema. That is `fixtures/malicious_specs.json` ms_05, whose own
`expected_rejection` field says "transform items allow additionalProperties but
this is audited in E2-V3; transform with expr is a known bypass vector" —
while E2-V3's pass condition requires **10/10 rejected by each validator
independently**. Verified empirically: before this audit the schema rejected
9/10, with ms_05 the sole pass.

**Alternative considered**: tighten the A7 schema — replace
`additionalProperties: true` on transform items with an allowlist of permitted
transform operations.

**Why not**: A7 says a build agent "must not relax `additionalProperties` to
make a chart work". Silently re-tightening a declared shape in the other
direction is the same class of unilateral edit to a locked contract, and it
would also mean inventing an allowlist of transform operations that the
specification never enumerates (A0.1). Keeping the schema byte-faithful to A7
and adding a separately-named, separately-reported check leaves the contract
auditable: the log distinguishes "failed the A7 schema" from "failed the BD7
expression audit", which is exactly the distinction E2-V3's "all three
rejection reasons must appear" language cares about.

**Why `filter` is treated differently from `calculate`**: `filter` is
dual-form — it accepts either an expression string (`"datum.x > 5"`) or a
non-executable predicate object (`{"field": "x", "gt": 5}`). Only the string
form is expression text, so only the string form is rejected. Banning the key
outright would forbid ordinary filtering for no security gain;
`fixtures/golden/phase_e2.json` e2_020 uses the predicate form deliberately, as
the shape the Synthesizer should emit.

**Scope note**: this is defense in depth, not the RCE boundary. The RCE
property comes from D8 (no execution path) and the CSP-plus-`vega-interpreter`
renderer, under which Vega expressions are interpreted, never `eval`'d. This
audit closes the gap between what A7 says its allowlist excludes and what its
schema actually excludes.

**Hook**: `_EXPRESSION_KEYS` / `_DUAL_FORM_EXPRESSION_KEYS` in
`orchestrator/schemas/synthesizer_output.py` and their `A7.EXPRESSION_KEYS` /
`A7.DUAL_FORM_EXPRESSION_KEYS` counterparts in `frontend/index.html` must stay
in step — same no-compile-time-link caveat as BD4 and BD5. E2-V3 run against
both validators is what catches drift.
