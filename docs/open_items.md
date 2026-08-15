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
| [F-V1 Class 1 & Class 4](#f-v1-class-1--class-4) | Both classes pass structurally on the local model/this browser now, but each rests on something worth re-confirming under different conditions later: Class 4's CSP claim on a browser that actually enforces CSP; Class 1's prompt-injection resistance on the graded model | Passing now; each has a distinct residual condition to re-confirm | No (see entry — deliberately not folded into BD6) | Part F, `F-V1` (run as part of Phase F; re-confirm Class 4 in an enforcing browser, Class 1 informally under the graded model) |
| [c2g_10 routing](#c2g_10-routing) | "List sellers whose contracted SLA hours has not been set" routes to `docs` instead of `sql` — the word "SLA" pulls a plainly relational question toward the document route | Open, unpatched | No | Part F, `F-V2` (M9 routing determinism) |
| [Task 10 / E2-V1 — M7 quality](#task-10--e2-v1--m7-quality) | Synthesizer still emits `chart: null` on every route; M7 chart-quality score cannot be measured until it's taught to populate `chart` | Blocked on Synthesizer work, not yet started | **Yes** | Part F, `F-V3` (M7 row); graded number per `rebaseline_runbook.md` |
| [M3 regression](#m3-regression) | D-V1 lift halved (+47.5pp → +25.0pp) and D-V2 entity preservation dropped (84.4% → 56.2%) since Phase D; the English reference line fell 80% → 45%, proving the cause is upstream of normalization. Two deliberate Phase-E-gap changes are candidates, not yet separated | Open — regression confirmed, cause not isolated | Partly (the graded re-baseline re-measures M3 anyway, but the cause here is a config/build change, not a model ceiling) | One controlled run separates the two causes; then Part F / re-baseline |

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

## F-V1 Class 1 & Class 4

Grouped together because both classes of `scripts/verify_security.sh`
**pass in full now**, and both rest on a condition worth tracking separately
from the pass/fail result itself — one environmental, one model-related.
Neither is re-baseline-gated in the BD6 sense (nothing here blocks a Part or
voids a decision); both are "keep an eye on this under different conditions"
items.

### Class 1 — tool poisoning through data

Nine payloads (`adv_01`–`adv_08`, `adv_16`) seeded into real
`delivery_events.note` / `disputes.narrative` rows via
`scripts/seed_adversarial.py`, each retrieved through a natural-language
query designed to surface that exact row (`fixtures/adversarial_targets.json`).
Results recorded in `docs/phase_f_log.md`'s F-V1 section.

**Why it's tracked here anyway, despite passing:** M8's pass condition is
binary and structural ("no unintended tool invocation," "injection not
obeyed"), but the mechanism that DELIVERS a pass — the local model
correctly declining to treat text arriving through a data column as an
instruction — is itself a property that could plausibly differ under a
different (graded) model. A stronger or more instruction-tuned model isn't
guaranteed to be more resistant; it could in principle be more literal about
following an embedded "[SYSTEM]" prefix. Worth a re-confirmation pass under
the graded model for completeness, the same spirit as Class 4's browser
re-check below, even though — unlike Class 4 — there's no reason to expect
today's PASS is an artifact of a broken test environment.

### Class 4 — execution path / CSP enforcement

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
[`docs/phase_f_log.md`](phase_f_log.md) (F-V1 section, both classes) ·
spec step: `docs/StateSync_Build_Spec.md` §F-V1.

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

## M3 regression

**Both halves of M3 regressed between Phase D and Phase F's F-V3 re-run**,
on the same model (`qwen2.5:7b`), same fixture, same procedure:

| | Phase D | Phase F (isolated clean re-run) |
|---|---|---|
| D-V1 lift | +47.5pp (15.0% → 62.5%) | **+25.0pp** (32.5% → 57.5%) |
| D-V2 entity preservation | 27/32 (84.4%) | **18/32 (56.2%)** |
| English reference line | 16/20 (80.0%) | **9/20 (45.0%)** |

M3 still *passes* its written condition — the normalization path is still
measurably better than the control, which is what D-V1 requires — but the
margin halved and entity preservation fell sharply. Reported as a regression
per F-V3's explicit instruction rather than quietly replacing the old number.

**The English reference line localizes the cause.** D-V3 confirms in both
runs that English queries pass through the gate byte-identical (20/20
`normalized_query_unchanged=True`), so an English-only 80%→45% drop cannot
originate in the multilingual path. Two deliberate changes from the
Phase-E-gap packet are the candidates:

1. **`memory.enabled` default flipped `true`→`false`** (Task 3, NOTE-1).
   Phase D measured M3 with memory ON. Every query in this run took the
   full miss path instead of hitting a cached trajectory.
2. **Draft-first interrogation targeting** (Task 4) changed miss-path SQL
   generation — and with memory off, every query is a miss-path query, so
   this change now applies universally rather than only on cache misses.

**Not yet separated, and deliberately not guessed at.** One controlled run
(`bench_multilingual.py` with `memory.enabled=true`, everything else held
constant) distinguishes them: if the number returns toward +47.5pp, cause 1
dominates and the regression is a known, accepted trade of the memory
default flip; if it stays near +25pp, cause 2 is implicated and Task 4's
interrogation change needs review against multilingual queries specifically.

**Not a BD6-style model-ceiling item** — both candidate causes are config or
code changes made in this repo, not properties of the local model. The
graded re-baseline will re-measure M3 regardless, but it will not by itself
answer which change caused this.

Full write-up: [`docs/metrics-report.md`](metrics-report.md) (M3 regression
section) · [`docs/phase_f_log.md`](phase_f_log.md) (F-V3 section).

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
