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
| [Schema column semantics](#schema-column-semantics-reverted) | The model cannot distinguish `locations.city` (English) from `city_local` (Indic script) and nothing tells it; ~4 of 8 English-line misses. A comment-based fix was built, measured, and REVERTED for damaging entity preservation | Open — mechanism known, fix unvalidated | No | Needs a script-neutral wording measured on BOTH the English line and D-V2 |
| [CSP `unsafe-inline`](#csp-unsafe-inline-in-script-src-raised-by-the-trace-renderer-xss-fix) | `script-src` still allows `'unsafe-inline'` because the app is one inline `<script>`; this is what would have made the (now-fixed) trace-renderer sink executable | **CLOSED 2026-08-16** (`9d73b7c`) — script extracted to `frontend/app.js`, `'unsafe-inline'` dropped, `verify_e2.py` 30/30. This row read "Open" for four commits after the fix shipped | No | Beyond F1 Scope; improvement, not a spec requirement |
| [c2g_04 row-count drift](#c2g_04-row-count-drift) | `phase_c2_v2.json`'s `c2g_04` keeps `expected_row_count = 5` while its own corrected SQL returns 8 and the correction's prose says 8 — the corrected fixture contradicts itself in one field | Open — no consumer reads it today, so zero blast radius; a future harness that trusts the field inherits a wrong number silently | No | Audit 004 F-004-7 |
| [Fixture corpus declaration unasserted](#fixture-corpus-declaration-unasserted) | `fixture_corpus()` treats absence of `_about.corpus` as a positive claim of `public`. True for all 11 fixtures today (verified); asserted by nothing, so a new olist-targeting fixture that omits the field passes the guard clean | Open — latent; this is `80d35c8`'s failure mode returning through the one door cycle 002's A3 left open | No | Audit 004 F-004-8 |
| [`m4_diagnosis_v2_corrected.json` is misnamed](#m4_diagnosis_v2_correctedjson-is-misnamed) | Despite the filename, it is a run of the ORIGINAL `phase_c2.json`, not `phase_c2_v2.json` — verified by content (its `c2g_04` ground truth is the location-join SQL). Its 44.0% is correct for what it actually measured | Open — flagged in `metrics-report.md`'s own M4 row; artifact deliberately not renamed or touched | No | Cycle 004 DEVIATION 2 |
| [Citations carry no support relation](#citations-carry-no-support-relation) | `build_citations` lists `retrievals[:citation_max_chunks]` with no relevance filter. Cycle 004 C1 fixed the *label* ("Sources" → "Retrieved Context"); the mechanism is unchanged | Label fixed 2026-08-20; **mechanism open by design** — filtering would alter the exact set M6's context precision measures | No | Audit 004 F-004-5 |
| [Three unfailable fixture queries](#three-unfailable-fixture-queries) | `c2g_03`/`c2g_10`/`c2g_18` return zero rows; standard EX scores any empty result correct against an empty reference. 23/23 passes across every run ever made. Published 60.0%; 54.5% over the 22 that can fail | Disclosed at every publication point 2026-08-20 (cycle 004 B2). **Fix is an owner judgment call**, per the fixture's own `_audit` — leave them, or replace the questions | No | Audit 004 F-004-3 |
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

> **CLOSED 2026-08-16 in `9d73b7c` — this entry was left stale and is corrected
> here by the fresh-context audit.** The fix described below as "not being done
> blind" was in fact done, exactly as described: the inline script was extracted
> to `frontend/app.js`, served same-origin, and `'unsafe-inline'` was dropped
> from `script-src`. `open_items.md` was edited twice *after* that commit
> (`a4293e1`) without updating this entry, so the repo's "what's still owed"
> file claimed an open item that had shipped.
>
> Verified now, not assumed: `frontend/nginx.conf`'s `script-src` is
> `'self' https://cdn.jsdelivr.net https://fonts.googleapis.com` — no
> `'unsafe-inline'`, no `'unsafe-eval'`; `frontend/index.html` carries no inline
> `<script>` body; `verify_e2.py` passes 30/30 including the three checks
> `9d73b7c` added for exactly this.
>
> `style-src` keeps `'unsafe-inline'` deliberately — the `<style>` block is
> still inline and inline style is not an execution primitive.
>
> Residual, recorded rather than reopened: `script-src` allows
> `https://cdn.jsdelivr.net`, because Vega/Vega-Lite/vega-embed load from that
> CDN. That is a supply-chain dependency on a third party, not a CSP weakness,
> and it is a different decision from the one this item tracked.

**Status when raised: open, deliberately not fixed in place.** Raised while
fixing the `renderTrace()` XSS sink (`docs/phase_f_log.md`). The original text
is kept below as the record of the reasoning.

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

## Schema column semantics (reverted)

**Status: open. Mechanism identified, one fix attempted and measured as
harmful, reverted.**

`locations.city` holds English/romanised names and `locations.city_local`
holds Indic script. Nothing tells the model which to use, so English queries
filter `city_local` and match nothing. Same shape on
`sellers.display_name`/`display_name_local` and
`catalog_items.title`/`title_local`. This accounts for roughly 4 of the 8
English reference-line misses (`docs/phase_d_log.md`).

The delivery channel exists and works: `describe_table` returns a `comment`
per column and the SQL Agent receives it. Migration 005 populated it;
migration 006 reverted it.

**Why the first attempt failed** (full numbers in `docs/phase_d_log.md`):
telling the model "an English name will never match `city_local`" made it
prefer the English column *even for Devanagari queries*, transliterating
`दिल्ली` to `'Delhi'`. D-V2 entity preservation fell 81.25% → 62.5% and D-V1
treatment fell 65.0% → 55.0%.

**Hypothesis for the next attempt, stated in advance so it can be wrong:**
wording that is symmetric and script-triggered rather than
English-preferring — e.g. *"match the script the question uses: romanised
names against `city`, Indic-script names against `city_local`"* — should fix
the English half without biasing the multilingual half.

**Acceptance condition, fixed now to prevent moving the goalposts later:** it
must improve the English reference line **and** hold D-V2 entity preservation
at or above 81.25% **and** hold D-V1 treatment at or above 65.0%. Anything
less is the same trade in a new costume.

**Not re-baseline-gated** — this is a schema-content question, not a
model-quality one, though a stronger model may well need less hand-holding.

---

### ✎ AMENDED 2026-08-20, audit 001 (F-001-3). The D-V1 clause is re-anchored; the D-V2 clause is NOT touched.

**The problem.** The bar above was written against the pre-`da6e49f` build.
`da6e49f` (projection discipline) then **shipped**, and took D-V1 treatment
65.0% → 55.0% (`docs/metrics-report.md`, "M3 re-measured on the current
build"). The word in the clause is *hold*. On the build that ships it is a
**+10.0pp improvement demand**, and nothing anywhere said so.

**The decisive fact — which clause still discriminates:**

```
$ python3 -c "
import json
runs={'pre-da6e49f (bar was written here)':'docs/bench_multilingual_report.json',
      'migration 005 (REJECTED)':'docs/bench_multilingual_report_migration005.json',
      'SHIPPED build (post-projfix)':'docs/bench_multilingual_report_postprojfix.json'}
for label,f in runs.items():
    d=json.load(open(f))
    t=d['dv1_treatment']['accuracy']; v=d['dv2_entity_preservation']['rate']
    print(f'{label:34} D-V1 {t:.1%} -> {\"PASS\" if t>=0.65 else \"FAIL\"}   D-V2 {v:.1%} -> {\"PASS\" if v>=0.8125 else \"FAIL\"}')"

pre-da6e49f (bar was written here)  D-V1 65.0% -> PASS   D-V2 81.2% -> PASS
migration 005 (REJECTED)            D-V1 55.0% -> FAIL   D-V2 62.5% -> FAIL
SHIPPED build (post-projfix)        D-V1 55.0% -> FAIL   D-V2 81.2% -> PASS
```

**`D-V2 ≥ 81.25%` still separates the two perfectly.** It is the clause that
actually rejected migration 005, and it passes the shipped build. **It is left
exactly as written**, and so is the English-line clause.

**`D-V1 ≥ 65.0%` no longer separates anything.** It fails the rejected
migration and the shipped product *equally*. A criterion that rejects the
status quo has no discriminating power left; it cannot tell the change we want
from the change we threw out.

**Why re-anchoring it is not the goalpost-move this repo prohibits.** That act
is *changing a criterion because you dislike a result you have already seen*.
No B1 attempt has been made and there is no result. What moved was the
**build**, underneath a criterion whose anchor was an absolute number from a
build that no longer exists. And the safety test is explicit: **does this let
migration 005 back in? No.** 005 fails on D-V2 (62.5% < 81.25%) independently,
so the owner decision in `CYCLE_PROTOCOL.md` §4.3 is untouched. That test is
the reason this amendment is safe, and it is why the D-V2 clause was not
re-anchored even though it could have been by the same argument.

**The amended condition.** The original intent — *"anything less is the same
trade in a new costume"* — was a **no-regression guard**, written as an
absolute only because 65.0% happened to be the current value. It is restored as
a guard, against an anchor that cannot rot:

1. **D-V2 entity preservation ≥ 81.25%.** Unchanged, absolute, as pre-committed.
2. **The English reference line must improve** over the paired pre-measurement.
3. **D-V1 treatment must not fall below the paired pre-measurement** taken on
   the same build immediately before the attempt — replacing `≥ 65.0%`.
4. **The Hinglish half must be no worse** than that same pre-measurement.

Clauses 2–4 are **paired**: the "before" is measured on the build the fix will
be applied to, *before the fix is written*. That is `V2_STRATEGY` §6 rule 2
applied to an acceptance condition — it cannot rot when the build moves, and it
cannot be gamed, because the baseline is fixed before the change exists. An
M3 re-run is therefore a **precondition** of any B1 attempt, not an optional
extra: the last measurement is 2026-08-17.

**What must NOT be silently absorbed by this.** Read as an improvement target,
the old 65.0% would have forced whoever attempted B1 to also recover
`da6e49f`'s −10.0pp D-V1 regression. Re-anchoring releases that obligation, so
the regression is hereby carried as an open item **in its own right** rather
than dissolved into a lowered bar — see below. It is not fixed and it is not
forgiven.

**Scope, per F-001-7.** The `x`/`x_local` defect also fires on **English**
questions — three independent local models project `city_local` on
`phase_c2_v2`, the fixture whose 60.0% is published. B1's condition is measured
only on `phase_d_multilingual` and cannot see it. The English manifestation is
therefore **measured and reported** on any B1 attempt (it costs nothing; the
`phase_c2_v2` run already happens) but is **not** made a gating clause — there
is no evidence on which to set a bar, and inventing one is how the first bar
rotted.

### `da6e49f`'s D-V1 regression — carried separately, not absorbed

**Status: open, unfixed, deliberately not folded into the B1 bar above.**

Projection discipline shipped on the strength of +8pp strict / +4pp standard EX
on `phase_c2`, and cost **−10.0pp D-V1 treatment** and −5.0pp on the English
reference line, measured afterwards on a fixture it was never validated
against. `metrics-report.md` records it and says "do not revert on these numbers
alone" — both effects are small-n. That judgement stands; what is recorded here
is that the regression is **owed**, independently of whether B1 ever happens.

### `attribute_failures.py` has no `WITH`-clause awareness — added 2026-08-20, cycle 001

**Status: open, unfixed, low urgency — worked around once, will recur.**

`_TABLE_RE = re.compile(r"\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.I)`
has no concept of a `WITH ... AS (...)` CTE, so a reference SQL that names a
CTE (e.g. `ranked`, `order_totals`) is parsed as if that name were a real
missing table. Found on `fixtures/golden/olist_hard.json`'s three
window-function questions (`oh_wf01`/`oh_wf03`/`oh_wf04`, all of which use a
CTE): the tool flagged all three `wrong_tables`, but reading the model's
actual generated SQL showed it selected the correct real tables in every
case — the true classification is `same_tables` (a genuine SQL-syntax
capability gap), 0/3 not 3/3. Caught and corrected in the write-up
(`docs/metrics-report.md` § "Hard-fixture results") by manual inspection this
time; nothing in the tool itself prevents the same misclassification on the
next CTE-using ground truth.

**Fix, not attempted here:** strip or ignore identifiers introduced by a
`WITH name AS (` clause before extracting FROM/JOIN identifiers — a few lines,
same file. Left unbuilt because it is a diagnostic-tool quality improvement,
not a defect that invalidates any published number now that this instance is
caught, and cycle 001's scope was the fixture and the measurement, not the
tooling underneath `attribute_failures.py`. Worth an hour in a future cycle
that touches this tool for any other reason, or immediately before the next
fixture that ships a CTE in its ground truth.


---

## M6's gate cannot fail — added 2026-08-20, cycle 003 (audit 003 F-003-6, residue)

Cycle 003's A5/A6 closed most of F-003-6: M6 now has a test file
(`tests/test_grounding_scorer.py`, 15 cases — it had **none**, the only
metric in the repo without one) and an entry in `docs/limitations.md`'s
instrument-limitations section (it was the only metric absent from it).
**One part was not closed and is recorded here rather than left implied.**

`scripts/verify_fv3.sh:100` runs `run_step "E1-V3 (M6)" python3
scripts/bench_grounding.py`, and `run_step` records PASS or FAIL purely on
the **exit code**. `bench_grounding.py` cannot exit non-zero for any
*measured* outcome — the only non-zero exits are the flag guard and the
`--out` collision check, both of which fire before any query runs. So
`E1-V3 (M6) PASS` in an F-V3 summary means "the script ran to completion",
not that anything about M6 held.

**This is defensible as far as it goes, which is why it is here and not a
defect.** E1-V3's spec condition sets **no threshold** on purpose:
*"Record both scores honestly; the Strategy Document sets no threshold."*
A gate that cannot fail on a metric with no bar is not obviously wrong.

**What is genuinely unguarded** is the other half of E1-V3's pass condition,
which is a requirement on source code rather than on a number: *"an
ungrounded claim is a failure regardless of whether it is factually true —
**the scoring script must enforce this** rather than crediting lucky
accuracy."* Cycle 003 added `_recompute_faithfulness_from_claims()` as a
cross-check and a test that pins it (an implementation crediting
`factually_correct` instead of `grounded_in_retrieved_context` fails that
test), so the *claim* is now checked in CI. What still is not: a run whose
judge parse-failed on every query would score `faithfulness 0.0`, write a
report, exit 0, and be recorded by F-V3 as **PASS**. A parse failure is
scored as a quality failure, indistinguishable in the mean from a genuinely
unfaithful answer. The published run and both cycle-003 runs have **zero**
parse failures, so no number on record is affected.

**Fix, not attempted here:** have `bench_grounding.py` exit non-zero when
the instrument itself failed — a parse-failure rate above some small
threshold, or any query erroring out — as distinct from the measured
scores, on which it should keep having no opinion. That distinction (the
instrument broke vs. the system scored badly) is the one `run_step`'s
exit-code contract currently cannot express. Left unbuilt because cycle
003's frozen criteria scoped A5 to the scorer's semantics, not the
harness's exit contract, and because no committed number is affected today.

---

## c2g_04 row-count drift

*Added 2026-08-21, cycle 004 (audit 004 F-004-7).*

`fixtures/golden/phase_c2_v2.json` is the model this project holds up for
correcting a fixture honestly — `V2_STRATEGY.md:391` calls it "the
`phase_c2_v2` protocol". Its `c2g_04` correction replaced a location-join
ground truth (5 rows) with a city-match one, and the correction's own `why`
records the new count: *"matching by city is the plain reading **(8 rows)**"*.
The `changed` list is `["ground_truth_sql"]`. **`expected_row_count` was left
at `5`.** Verified by executing all 25 references live: 24 of 25 match their
frozen count, `c2g_04` returns 8 against a stored 5.

**Blast radius today is zero**, which is why this is open rather than fixed.
Every consumer was traced: `diagnose_m4.py` and `check_c2v8.py` both execute
the reference SQL live and score against those rows (`check_c2v8.py:109`
touches the field only to fill a `None`, and points at `phase_c2.json`
anyway); `verify_b.sh:81` is the one place it is load-bearing and it reads
`phase_b.json`.

**Why it is still worth closing.** A future harness that trusts the field —
the obvious one being a cheap pre-flight that skips executing references —
inherits a wrong number silently, and the field's own generator
(`gen_phase_d_fixtures.py:10`, *"never guessed"*) exists precisely because
that matters.

---

## Fixture corpus declaration unasserted

*Added 2026-08-21, cycle 004 (audit 004 F-004-8).*

Cycle 002's A3 (`cefe045`) closed audit 002's F-002-3 by tying
`schema_retriever.target_schema` to the fixture actually being run.
`fixture_corpus()` resolves a fixture to a corpus by reading `_about.corpus`,
and treats **absence of the field as a positive claim of `public`** —
documented deliberately at `shipped_flags.py:170-173`. That is sound *given*
that every olist-targeting fixture declares the field.

**Nothing asserts that.** Checked by hand across all eleven fixtures
(cross-referencing whether each one's SQL actually references `olist.`
against what the guard resolves): all eleven are consistent today, so nothing
in the repo is currently mis-guarded. `tests/test_fixture_corpus_guard.py`'s
six cases all run against named or synthetic fixtures; none sweeps
`fixtures/golden/` and asserts the invariant.

A new olist-targeting fixture that omits `_about.corpus` — the natural thing
to forget, since ten of eleven fixtures legitimately omit it — resolves to
`public`, matches the shipped default, and passes the guard clean. That is
`80d35c8`'s failure mode returning through the one door A3 left open.

**Note on the right assertion.** A grep-for-`olist.` sweep is the obvious
implementation and probably the wrong one: a future fixture could target a
third schema entirely. The general invariant — *a fixture declares the corpus
its ground truth was written against* — is what matters, not the substring.

---

## `m4_diagnosis_v2_corrected.json` is misnamed

*Added 2026-08-21, cycle 004 (DEVIATION 2 — found while sourcing B2's fix,
flagged rather than acted on).*

`docs/m4_diagnosis_v2_corrected.json` reads as "v2, corrected" and is cited
by `metrics-report.md`'s M4 row for **44.0%**. It is a run of the
**original** `fixtures/golden/phase_c2.json`, not `phase_c2_v2.json`.
Verified by content, not by filename: its `c2g_04` ground truth is the
original location-join SQL (5 rows), where `phase_c2_v2`'s is the corrected
city-match INTERSECT (8 rows). The `v2` in the name refers to the **report
schema** version (`schema_note`: *"v2: rows are persisted (v1 stored only
counts)"*), not the fixture.

**The 44.0% is not wrong** — it is the original fixture's number, which is
exactly what `AGENT_HANDOFF.md` separately and correctly labels "standard EX
— synthetic, original fixture". The corrected fixture's number is 60.0%,
from `m4_diagnosis_v2fixture_7b.json`, which does carry a `fixture` field
naming its source.

**Not fixed in cycle 004**: renaming or re-sourcing a committed artifact was
outside that cycle's frozen scope, and the file is not a fixture, rubric or
scorer it was licensed to touch. Flagged in `metrics-report.md`'s own M4 row
so a reader hits the warning where they hit the number.

---

## Citations carry no support relation

*Added 2026-08-21, cycle 004 (audit 004 F-004-5). Label half fixed; mechanism
half open **by design**.*

`build_citations` (`orchestrator/nodes/synthesizer.py:350`) takes
`retrievals[:citation_max_chunks]` in retrieval order and applies no
relevance filter of any kind — the answer is never consulted. Until cycle
004 the frontend presented that list under the heading **"Sources"**.

Live instance, from the published M6 run: `e1_doc_004` asks *"What committed
transit time does FastShip 1's SLA annexure specify for the Metro Mumbai
zone?"* FastShip 1 is **LP001**; the fixture expects
`doc_sla_annexure_LP001_c000/c001`; the actual cited list is
`doc_sla_annexure_LP003_c000/c001` and `doc_sla_annexure_LP007_c000` — two
**other logistics providers'** contracts. Not a topic drift: the wrong
company's SLA annexure, same document family, same table shape, different
number in it.

**Fixed (cycle 004 C1):** the heading now reads "Retrieved Context", which
stops claiming a support relation the mechanism does not provide. Verified
live in a browser.

**Deliberately NOT fixed, and this is the part that is open:** filtering
what gets listed would alter the exact retrieval set `mean_context_precision`
measures — a metric change wearing a UI fix's clothes. Closing this properly
means deciding what a citation *should* mean here (top-k retrieval, or
answer-supporting), and measuring M6 before and after, because the change
moves the number by construction.

---

## Three unfailable fixture queries

*Added 2026-08-21, cycle 004 (audit 004 F-004-3). Disclosed, not fixed — the
fix is an owner judgment call and was routed there by the fixture itself.*

`c2g_03`, `c2g_10` and `c2g_18` have ground truths returning **zero rows**,
each for a real reason in the data (0 of 30 sellers have a null
`contracted_sla_hours`; `max(order_items.quantity)` is exactly 5 against a
`> 5` predicate; all 19 `pending` fulfillments have delivery events). Under
`ExecutionAccuracyScorer`, `sorted([]) == sorted([])`, so **any** generated
SQL returning nothing scores 1.0 against them regardless of projection,
column count or semantics — confirmed by direct call.

They have never failed: **23 of 23** scoring opportunities across all eight
committed `m4_diagnosis*.json` reports and four model configurations. In the
published 60.0% run, two of the three pass while returning a visibly
different projection from the reference (4 cols vs 2, 4 cols vs 3).

Every synthetic standard-EX figure this project publishes therefore sits on a
floor of `3/25 = 12.0%` no model can fall below and none earns. Over the 22
references that can fail, the published 60.0% is **54.5%**.

**Disclosed at every publication point** by cycle 004 B2
(`AGENT_HANDOFF.md`, `V2_STRATEGY.md`, `metrics-report.md`). **The fixture
was not edited** — its own `_audit.known_remaining_weakness` names this and
states the fix "needs either different questions or seeded data, and both are
judgment calls for the owner rather than corrections with an evident right
answer." That routing is respected here.

**Note for whoever closes it:** excluding the three and publishing 54.5% is
*also* a distortion — a question whose correct answer is "none" is a legitimate
question. The honest options are (a) replace the three questions with ones
whose answers are non-empty, (b) seed data so the current questions return
rows, or (c) keep publishing 60.0% with this caveat permanently attached.
