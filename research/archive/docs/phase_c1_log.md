# Phase C1 — Semantic Memory: completion log

Date: 2026-08-14. Builds on Phase B (28/28, commit `5975fcc`) and the
Phase B amendment (BD1, commit `e148d7a`).

## Deliverables

1. `agentsm_trajectories` Qdrant collection — created idempotently at mcp
   startup (`mcp/qdrant_store.py`), cosine distance, `memory.embedding_dim`
   (1024) dimensions.
2. `memory.get_trajectory` / `memory.put_trajectory` — `mcp/tools/memory_tools.py`,
   registered in `mcp/main.py` (tool count 4 → 6, under the ceiling of 15,
   no BD needed). Both Qdrant searches filter on `tenant_id` AND
   `schema_fingerprint` inside the query itself (C1-V6).
3. Schema fingerprint — `mcp/fingerprint.py` already existed with the
   correct A5 formula but was never called; now wired into `mcp/main.py`'s
   lifespan via the ordinary `statesync_reader` pool. See "Bugs found and
   fixed" below — this took two real fixes before it actually worked.
4. `TrajectoryStore` (`orchestrator/memory_store.py`, resolves
   `memory.provider` from `statesync.toml`/cfg — a locked constant, not a
   flag) and `SchemaRetriever` (`orchestrator/schema_retriever.py`, resolves
   `schema_retriever.provider` from `flags.toml`) — both mirror the
   `llm/provider.py` factory pattern; no `if provider ==` in node code.
5. `orchestrator/nodes/sql_agent.py` — consults memory before construction
   (miss → `SchemaRetriever`, the Phase B path; hit → compact insight
   context), writes back via `TrajectoryStore` only on a miss-path success.
6. `memory.enabled` — already present in `flags.toml` from Phase B; captured
   in `flags_snapshot` and read from there (never the live singleton) in
   `sql_agent.py`, per A2.
7. `scripts/bench_memory.py`.
8. `fixtures/golden/phase_c1.json` — 10 base (reused verbatim from
   `phase_b.json`), 10 exact repeats, 10 paraphrases.

Two scripts beyond the Deliverables list, both directly in service of C1's
own verification and reusable for Phase F's unchanged re-run of C1-V3/V1:
`scripts/check_consistency.py` (C1-V3) and `scripts/refresh_fingerprint.sh`
(recompute + apply + restart, factored out of `migrate.sh` since C1-V4's
rename/revert test needed it standalone).

## Verification results

**C1-V1 — Memory efficiency (M1). PASS.**
Control (memory off) vs. treatment (memory on), 20 repeat+paraphrase queries,
base 10 excluded from comparison per spec:
- Tokens: 40,735 → 24,787 (**-39.2%**)
- Trajectory steps: 320 → 86 (**-73.1%**)
Both comfortably exceed AgentSM's cited -25%/-35% reference range. Full
per-query data in `docs/bench_memory_report.json`.

