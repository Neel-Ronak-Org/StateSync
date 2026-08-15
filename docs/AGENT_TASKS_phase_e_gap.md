# StateSync — Agent Task Packet: Anthropic-Gap Work (pre-Phase-E2 close)

**Audience:** the coding agent with the StateSync repository checked out.
**Author:** audit pass, 2026-08-15. **Companion docs:** `phase_e_readiness_call.md`
(rationale), `BD6_model_ceiling.md`, `strategy_addendum_model_config.md`,
`rebaseline_runbook.md`.

**Ground rules (read first):**
- Obey Build Spec §A0.1–A0.6. If the spec is silent on something you hit, **flag
  it in `docs/spec-gaps.md`, do not invent behavior.**
- No numeric literals in application source (A0.2) — new constants go in
  `config/statesync.toml` or `config/flags.toml`. Run `scripts/lint-no-literals.sh`
  after every task; it must exit 0.
- Do not reopen D1–D15 or edit BD1–BD5. You may ADD BD6 (provided).
- Every task below has an **Acceptance** block. A task is done only when its
  Acceptance is met AND `scripts/verify_b.sh` structural checks still pass with
  no new regression (per A0.4).
- Do all quality-number reporting honestly. Local model = `qwen2.5:7b`; label all
  figures as local, not graded.

Work the tasks in the order given. Tasks 1–3 are documentation (no code risk).
Tasks 4–5 are model-independent code fixes. Tasks 6–9 are the model-independent
half of Phase E2. Task 10 is the deferred, model-gated half — do NOT attempt its
quality numbers until the re-baseline runbook runs.

---

## Task 1 — Record BD6 (documentation)

**What:** Append the entry in `BD6_model_ceiling.md` verbatim to
`docs/phase_b_amendment.md`.
**Why:** C1-V3, C2-V1 (cond 3–4), and D-V6 have unmet written pass-conditions
carried as "known limitations" with no recorded decision — a latent A0.4
violation. BD6 makes the disposition explicit and re-baseline-gated.
**Acceptance:** `docs/phase_b_amendment.md` ends with a BD6 section matching the
provided text; no other BD entries changed.

## Task 2 — Record the Strategy addendum (documentation)

**What:** Insert `strategy_addendum_model_config.md`'s §7.0 into
`StateSync_Strategy_Document.md` at the head of §7 (Success Metrics).
**Why:** The local-vs-graded model distinction — on which every quality number
depends — is currently undocumented.
**Acceptance:** §7 opens with the §7.0 subsection; §7's existing M1–M10 text is
unchanged below it.

## Task 3 — Flip the memory default and document it

**What:** In `config/flags.toml` set `memory.enabled = false` (was `true`). Add a
one-line comment: `# BD6/readiness call: default false until graded re-baseline;
memory-on regresses M2 + golden rate on the local model. A/B still run by toggling.`
Add a short note to `docs/spec-gaps.md` (or a `docs/decisions.md`) recording this
as a reversible flag default, not a BD.
**Why:** On the local model `memory.enabled=true` regresses M2 (C1-V3) and drops
golden 10/10→7/10. The flag stays a first-class D13 control; only the shipped
default changes.
**Acceptance:** `flags.toml` default is `false`; `flags_snapshot` still captures
it at entry; `scripts/bench_memory.py` and `scripts/check_consistency.py` still
run both flag states (they toggle explicitly, so they are unaffected). Re-run
`scripts/verify_b.sh`: its golden rate should return to the memory-off baseline
(expect ~10/10, modulo the documented run-to-run variance and the pre-existing
`b_008`/`b_010` cases).

## Task 4 — Fix interrogation target selection (model-independent code)

**What:** In `orchestrator/nodes/sql_agent.py`, active interrogation
(attempt 0) currently samples a fixed ~4 tables schema-wide (`catalog_items`,
`delivery_events`, `disputes`, `document_chunks`) regardless of the question.
Change target selection to derive from the tables the planned query actually
references. Minimal viable approach: after the model proposes its first candidate
SQL (or its intended tables), parse the referenced table names (reuse `pglast`,
already a dependency via the Layer-1 gate) and restrict `data.sample_rows` calls
to those tables (intersected with real tables from `schema.list_tables`), capped
at `sql.interrogation_max_samples`. If no tables can be parsed, fall back to the
current schema-wide behavior (do not regress).
**Why:** Sampling the wrong tables wastes the interrogation budget and helps no
model. Targeting the actual FROM/JOIN tables is plumbing, not model quality — it
improves the graded run too. (This is the simplification C2's log flagged and
GAP-adjacent; if implementing reveals the spec is silent on the mechanism,
record it in `docs/spec-gaps.md` rather than guessing further.)
**Acceptance:** For at least 3 distinct `phase_c2.json` questions touching
different tables, the `data.sample_rows` targets differ and match the question's
tables (confirm via `docker logs` on `mcp`). Interrogation still populates
`interrogation_samples` (≤ cap, non-empty) before any `data.run_select`, i.e.
C2-V7 still passes. No literal table names hardcoded in source (A0.2).

## Task 5 — Exclude `spatial_ref_sys` from schema listing (model-independent code)

