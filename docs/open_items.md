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
| [c2g_10 routing](#c2g_10-routing) | "List sellers whose contracted SLA hours has not been set" routes to `docs` instead of `sql` — the word "SLA" pulls a plainly relational question toward the document route | **Fixed 2026-08-15** (`c735457`); M9 sql now 1.000 incl. 6 new overlap cases. Open only for the M3-recovery measurement | No | Part F, `F-V2` (M9 routing determinism) |
| [Task 10 / E2-V1 — M7 quality](#task-10--e2-v1--m7-quality) | Synthesizer still emits `chart: null` on every route; M7 chart-quality score cannot be measured until it's taught to populate `chart` | **Built and measured 2026-08-15** (`a00d0d1`). A7 30/30 (binary PASS); M7 0.4850 local provisional. Graded number still owed | **Yes** (graded number only) | Part F, `F-V3` (M7 row); graded number per `rebaseline_runbook.md` |
| [CSP `unsafe-inline`](#csp-unsafe-inline-in-script-src-raised-by-the-trace-renderer-xss-fix) | `script-src` still allows `'unsafe-inline'` because the app is one inline `<script>`; this is what would have made the (now-fixed) trace-renderer sink executable | Open, deliberately deferred — needs its own change + browser re-verify | No | Beyond F1 Scope; improvement, not a spec requirement |
| [M3 regression](#m3-regression) | D-V1 lift halved and D-V2 entity preservation dropped since Phase D. **Separation run done:** memory-flip candidate REFUTED; ~20 of the 35 lost English points traced to `c2g_10` misrouting; residual ~15 points still unattributed | Partly closed — one cause identified and shared with `c2g_10`, residual open | No for the identified cause (a routing defect, fixable now); the residual is re-measured by the graded re-baseline anyway | Routing half → fix with `c2g_10`; residual → needs a draft-first toggle that does not yet exist |

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

**Upgraded from "one observed query" to a measured, deterministic defect
(2026-08-15).** The M3 separation run found this is not an isolated case:
it costs **~20 of the 35 percentage points** M3 lost on its English
reference line, reproducibly, in both arms of a controlled experiment. Two
distinct query classes misroute, both asking for values that are **A5
columns on `sellers`**:

| Query class | Column it actually needs | Route taken |
|---|---|---|
| "What is Seller Store *N*'s contracted **SLA** in hours?" | `sellers.contracted_sla_hours` | `docs` ✗ |
| "What **category** is Seller Store *N* in?" | `sellers.category` | `docs` ✗ |

Verified live against the orchestrator, not inferred from row counts.

**Why M9 still scored 60/60.** `fixtures/golden/phase_f_routing.json` was
authored fresh in Phase F with unambiguous phrasing, so it contains no query
of this class. M9's 100% is real for its own fixture but does **not** mean
routing is defect-free — the D fixture, written earlier and independently,
exposes a class the F fixture never probes. Worth adding such cases to the
routing fixture when this is fixed, so M9 can catch a regression here.

**This now shares a root cause with the [M3 regression](#m3-regression)
item** — one fix closes the routing half of both.

### FIXED 2026-08-15 — routing half closed, M3 recovery not yet measured

Cause was narrower than "the word SLA pulls toward docs": the `sql` route
description ended with *"No mention of a policy, agreement, SLA commitment
document, or SOP."* That is a test on the question's **vocabulary**, so it
fired on wording rather than on where the answer lives. Replaced with an
explicit stored-column listing, a DISAMBIGUATION block ("decide by WHERE THE
VALUE LIVES, never by which word appears"), and worked examples for the
overlap cases. Commit `c735457`.

Also done, as this item itself asked for: the six missing query classes were
added to `fixtures/golden/phase_f_routing.json`, so **M9 can now catch a
regression here** — the blind spot that let M9 score 60/60 while production
routing was broken is closed. Six of the least discriminating existing entries
were dropped to hold Appendix 2.2's mandated 20/route.

**Measured so far:** M9 re-run scores **sql = 1.000 (20/20), including all six
new hard cases**; overall 0.983 (59/60), F-V2 PASS.

**Still open:** whether this recovers the ~20 percentage points it was costing
M3's English reference line. That is a D-V1/D-V2 measurement, not an
inference, and it has not been run yet. The prediction on record — English
9/20 (45%) → roughly 13/20 (65%) — is written down *before* the run precisely
so it can be wrong. This item closes when that number exists, not before.

Full write-up: [`docs/phase_d_log.md`](phase_d_log.md) (§ M3 separation run) ·
[`docs/phase_f_log.md`](phase_f_log.md) (§ Supervisor routing fix) ·
[`docs/phase_e_gap_log.md`](phase_e_gap_log.md) · spec step:
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

### Separation run performed (2026-08-15) — results

`bench_multilingual.py` run twice back to back, unmodified, changing only
`memory.enabled`. Full write-up: **`docs/phase_d_log.md` § "M3 separation run"**.

| | control | treatment | delta | English ref | entity pres. |
|---|---|---|---|---|---|
| Phase D (record) | 15.0% | 62.5% | +47.5pp | **80%** | 84.4% |
| memory=OFF (shipped) | 32.5% | 57.5% | +25.0pp | **45%** | 56.2% |
| memory=ON (pre-gap) | 12.5% | 42.5% | +30.0pp | **25%** | 43.8% |

**Candidate 1 REFUTED.** Memory ON does not recover the numbers — it makes
every absolute measure worse. **Decision recorded: `memory.enabled` stays
`false`** (runbook §4), re-decided again on the graded model.
(The `+30.0pp` delta is higher only because the *control* degraded faster;
delta is the right measure for D-V1's pass condition but the wrong one for
diagnosing a regression.)

**~20 of the 35 lost English points are `c2g_10`, confirmed deterministic.**
The same four English queries produced no `executed_sql` in *both* arms —
`d_ml_005/006_en` ("What category is Seller Store *N* in?") and
`d_ml_009/010_en` ("...contracted **SLA** in hours?"). Verified live:
both return `route="docs"`. Both are plainly relational —
`sellers.category` and `sellers.contracted_sla_hours` are A5 columns, and
neither value exists in any document. **This is the same defect as
`c2g_10`, so the two items share a root cause and a fix.**

**Residual ~15 points remain unattributed.** Those queries routed correctly
and produced wrong SQL. Candidate 2 (draft-first interrogation, Task 4) is
now the only live hypothesis, and separating it needs a third condition —
miss path *without* draft-first — which requires a code toggle that does
not exist. **Not guessed at.**

**Not a BD6-style model-ceiling item** — the identified cause is a prompt
regression in this repo, fixable without waiting for the graded model.

Full write-up: [`docs/phase_d_log.md`](phase_d_log.md) (§ M3 separation run) ·
[`docs/metrics-report.md`](metrics-report.md) · [`docs/phase_f_log.md`](phase_f_log.md).

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

## CSP `'unsafe-inline'` in script-src (raised by the trace-renderer XSS fix)

**Status: open, deliberately not fixed in place.** Raised while fixing the
`renderTrace()` XSS sink (`docs/phase_f_log.md`).

The frontend CSP is strong where E2-V4 requires it — no `'unsafe-eval'`, so
`Function()` construction is blocked and Vega renders through the AST
interpreter. But `script-src` still carries `'unsafe-inline'`, because the
whole application is a single inline `<script>` in `frontend/index.html`.

**Why it matters.** `'unsafe-inline'` is what would have turned the trace
renderer's `innerHTML` sink into script execution. The sink is fixed, so this
is no longer a live path — but it is the difference between "one bug away from
execution" and "not exploitable even with a bug". Defence in depth, in a
component that renders untrusted database content by design.

**The fix, and why it is not being done blind:** extract the inline script to
`frontend/app.js`, serve it same-origin, drop `'unsafe-inline'` from
`script-src`. That is a small change with a real regression surface — E2-V4
and E2-V6 verify CSP contents and the in-browser A7 validator
(`window.validateA7Chart`), and both are currently passing. It should be done
as its own change with the browser half of E2-V3/V4/V6 re-run immediately
after, not folded into unrelated work.

**Not claimed as a Part F requirement.** F1 Scope asks for CSP without
`unsafe-eval`, which is satisfied and verified. This is an improvement beyond
the spec's bar, recorded so it is a decision rather than an oversight.