**C1-V2 — Paraphrase generalization. PASS.**
9/10 paraphrases produced `memory_hit = true` (≥ 6 required). The one miss
(`c1_paraphrase_010`, "on average, how many delivery events happen per
fulfillment?") has nothing to hit against — its base query never
successfully wrote a trajectory in any run (see "avg(record)" below).

**C1-V3 — Consistency (M2). FAIL, honestly recorded.**
Mean distinct result sets: control 1.0, treatment 1.5 (5 of 10 base queries
regressed). Full data in `docs/consistency_report.json`. Diagnosis: the
control run is perfectly consistent (1/5 for all 10 queries — full-schema
generation is itself deterministic at temperature 0). The treatment
inconsistency is concentrated exactly where run 1 (necessarily a cold miss
right after the collection reset) differs from runs 2-5 (hits) — for 5 of
10 queries the insight-based hit-path SQL doesn't reproduce the same result
as the full-schema miss-path SQL for the same question. This is not
run-to-run instability within a mode; runs 2-5 agree with each other in
every case observed. See "Known limitations" below — this is the same root
cause as the B-V1 regression found during the post-verification re-check.

**C1-V4 — Stale-trace rejection (D4). PASS**, after fixing two real bugs
(below) that made this fail entirely on the first attempt. Final sequence,
all clean: populate base 10 → 9/10 hit on repeat (base_010 has the same
pre-existing generation failure as always) → rename
`fulfillments.delivered_at` → `completed_at`, recompute fingerprint, restart
mcp → **all 10 return `hit=false`**, the 9 with existing traces show
`rejected_stale=1`, zero generated SQL references `delivered_at`, all
complete via the cold path → revert the rename, recompute fingerprint
(reproduces the exact original hash, `cf4caa5c...`, confirming it is a pure
function of schema shape) → **all 9 traces become `hit=true` again**.

**C1-V5 — No SQL in memory. PASS.** Direct `memory.put_trajectory` with
`insight="SELECT * FROM orders"` rejected server-side
(`-32602 Invalid params`). Scanned all 18 stored payloads:
zero matches of `(?i)\bselect\b.*\bfrom\b`.

**C1-V6 — Tenant scoping. PASS.** Code audit: the only two Qdrant search
call sites (`memory_tools.py::get_trajectory`, both the fingerprint-filtered
and tenant-only searches) both include a `tenant_id` equality
`FieldCondition`. No other file in `mcp/` issues a Qdrant search.

## Bugs found and fixed (not pre-existing C1 scope, but blocking)

1. **`information_schema.table_constraints` invisible to `statesync_reader`.**
   Per the SQL/PostgreSQL spec, that view (and `key_column_usage`/
   `constraint_column_usage`, joined through it) only shows constraints to a
   role that owns the table or holds a privilege *other than SELECT*.
   `statesync_reader` holds SELECT only, by design (Layer 3) — so
   `schema.describe_table`'s `foreign_keys` had been silently empty since
   Phase B, and `mcp/fingerprint.py`'s FK query inherited the same bug,
   making the mismatch-as-startup-failure check permanently unsatisfiable
   the moment it was actually wired in (this session, Deliverable 3).
   Fixed in `schema_tools.py`, `fingerprint.py`, and `migrate.sh` by
   switching to `pg_constraint`/`pg_class`/`pg_attribute` directly, which
   carry no such privilege-tier restriction.
2. **`schema_versions` upsert couldn't survive a real schema change.**
   `migrate.sh`'s original `INSERT ... ON CONFLICT (fingerprint) DO NOTHING`
   would hit the `schema_versions_one_current` partial unique index the
   first time a migration actually changed the fingerprint. Fixed to retire
   the current row before inserting/reactivating the new one — this is
   exactly the path C1-V4's rename/revert exercises.
3. **`state.merge()` (A2's SUM/APPEND/LWW reducer machinery) was never
   called.** `orchestrator/graph.py`'s node wrappers returned each node's
   raw partial dict straight to LangGraph, which — for a plain Pydantic
   state schema with no `Annotated[..., reducer]` fields — just does
   last-write-wins per key. `metrics` was silently only ever the *last*
   node's numbers, not the sum A2 specifies; nothing in Phase B's own
   verification checks summed metrics, so this was invisible until C1-V1
   needed real numbers. Fixed by routing every node's update through
   `merge()` in the wrapper and returning the full resulting state (so
   LangGraph's overwrite becomes a full, correct replacement). Also fixed:
   `entry_node` was regenerating `query_id` with a fresh UUID every query
   (discarding `main.py`'s), which is harmless under the old no-merge
   behavior but trips `merge()`'s immutable-field assertion the moment it's
   real — `entry_node` now passes `query_id` through unchanged, and
   `_sql_error`/`_stub_message` (deliberately outside the typed schema) are
   now explicitly passed through the wrapper alongside the merged state,
   since `merge()` itself intentionally drops underscore-prefixed keys.
4. **`schema.list_tables`'s fingerprint was a different, weaker formula**
   (a hash of sorted table names only) than `fingerprint.py`'s real A5
   formula used at startup — the original Phase B author's own comment
   flagged this ("this is for cache keying in C1"). A column rename doesn't
   change the table-name list, so this silently defeated D4's entire
   stale-detection guarantee for exactly the case C1-V4 tests. Fixed by
   having `list_tables` call `fingerprint.compute_fingerprint()` directly.
5. **`memory.put_trajectory` over-strict validation.** Required
   non-empty `join_path`, rejecting every single-table query (which
   legitimately has no join to describe) with a 422. A4.8 requires the
   field present, not non-empty. Fixed.
6. **`main.py`'s `state_trace` never exposed `memory_hit`, `memory_trace_id`,
   `schema_fingerprint`, `sql_rows`, or `sql_columns`** — needed for C1-V1
   through C1-V4 to be checkable via the API at all (only `sql_rows_count`
   existed). Added.
7. **`docker compose restart` vs `up -d`.** `restart` cycles the existing
   container without picking up a freshly built image; only `up -d`
   recreates from the current image. `refresh_fingerprint.sh` used
   `restart` and silently ran stale code for one full test cycle before
   this was caught (`scripts/bench_memory.py`/`check_consistency.py`
   already used `restart` correctly for the orchestrator only *between*
   flag toggles, never across a rebuild, so they were unaffected). Fixed.

## Known limitations (honest, not hidden)

- **Hit-path SQL is measurably less reliable than miss-path SQL with the
  local model.** Re-running `scripts/verify_b.sh` after all C1 changes,
  with `memory.enabled=true` left as the shipped default, dropped the
  golden-query pass rate from 10/10 (Phase B baseline, memory never
  consulted) to 7/10 in one run. The two new failures
  (`b_007`/`c1_base_007`, `b_008`/`c1_base_008`) both occurred with
  `memory_hit=true`: the model hallucinated `fulfills` (should be
  `fulfillments`) and `de.delivery_event_id` (should be `de.event_id`)
  when working from the compact insight instead of the full schema.
  `verify_b.sh`'s own B-V1 check does not actually gate on the per-query
  pass count it prints (a pre-existing structural gap, not touched here) —
  its summary still reported 28/0, which is why this needed reading the
  actual per-query output rather than trusting the tally. This is the same
  root cause as the C1-V3 fail above, and is exactly the kind of thing a
  correction loop (C2, out of scope here) is positioned to recover from.
  Not fixed in C1: implementing any retry/fallback here would be C2's
  correction loop built early, which B2/C1's own scope notes explicitly
  rule out.
- **`c1_base_010`** ("what is the average number of delivery events per
  fulfillment?") never once produced a working query across every run in
  this session, under any flag state: the model consistently emits
  `AVG(count.*)` (`avg(record) does not exist`) or similar. Pre-existing,
  unrelated to memory — it fails identically with `memory.enabled=false`.
- **`spatial_ref_sys`** (a PostGIS system table) is not excluded from
  `schema.list_tables`'s returned table list, so every miss-path query
  wastefully calls `schema.describe_table` on it too. Constant overhead on
  both control and treatment equally, so it doesn't affect C1-V1's
  comparison, but it's real waste. Not fixed here — flagging only.
- GAP-1 (`docs/spec-gaps.md`): does a memory-hit success also write a new
  trajectory back? Decided no (see `sql_agent.py` module docstring);
  recorded as a gap since the spec doesn't say either way.
- BD2 (`docs/phase_b_amendment.md`): embedding model (`mxbai-embed-large`
  via the mcp-only Ollama container) — A6 requires an embedding of
  `query_text` but never names a model.
- All numbers above are from `qwen2.5:7b` (local mode — no Anthropic key
  configured yet). Per this repo's own standing warning, none of these
  figures should be treated as representative of the graded/Anthropic
  configuration.

## Regression check

`scripts/lint-no-literals.sh`: PASS (34 files, 0 violations).
`scripts/verify_b.sh`: summary reports 28/0, but see "Known limitations" —
the structural checks (B-V2 through B-V9: network isolation, credential
absence, Layer 1-3 enforcement, failure visibility, config discipline, A7
output contract) all independently pass with no regressions. The golden
query pass rate is a separate, LLM-quality-dependent number that is not
what B-V2 through B-V9 test.