**What:** In `mcp/tools/schema_tools.py`, `schema.list_tables` should exclude the
PostGIS system table `spatial_ref_sys` (and any other PostGIS/system catalog
tables that are not part of the A5 application schema). Prefer filtering to the
application schema's own tables rather than name-denylisting one table.
**Why:** Every miss-path query currently calls `schema.describe_table` on
`spatial_ref_sys` — constant pure waste on both control and treatment.
**Acceptance:** `schema.list_tables` returns only A5 application tables (17 per
the Phase B log); `spatial_ref_sys` absent. Schema fingerprint (A5 formula) must
be UNCHANGED by this — the fingerprint already excludes system tables by
operating on the application schema; verify the startup fingerprint still equals
the recorded value (`4e1de0e0...` per the Phase B log) and `mcp` still starts
without the mismatch-as-startup-failure firing. If the fingerprint changes, STOP
— that means the fingerprint was including `spatial_ref_sys`, which is its own
bug to flag, not silently absorb.

## Task 6 — Phase E2: A7 output-contract enforcement, both sides (model-independent)

**What:** Ensure the Synthesizer output is validated against the A7 JSON Schema
**in the orchestrator before the response leaves the process** AND **again in the
frontend before rendering** — two independent validations (A7 requirement). On
any schema failure, return the prose answer with `chart: null` and log a
validation failure. **Never** emit a repaired chart.
**Why:** This is deterministic and model-independent; it is the M7-validity and
security half of E2 and is gradeable now.
**Acceptance:** Feed every spec in `fixtures/malicious_specs.json` through the
synthesizer output path; each is rejected → `chart:null` + logged failure, never
rendered, never repaired. `additionalProperties:false` holds at every level of
A7 (do not relax it to make a chart work). A valid chart still passes both
validations.

## Task 7 — Phase E2: CSP-safe rendering (model-independent, security)

**What:** Frontend renders Vega-Lite with `vega-interpreter` (the CSP-safe
expression interpreter), NOT the default compiler that uses `Function`
construction. Set the container CSP to disallow `unsafe-eval`.
**Why:** This is the mechanism E2-V4 must rest on — it makes the guarantee real
rather than apparent. Model-independent.
**Acceptance:** The frontend container's CSP header contains no `unsafe-eval`
(and remains a single-line header — see Phase B's nginx CSP fix). A chart
containing an expression string fails to execute the expression (interpreter,
not eval). E2-V4 passes.

## Task 8 — Phase E2: deterministic M7 spec-score scorer (model-independent)

**What:** Implement the recall-weighted, deterministic specification-correctness
scorer that compares a generated Vega-Lite spec against a reference spec (a
missing encoding penalized far more than an extra stylistic property, per M7).
Register it in the `Scorer` registry (A8, `evaluation.scorers`) alongside
`SpecScorer`/`ExecutionScorer`. Weights load from config (A0.2), not literals.
**Why:** The scoring machinery is deterministic and can be built and unit-tested
now, independent of how good the generated charts are.
**Acceptance:** Unit tests: identical spec → max score; a spec missing one
required encoding scores materially lower than one with an extra cosmetic
property; scorer resolves from the registry by config name (no `if scorer==`
branch). Weights read from config.

## Task 9 — Run the model-independent E2 Verification steps and record results

**What:** Execute every E2 Verification step that does NOT depend on generation
quality (at minimum E2's output-contract validity check and E2-V4 CSP check, plus
the `malicious_specs.json` rejection suite). Record pass/fail honestly in a new
`docs/phase_e2_log.md` following the style of the existing phase logs.
**Why:** These are the gradeable-now half of E2.
**Acceptance:** `docs/phase_e2_log.md` exists, each model-independent E2 step
recorded with evidence (commands/log excerpts), `scripts/lint-no-literals.sh`
exit 0, `scripts/verify_b.sh` structural checks still pass.

## Task 10 — DEFERRED: model-gated E2 quality (do NOT report until re-baseline)

**What:** The M7 chart-*quality* number on real generated charts, and any E2
step whose pass depends on generation quality, are measured but reported as
LOCAL-MODEL figures only, explicitly deferred to the re-baseline runbook — exactly
as E1-V3 (M6) and BD6's checks are.
**Why:** Reporting a local-model quality number as final repeats the exact gap
this whole packet exists to close.
**Acceptance:** `docs/phase_e2_log.md` marks these clearly as "local model,
pending graded re-baseline (see rebaseline_runbook.md)"; no final M7 quality
claim is made.

---

## Global exit criteria for this packet

- Tasks 1–9 complete and their Acceptance blocks met.
- `scripts/lint-no-literals.sh` exit 0.
- `scripts/verify_b.sh` structural checks (B-V2..B-V9) pass; golden rate recorded
  honestly (expect improvement from the Task 3 default flip).
- `docs/phase_b_amendment.md` has BD6; Strategy Document has §7.0;
  `docs/phase_e2_log.md` written.
- Nothing in this packet reports a model-sensitive quality number as final.
- Anything the spec was silent on is in `docs/spec-gaps.md`, not invented.
