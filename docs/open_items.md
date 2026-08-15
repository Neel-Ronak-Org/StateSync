# Open items

Tracked list of everything carried forward out of a phase rather than closed
in it. This file is the single place to check "what's still owed" — each
entry below is a pointer to its full write-up, not a duplicate of it. Update
this file (status column only) as items close; keep the underlying logs
as the historical record and do not edit them to match this summary.

| ID | Description | Status | Re-baseline-gated | Owed at |
|---|---|---|---|---|
| [GAP-6](#gap-6) | A5 schema fingerprint hashes PostGIS's `spatial_ref_sys` alongside the 12 real application tables | Open, escalated | No | Team decision (no Part owns this — see below) |
| [BD7](#bd7) | A7's `transform` items allow `additionalProperties: true`, so a `calculate` transform (ms_05) satisfies the schema; closed by a separate expression audit, not a schema edit | Closed as a decision, not a defect | No | — (recorded; the two-file sync it depends on is the residual risk) |
| [F-V1 Class 4 / E2-V4](#f-v1-class-4--e2-v4-csp-enforcement) | E2-V4's CSP-safe-render claim was proven via a browser-independent mechanism (dynamic-code-construction trap), not by observed enforcement — this test browser doesn't enforce CSP at all | Mechanism proven; enforcement unconfirmed | No | Part F, `F-V1` Class 4 ("re-run E2-V3 and E2-V4 against the frozen build") |
| [c2g_10 routing](#c2g_10-routing) | "List sellers whose contracted SLA hours has not been set" routes to `docs` instead of `sql` — the word "SLA" pulls a plainly relational question toward the document route | Open, unpatched | No | Part F, `F-V2` (M9 routing determinism) |
| [Task 10 / E2-V1 — M7 quality](#task-10--e2-v1--m7-quality) | Synthesizer still emits `chart: null` on every route; M7 chart-quality score cannot be measured until it's taught to populate `chart` | Blocked on Synthesizer work, not yet started | **Yes** | Part F, `F-V3` (M7 row); graded number per `rebaseline_runbook.md` |

---

## GAP-6

**A5's schema fingerprint hashes `spatial_ref_sys`.** A5 defines the
fingerprint over "each table in the application schema" but never says what
that means when a Postgres extension (PostGIS) installs its own table into
the same `public` namespace. Verified empirically under both
`statesync_migrator` and `statesync_reader`: `public` holds 14 base tables —
12 real A5 tables + `schema_versions` (already excluded) + `spatial_ref_sys`
(not excluded). `mcp/fingerprint.py` is hashing `spatial_ref_sys`'s columns
into the fingerprint every time.

**Fixed on one side, not the other, deliberately.** `schema.list_tables`
(A4.1, `mcp/tools/schema_tools.py`) now excludes extension-owned tables by
`pg_depend` ownership — it returns exactly the 12 application tables, so the
SchemaRetriever no longer wastes a `describe_table` call on it. The
*fingerprint* formula (`mcp/fingerprint.py`) is untouched: changing it changes
the fingerprint value, which under D4's validate-on-read invalidates every
stored trajectory and requires re-stamping `schema_versions`. That is a
decision about D4's blast radius, not a cleanup — flagged per A0.1 rather than
made unilaterally.

**Why "no Part owns this":** it isn't gated on the graded re-baseline (it's a
schema/config question, not a model-quality one), and it isn't naturally a
Part F step either — F-V4 checks that seams exist, not that A5's fingerprint
formula is scoped correctly. It sits as a standing team decision until
someone decides whether "the application schema" should exclude
extension-owned tables at the fingerprint level too.

**Practical impact today: nil.** `spatial_ref_sys`'s shape is fixed by the
installed PostGIS version, so the fingerprint is stable in practice; it would
only move on a PostGIS upgrade, where invalidating trajectories is arguably
the conservative behavior anyway.

Full write-up: [`docs/spec-gaps.md`](spec-gaps.md) (GAP-6) ·
originating session: [`docs/phase_e_gap_log.md`](phase_e_gap_log.md) (Task 5).

## BD7

**A7's own schema has a hole its prose promises doesn't exist.** A7 says its
allowlist "excludes every one" of Vega-Lite's expression-string properties.
`additionalProperties: false` delivers that everywhere except `transform`,
which A7 itself declares `additionalProperties: true` on. A `calculate`
transform therefore satisfies the literal schema —
`fixtures/malicious_specs.json` ms_05 is exactly that spec, and before this
was found the orchestrator validator rejected 9/10 malicious specs, not the
10/10 E2-V3 requires.

**Closed as a decision, not a code defect:** a second, separately-named
deterministic audit (`_audit_expression_strings` in
`orchestrator/schemas/synthesizer_output.py`, mirrored in
`frontend/index.html`) walks `chart.transform` and rejects expression-bearing
keys, rather than editing A7's declared shape in either direction — A7
forbids relaxing `additionalProperties` to make a chart work, and tightening
it the other way (inventing a transform-op allowlist the spec never
enumerates) is the same class of unilateral edit to a locked contract.

**Not re-baseline-gated** — this is a schema/security-contract question,
independent of which model generates charts.

**Residual risk, not an open question:** the audit's key list
(`_EXPRESSION_KEYS` / `_DUAL_FORM_EXPRESSION_KEYS`) exists in two files with
no compile-time link between them (same caveat as BD4 and BD5). E2-V3 run
against both validators is what would catch drift; nothing currently runs
that automatically outside manual verification.

Full write-up: [`docs/phase_b_amendment.md`](phase_b_amendment.md) (BD7) ·
also migrated to Appendix 5 of
[`docs/StateSync_Build_Spec.md`](StateSync_Build_Spec.md) (see Task B below).

## F-V1 Class 4 / E2-V4 CSP enforcement

**What E2-V4 asked for, and what was actually shown.** E2-V4 wants "zero CSP
violations in the console" for 30 valid specs, and a malicious spec to raise
a CSP violation rather than execute. Both were attempted directly and both
technically passed — but the passing result was caught as potentially
meaningless before it was reported as a pass: this automation browser **does
not enforce CSP** at all (verified directly — `new Function(...)` and
`eval(...)` both succeed in the page despite the served header correctly
omitting `unsafe-eval`). So "0 CSP violations" here proves nothing on its
own; it's exactly the "validated-but-eval-capable" false pass E2-V4's own
wording warns about.

**What was actually proven instead, and holds regardless of browser
enforcement:** dynamic code construction (`Function`, `eval`) was trapped to
throw, simulating a strictly-enforcing CSP from outside the browser's own
mechanism. Under that trap, the shipped interpreter render path (`ast: true`
+ `expr: vega.expressionInterpreter`) rendered **30/30 specs with zero
`Function`/`eval` calls**; the default compiler path attempted dynamic code
construction on **5/5** specs tried and died every time. That's a stronger,
browser-independent claim than a violation count — but it is still not the
same thing as confirming a real, enforcing browser blocks the compiler path
and shows a CSP violation in its console.

**Correction to the original log's pointer.** `docs/phase_e2_log.md` names
this as owed at "F-V3" — that's imprecise. Checked against the Build Spec:
**F-V3 is the M1–M10 metric re-verification table** (E2-V1/M7 lives there,
see below); the CSP re-check belongs to **F-V1 Class 4** ("Execution path...
Re-run E2-V3 and E2-V4 against the frozen build"). Not correcting the
original log text (it's a closed record of what was said at the time) — noting
the precise owner here instead.

**Not re-baseline-gated** — a browser/environment question, not a model one.

Full write-up: [`docs/phase_e2_log.md`](phase_e2_log.md) (E2-V4 section) ·
spec step: `docs/StateSync_Build_Spec.md` §F-V1 Class 4.

## c2g_10 routing

**A plainly relational question routes to the document engine.** "List
sellers whose contracted SLA hours has not been set" routed to `docs`, not
`sql` — no SQL ever ran. Traced to E1's Supervisor prompt rewrite, which
removed Phase B's "default to sql" bias (that bias was itself a real problem
E1 needed to fix — see the E1 log's Supervisor routing bug). The word "SLA"
now appears to pull an otherwise ordinary relational query toward the
document route as an over-correction.

**Deliberately not patched blind.** This surfaced while verifying interrogation
targeting (an unrelated change, downstream of routing) — fixing a single
observed case by hand risks a prompt tweak that looks right on this one query
and is unverifiable against anything else. M9 (routing determinism ≥ 90% over
`fixtures/golden/phase_f_routing.json`, 60 queries / 20 per route) is the
actual instrument for this class of problem and belongs to Part F.

**Not re-baseline-gated** — routing-prompt quality is model-sensitive in
principle, but this item tracks a specific observed miscategorization to fix
or re-measure, not a number to defer.

Full write-up: [`docs/phase_e_gap_log.md`](phase_e_gap_log.md) ("Observed in
passing, out of this packet's scope") · spec step:
`docs/StateSync_Build_Spec.md` §F-V2.

## Task 10 / E2-V1 — M7 quality

**The model-independent half of E2 is done; the model-dependent half hasn't
started.** `docs/phase_e2_log.md` (Tasks 6–9) built and verified A7 dual
validation, CSP-safe rendering, the recall-weighted `SpecScorer`
(`orchestrator/scorers.py`), and the malicious-spec rejection suite — all
27/27 checks pass. None of that requires the Synthesizer to actually produce
a chart, and it currently doesn't: `chart` is `null` on every route by
construction (E1-era behavior, unchanged by the E2 gap-close work).

**What's actually left:** teach the Synthesizer to populate `chart` per A7
where a chart is warranted (E2 Deliverable 1, not yet done), then run
`scripts/score_specs.py --score` against `fixtures/golden/phase_e2.json`'s 30
reference specs to get a real M7 number.

**Re-baseline-gated — but distinct from BD6.** BD6 (`docs/phase_b_amendment.md`)
is scoped to exactly three checks: C1-V3, C2-V1, D-V6. M7 quality is *not* one
of them — it's gated instead by Strategy §7.0's own model-sensitivity
classification ("M7 (visualization *quality* score; M7's *validity/rejection*
half is model-independent and stands)") and appears as its own row in
`docs/rebaseline_runbook.md`'s re-run table. Any number produced on the local
model before the graded run must be labeled local-model-provisional, not
final — same rule, different mechanism than BD6.

**Owed at Part F**, `F-V3`'s metric table (`E2-V1 (M7)` is a named row there),
and reported as final only after `docs/rebaseline_runbook.md` runs.

Full write-up: [`docs/phase_e2_log.md`](phase_e2_log.md) ("Not covered,
deliberately (Task 10)") · packet source:
[`docs/AGENT_TASKS_phase_e_gap.md`](AGENT_TASKS_phase_e_gap.md) (Task 10) ·
graded procedure: [`docs/rebaseline_runbook.md`](rebaseline_runbook.md).
