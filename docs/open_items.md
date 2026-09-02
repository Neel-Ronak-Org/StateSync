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
| [`c2g_15`'s filter is a no-op](#c2g_15s-filter-is-a-no-op) | "Sellers with a Devanagari display name" matches all 30 sellers, 0 NULLs — a fourth reference that cannot discriminate, by a mechanism distinct from the three above. The shipped 7B's pass is awarded to `WHERE display_name_local IS NOT NULL`, not a script test. Published 60.0% / 54.5%; **52.4% over the 21 references that can both fail and discriminate** | Disclosed at every publication point 2026-08-21 (cycle 005 B1). **Fix is an owner judgment call**, same shape as the row above — seed a Latin-only `display_name_local`, or change the question | No | Audit 005 F-005-2 |
| [The real-data headline's difficulty caveat did not propagate](#the-real-data-headlines-difficulty-caveat-did-not-propagate) | `olist.json` removes schema linking and ambiguity resolution by construction (12/18 questions name a schema column verbatim, 16/18 single-table) — `metrics-report.md` states this correctly, but `AGENT_HANDOFF.md` and `V2_STRATEGY.md` compressed it to the single word "well-specified", where no reader would decode it. Same defect class as audit 002 F-002-6, never applied to the easy fixture | **Caveat propagated 2026-08-23** (post-cycle-005 follow-up, owner-raised). Underlying question — whether to keep publishing a schema-guided figure as the real-data headline at all — **open, and an owner call** | No | Raised by the owner reading the handoff, 2026-08-23 |
| [Five real-data questions state a restriction the data makes invisible](#five-real-data-questions-state-a-restriction-the-data-makes-invisible) | `ol_007`/`ol_008`/`ol_012`/`oh_cs01`/`oh_cs03` each state a population/denominator restriction that removes zero rows on this corpus. Three passes demonstrably credited to a model that dropped the stated restriction — same failure class as `c2g_15`, real data being *complete* (0 orphans anywhere) rather than the fixture's own defect | Disclosed at every publication point 2026-08-23 (cycle 006 C1). **Fix is an owner judgment call**, same shape as `c2g_15` — neither fixture edited | No | Audit 006 F-006-4 |
| [Two hard-fixture questions are softer than their own labels](#two-hard-fixture-questions-are-softer-than-their-own-labels) | `oh_j04` (labelled `join3`) is answerable with 2 tables — its middle table is a pure pass-through; `oh_j07` defines "repeat customer" and Gemini's SQL implements a different definition, disagreeing on 84/2,997 customers, hidden by the question's own `LIMIT 5`. Cycle 002's safeguard 6 checks the reference's table count, not the question's requirement | Disclosed at every publication point 2026-08-23 (cycle 006 C2). Fixture not edited — relabelling `oh_j04` or tightening safeguard 6 is an owner call | No | Audit 006 F-006-5 |
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

### ✎ B1 ATTEMPT 1 — 2026-09-02. Hypothesis refuted, nothing shipped, bar unmet.

**Status: B1 stays OPEN.** No fix was shipped and no acceptance clause was met.
The working tree is back to its committed state (`verify_deployed.py` 19/19,
`pytest` 125, `lint-no-literals` PASS, `verify_seams` 26/0, `verify_e2` 46/0,
shipped flags clean). What this attempt bought is **three eliminated
mechanisms and a much sharper diagnosis**, recorded so attempt 2 does not
re-run any of it.

**The paired pre-measurement was taken first, as the amendment requires**, and
then taken a SECOND time on the same build:

| | PRE run 1 | PRE run 2 | anchor for |
|---|---|---|---|
| D-V1 control | 15/40 | 15/40 | — |
| D-V1 treatment | 25/40 | 25/40 | clause 3 (POST ≥ 25/40) |
| English reference | 11/20 | 11/20 | clause 2 (POST ≥ 12/20) |
| D-V2 total | 27/32 | 27/32 | clause 1 (POST ≥ 26/32 = 81.25%) |
| D-V1 hinglish | 12/20 | 12/20 | clause 4 |
| D-V2 hinglish | 12/16 | 12/16 | clause 4 |

Artifacts: `docs/bench_multilingual_report_pre_b1.json`,
`docs/bench_multilingual_report_pre_b1_run2.json`.
**Clause 4 is read as BOTH halves** (D-V1 on the 20 hinglish queries AND D-V2
on the 16 hinglish proper-noun queries) — the aggregate D-V2 clause can be held
while the Hinglish half degrades and the Devanagari half improves, which is
migration 005's own trade in mirror image. D-V1-only is the convenient reading.

### ⚠ F-007-10 IS REFUTED. M3 is deterministic on a fixed build.

Audit 007 concluded M3 "does not reproduce" and inferred a **±7.5pp noise
floor**, on the strength of 2026-08-17 vs 2026-08-24 differing. Two full M3 runs
taken back to back on one build, an hour apart, **agree on every one of the 60
queries — zero flips**, across the orchestrator restart the harness performs
between its own control and treatment passes.

F-007-10's premise was *"no code change between the two measurements on any path
D-V1 exercises."* That is false:

```
$ git log --oneline --since=2026-08-17 --until=2026-08-25 -- orchestrator/nodes/ \
      orchestrator/language_normalizer.py orchestrator/schema_retriever.py \
      orchestrator/graph.py config/statesync.toml
cb61a53 Cycle 007: rewrite_node for multi-turn ...
cdecb72 Cycle 007: session history storage ...
80d35c8 The agent was querying the SYNTHETIC corpus while being scored against the real one
527441d The Gemini wall was per-MINUTE, not per-day ...
$ git log --oneline --since=2026-08-17 --until=2026-08-25 | wc -l
     112
```

112 commits, four on D-V1's own paths. **The 08-17 → 08-24 movement was build
movement, not sampling noise.** Consequence: M3 IS a usable instrument, and
clause 2's one-query threshold on n=20 is decidable rather than lost in noise.
The unsent `cfg.models.seed` remains a real latent defect (F-007-10's other
half stands) but is not the explanation — at `temperature = 0.0` decoding is
greedy and a seed would change nothing.

### The hypothesis that was tested, and how it died

`open_items.md`'s recorded hypothesis is *"symmetric, script-triggered wording."*
**That wording already ships**, in a more salient prompt position than a column
comment, and has since before migration 005 — `orchestrator/nodes/sql_agent.py`
`_format_entity_hint`, which names all six columns and says *"Do NOT use a
_local column for a Latin-script value."*

A second hypothesis was formed and tested: interrogation
(`_pick_interrogation_targets` / `_target_for`) selected `columns: nullable_cols`,
and on **every** pair the romanised half is NOT NULL while the `_local` half is
NULLABLE — so the agent was shown Devanagari samples and never shown that
`city` holds `'Mumbai'`. Verified live in the trace
(`columns_sampled: ["digipin","city_local","pincode"]`).

**It was implemented, deployed, and refuted three independent ways:**

1. **Direct experiment.** With the fix live and the prompt demonstrably
   containing `"city": "Mumbai", "city_local": "मुंबई"` side by side, the model
   still emitted `city_local = 'Lucknow'` / `' Bengaluru'` / `' Chennai'` /
   `'Delhi'` on 6/6 target queries, including the three English-line failures
   clause 2 needs.
2. **Not sufficient.** 4 queries already chose `city =` correctly under the OLD
   nullable-only regime (`d_ml_015_en`, `d_ml_016_en`, `d_ml_035`, `d_ml_036`).
3. **Same-schema counter-example — the decisive one.** `sellers.display_name`
   has *identical* NOT NULL/NULLABLE geometry and identical sampling, computed
   from `bench_multilingual_report_pre_b1.json`:

   | column | control | treatment | flips local→plain | flips plain→local |
   |---|---|---|---|---|
   | `display_name` | local 22, plain 1 | local 8, plain 20 | **15** | 0 |
   | `city` | local 14, plain 4 | local 18, plain 1 | **0** | **3** |

   A cause constant across both columns cannot produce opposite outcomes. This
   was computable from committed data **before any code was written**.

**The patch also had two defects of its own**, found in adversarial review and
confirmed: it was not purely additive — `orders`, `order_items` and
`logistics_providers` have ZERO nullable columns, so the old code sent
`columns=None` → `SELECT *`, and the patch *shrank* them (dropping
`order_value`, `placed_at`, `quantity`), on the highest-traffic interrogation
target; and it newly sampled `document_chunks.content_md` (`text NOT NULL`),
putting ingested RAG text into the SQL-agent prompt through a `SELECT` with no
tenant predicate. Both are reasons the change should not ship even if it had
worked.

### What attempt 2 should start from

**The live lead is the asymmetry in the table above:** normalization *fixes*
`display_name` (+15 flips to the romanised column, zero reverse) and *worsens*
`city` (romanised uses 4 → 1). Same schema shape, same sampling, opposite
result. Whatever distinguishes the two is B1's actual cause, and it is now
localised to the normalizer/entity-hint interaction rather than the schema.

**Three prompt-side channels are now eliminated** — column comments (005,
measured), the entity-hint instruction (shipped, still fails), and sampled
evidence (this attempt, measured). Attempt 2 should consider a **non-prompt**
mechanism, e.g. deterministic post-generation value linking: a literal that
cannot match its column's stored script is a checkable property, not a hint.
That is a genuine change in kind and should be proposed to the owner before
being built — it is closer to a feature than a fix.

**Two fixture defects that will distort any future B1 measurement:**
- `d_ml_034` is a **false pass**. Its SQL is `WHERE l.city_local = 'Nagpur'` —
  the exact B1 defect — and it scores `match=True` AND `all_preserved=True`
  because both it and the ground truth return scalar `0`. The defect is
  credited twice. `d_ml_017` and `d_ml_037` are also scalar-`0` ground truths.
- `d_ml_031`'s `'जयपुर'` is the **correct stored value**, not an invention; it
  fails D-V1 only for a missing `fulfillments` join. Genuine invented spellings
  are exactly two: `d_ml_035` `'पतन'` (stored `पटना`) and `d_ml_036` `'इंदोर'`
  (stored `इंदौर`).

---

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

---

## `c2g_15`'s filter is a no-op

*Added 2026-08-21, cycle 005 (audit 005 F-005-2). Disclosed, not fixed — same
shape of owner judgment call as the section above, and routed the same way.*

`c2g_15` asks *"Which sellers have a Devanagari display name recorded?"* On
this corpus **all 30 sellers match** and **0 are NULL** — the note attached to
the query in the fixture itself, *"display_name_local is nullable; only some
seed rows populate it,"* is false. The ground truth's `WHERE display_name_local
~ '[Devanagari range]'` filter is therefore a no-op: a query that omits it
entirely returns the identical 30-row result set (confirmed both ways — zero
rows exist only in one side and not the other, checked with `EXCEPT` in both
directions).

This is a different mechanism from the three zero-row queries above.
Those pass because `ExecutionAccuracyScorer` cannot distinguish two empty
results. This one passes because the reference and a wrong-condition query
happen to be the *same non-empty* result set on this seed data — the scorer is
working exactly as designed; the fixture's condition simply cannot be tested
by this corpus.

**Not hypothetical.** The shipped 7B's committed run
(`docs/m4_diagnosis_v2fixture_7b.json`) generates
`WHERE s.display_name_local IS NOT NULL` — not a script test — and is scored
`correct`, 30/30 rows matching. That pass is one of the fifteen behind the
published 60.0%. A second model in the committed reports
(`m4_diagnosis_v2fixture_qwen14b_general.json`) does the same thing.

The other fourteen passing queries were checked against this same class of
defect and are clean — differences from their ground truth are alias names,
`ORDER BY`, and the harness's `LIMIT 1000` only.

**Arithmetic, stacked on the section above:**

| basis | figure |
|---|---|
| published | 15/25 = **60.0%** |
| excluding the 3 disclosed unfailable queries | 12/22 = **54.5%** |
| **also excluding `c2g_15`** | **11/21 = 52.4%** |

**Disclosed at every publication point** 2026-08-21 (cycle 005 B1):
`AGENT_HANDOFF.md`, `V2_STRATEGY.md`, `metrics-report.md`. **The fixture was
not edited.** Unlike the three zero-row queries, `c2g_15` is **absent** from
the fixture's own `_audit.known_remaining_weakness` — nothing before audit 005
recorded that this query could not discriminate.

**Note for whoever closes it:** the ground-truth SQL is correct; the question
is undiscriminating *on this corpus*. Unlike the zero-row queries, there is a
cheap, unambiguous fix available — seed one seller's `display_name_local`
with a Latin-only value so the filter has something to exclude — that does not
require replacing the question or accepting a permanently attached caveat.
That is still a fixture edit, so it is still the owner's call, not made here.

---

## Five real-data questions state a restriction the data makes invisible

*Added 2026-08-23, cycle 006 (audit 006 F-006-4). Disclosed, not fixed —
same shape of owner judgment call as the two sections above, and routed the
same way. This is the same failure class as `c2g_15`, found in the real-data
fixtures audit 005 named as the largest hole in its own audit — "on a
1.45M-row corpus that class of defect is more likely, not less, and I did
not check for it."*

`ol_007`, `ol_008`, and `ol_012` (`fixtures/golden/olist.json`), plus
`oh_cs01` and `oh_cs03` (`fixtures/golden/olist_hard.json`), each state a
population or denominator restriction in prose — exactly the kind of
disambiguation the fixtures' own `self_authored_warning` names as a
deliberate safeguard (*"every ambiguous denominator is stated in the
question text"*). On this corpus, each restriction removes zero rows:

| id | restriction stated | verified no-op by |
|---|---|---|
| `ol_007` | "Considering ONLY orders that have a delivery date recorded" | `avg()` skips NULL regardless; identical result with the `WHERE` clause removed |
| `ol_008` | same restriction | `NULL > x` is never true; identical result with the `IS NOT NULL` removed |
| `ol_012` | ground truth's `WHERE product_category_name IS NOT NULL` | the 610-row NULL group never reaches the top-5 either way; identical top-5 |
| `oh_cs01` | "sellers" (implicitly: all of them, as a population) | 0 of 3,095 sellers have never sold — every seller appears in `order_items` |
| `oh_cs03` | "among products that have been ordered at least once" | 0 of 32,951 products have never been ordered — the restricted and unrestricted populations are identical |

**Three passes are demonstrably credited to a model that dropped the stated
restriction, not merely unaffected by it:**

- `ol_012`: the shipped 7B's generated SQL omits the `IS NOT NULL` filter
  entirely (`docs/m4_diagnosis_olist_local7b.json`), scored correct.
- `oh_cs01`: Gemini's generated SQL counts `DISTINCT seller_id` over
  `order_items`, not `sellers` — a different population than the question
  asks about — and lands on the same number, 1,161, because there is no
  seller absent from `order_items` for the two populations to disagree on.
- `oh_cs03`: the shipped 7B's generated SQL applies the `NOT EXISTS` check
  to `olist.products` directly, never restricting to products that have been
  ordered, and lands on the same number, 23,666.

**Does not move any published figure.** 88.9%, 94.4%, 50.0%, and 100.0% are
all unaffected — unlike `c2g_15`, none of these five is a case of SQL that
answers a *different* question being scored correct; each generated query
is a correct, defensible answer to the question as literally posed. The
caveat is about what the fixtures' own safeguard claims to test, not about
any pass being wrong.

**The generalisable cause, worth naming because it will recur:** real
transactional data is *complete* in ways synthetic seed data is not — this
corpus has zero orphan `order_items`, zero orphan `order_payments`, zero
sellers absent from `order_items`, zero products never ordered (verified
directly, `docs/audits/006.md` F-006-4). A restriction written to exclude
an awkward case excludes nothing when the corpus contains no awkward case.
Every future fixture written against `olist` inherits this risk.

**Disclosed at every publication point** 2026-08-23 (cycle 006 C1):
`AGENT_HANDOFF.md`, `V2_STRATEGY.md`, `metrics-report.md`. **Neither fixture
was edited.**

---

## Two hard-fixture questions are softer than their own labels

*Added 2026-08-23, cycle 006 (audit 006 F-006-5). Disclosed, not fixed —
routed the same way as the section above.*

`fixtures/golden/olist_hard.json`'s own `_about` block states its purpose
plainly: *"Built to test whether 88.9%/94.4% on the easy fixture is real or
an artifact of easy questions"* — every question is meant to require a
join across ≥3 tables, a correlated subquery, or a window function. Two of
the sixteen do not hold up to that claim, by two different mechanisms:

**`oh_j04` is labelled `join3` (11 questions carry this label) and is
answerable with a 2-table join.** The ground truth joins
`order_payments → orders → order_items`, but `orders` is a pure
pass-through — both neighbouring joins key on `order_id`, and nothing from
`orders` is selected or filtered. Both graded models generated the 2-table
form and scored correct:

```
GT (3 tables):  order_payments JOIN orders JOIN order_items
GEN (2 tables): order_payments JOIN order_items   -- orders dropped entirely
```

Both queries return the identical result — `credit_card|2951 boleto|2010
voucher|1046` — because 0 of the corpus's `order_items`/`order_payments`
rows are orphaned from `orders` (verified directly). Cycle 002's safeguard
6 (*"every join3 label is checked against the ground truth's own
distinct-table count"*, added for audit 002 F-002-7) cannot catch this: the
ground truth genuinely touches 3 tables, so the label passes its own check.
What safeguard 6 checks is the *reference SQL's* table count, not whether
the *question* actually requires that many.

**`oh_j07` defines "REPEAT CUSTOMER" in the question text, and the fixture
never tests whether a generated answer implements that definition.** The
question states: *"A REPEAT CUSTOMER is a customer_unique_id that placed
more than one order."* The ground truth implements exactly that. Gemini's
generated SQL implements a different definition — a customer counts as
repeat only if more than one of their orders has line items in
`order_items` — which disagrees with the question's own definition on 84 of
2,997 customers (verified: `gt_repeat=2997, gen_repeat=2913,
in_gt_only=84`). This is invisible in the score because the question's own
`LIMIT 5` selects the highest-item customers, none of whom are among the 84
where the definitions diverge.

**Does not move 50.0% or 100.0%.** Both models' generated SQL is a correct
answer to its question — `oh_j04`'s 2-table form and `oh_j07`'s alternate
repeat-customer definition both happen to be extensionally correct on this
data, the same shape as the no-op-restriction section above. This entry is
about what the fixture's difficulty labelling claims, not about a wrong
pass.

**Disclosed at every publication point** 2026-08-23 (cycle 006 C2):
`AGENT_HANDOFF.md`, `V2_STRATEGY.md`, `metrics-report.md`. **The fixture
was not edited** — re-labelling `oh_j04` or tightening safeguard 6 to check
question-necessity rather than reference-table-count is a fixture change
and an owner call, not made here.

---

## The real-data headline's difficulty caveat did not propagate

*Raised 2026-08-23 by the owner, reading the handoff's metrics table and asking
directly: "How credible is this metric?? sounds too good?" The instinct was
correct. Caveat propagated the same day; the underlying question is open.*

**What the number is.** `standard EX — real data (olist, n=18)`, published as
**local 7B 88.9% · Gemini 94.4%**.

**What makes it much easier than "text-to-SQL on real data" sounds.**
`scripts/gen_olist_fixture.py`'s docstring states the design as an explicit
goal:

> 1. EVERY question names its expected columns.
> 2. EVERY ambiguous denominator is stated IN the question.

Measured over the 18 questions (2026-08-23, against the live `olist` schema's
column names):

| | |
|---|---|
| name a real schema column **verbatim** in the question text | **12 / 18** |
| remaining 6 | state the filter/denominator in prose instead |
| **single-table** (ground-truth SQL touches ≤1 table) | **16 / 18** |
| involve any join at all | **2 / 18** |

Representative question, `ol_008`: *"Considering ONLY orders that have a
delivery date recorded, how many were delivered LATER than their estimated
delivery date? Return a single count."* That is a `WHERE` clause, a comparison
and an output shape, dictated in English.

**The safeguards are legitimate; the side effect is the problem.** They were
added deliberately after the `phase_c2` audit found under-specified questions
manufacture *false* failures under a scorer that compares projections — the
16pp instrument defect, designed out. Nothing here suggests the fixture was
built to flatter the system. But the consequence is that schema linking and
ambiguity resolution — the two hardest parts of the task — are absent by
construction, and what remains is closer to schema-guided SQL **dictation**.

**The tell.** A free local 7B at 88.9% would beat published BIRD dev-set state
of the art (roughly 70–75%, for far larger and heavily engineered systems). It
does not. The task is different, not the model better. The honest capability
signal is the hard fixture: **the same 7B falls to 50.0%, −38.9pp**, once
multi-table joins are required (`olist_hard`, n=16).

**This is a propagation failure, not a measurement failure.**
`docs/metrics-report.md` § "What it does establish" has always stated the full
caveat — column-naming, stated denominators, *and* the single-table skew — and
it is accurate. By the time the number reached `AGENT_HANDOFF.md`'s metrics
table and `V2_STRATEGY.md`'s headline table, all of it had compressed into the
single word **"well-specified"**, which no reader decodes as "the questions
name their own columns and 89% are single-table."

**Same defect class as audit 002 F-002-6**, which caught exactly this for the
*hard* fixture (*"B2 specified 'deliberately ambiguous phrasings'; the fixture
systematically eliminates ambiguity, and 100.0% is published without that
stated"*). It was never applied to the easy fixture.

**Done 2026-08-23:** the full caveat now travels with the number in
`AGENT_HANDOFF.md` §6 and `V2_STRATEGY.md`'s headline table, including the
BIRD comparison and the pointer to the hard-fixture figure as the honest
capability signal.

**Still open, and an owner call:** whether a schema-guided figure should remain
*the* published real-data headline at all. Three options, none free:
(a) keep it with the caveat permanently attached, as now;
(b) demote it and lead with the hard-fixture 50.0%/100.0% instead, which is
    harder but n=16 and carries its own ambiguity caveat (F-002-6);
(c) author a genuinely natural-language fixture over `olist` — questions that
    do *not* name their columns — accepting that the projection-mismatch false
    failures the current design exists to prevent will come back, and that the
    resulting number will be substantially lower and not comparable to the
    existing one.

**Note for whoever closes it:** this strengthens the case for a BIRD/Spider
subset run (`docs/benchmark_readiness.md`) above almost everything else on the
list. It is the only route to a number that means anything to a reader outside
this repository, and it is precisely the question the owner asked.

## The A2 concurrency assertion cannot fire — added 2026-08-24, audit 007 (F-007-4)

The Build Spec is unambiguous: a last-write-wins field written by two
concurrent nodes is a defect, and it must be "enforced with an assertion in
the merge path, active in all environments including production."
`orchestrator/state.py::merge()` implements the assertion correctly in
isolation — a live probe inside the running container confirms it fires when
two nodes write the same field in one merge cycle.

It cannot fire in the graph. `graph.py`'s node wrapper calls
`merge(state, update, node_name=...).model_dump()` after every node, and
`model_dump()` drops `_lww_writers` (it is set via `object.__setattr__` and
excluded from serialization by design — the field isn't declared on the
model). LangGraph rebuilds a fresh `StateSyncState` from that dict for the
next node, and `model_post_init` resets `_lww_writers` to `{}`. Every node
therefore starts with an empty writer map, so the "was this field already
written by a different node this cycle" check can never see a prior writer.

The corroborating detail: `state.py::reset_lww_writers()` — the function
whose own docstring explains why writer-tracking needs a reset point at the
start of each parallel dispatch batch — is imported by `graph.py` and called
nowhere. It was written for a design where the tracking *persists* across
nodes within a batch; the actual graph resets it on every single hop instead,
making the reset function unreachable dead code and the assertion body
unreachable for a different reason than the one the reset function guards
against.

**Does not currently cost anything.** The shipped graph has no parallel
dispatch — `supervisor_node` routes to exactly one of `sql`/`docs`/`hybrid` —
so there is nothing today for a concurrent double-write to happen to. The gap
matters the moment a future cycle adds real parallel nodes (a candidate:
running the SQL and doc sub-tasks of a hybrid query concurrently instead of
sequentially, which `unstructured_knowledge.py`'s decomposition already sets
up the pieces for) — at that point the safety net the spec requires will
still be absent, silently.

**Fix shape, not attempted here:** either (a) make `_lww_writers` survive the
`model_dump()`/rebuild round-trip (a serialized, non-underscore field, which
changes the public schema), or (b) move the concurrency check to a layer that
sits outside the per-node dump/rebuild cycle — e.g. tracked once per
`graph.ainvoke()` call in `main.py`, keyed by `query_id`, reset explicitly
when a new query starts. Neither was implemented in cycle 007: multi-turn
touched this file only to add three new LWW fields, all single-writer, and
did not add parallel dispatch.

## SchemaRetriever Protocol omits the parameter its only implementation needs — added 2026-08-24, audit 007 (F-007-5)

`orchestrator/schema_retriever.py`'s `SchemaRetriever(Protocol)` declares
`retrieve(self, session, table_names) -> tuple[str, list[dict]]`.
`DirectSchemaRetriever.retrieve` — the only implementation, and the one every
call site actually uses — requires a third parameter, `schema: str | None`,
without which the Olist real-data path cannot be reached (`sql_agent.py`
passes it positionally at two call sites). A second implementation written
*to the declared interface* — exactly what §5a's pre-committed top-k
candidate selector would be — would satisfy the Protocol and break on the
third positional argument at either call site.

`@runtime_checkable` does not catch this: `isinstance()` against a
runtime-checkable Protocol checks method names, not signatures, and nothing
in the codebase calls `isinstance` against `SchemaRetriever` anyway. The seam
gate that reports this as passing (`verify_seams.py`, part of the 26/26
figure) checks for the literal string `"class SchemaRetriever(Protocol)"` via
regex — true regardless of what the class declares.

**Not fixed in cycle 007.** Cycle 007 built two new MCP tools and one new
LLM role; it did not touch `schema_retriever.py` or add a second
`SchemaRetriever` implementation. Whoever builds §5a's top-k selector should
fix the Protocol's signature (add `schema: str | None = None`) as part of
that work, before writing the second implementation against it — writing the
second implementation first and discovering the mismatch by breakage would
repeat the exact shape of this finding.

## `run_e2_v1.py` crashes on an unregistered `--force` — added 2026-08-24, audit 007 (F-007-6)

`scripts/run_e2_v1.py` reads `args.force` at its append-only-guard check but
never registers `--force` on its `argparse.ArgumentParser` (every sibling
harness that has this guard — `bench_multilingual.py`, `bench_memory.py`,
`bench_correction.py`, `check_consistency.py` — registers it; this one
doesn't). Reproduced live: `python3 scripts/run_e2_v1.py --out
docs/metrics-report.md` raises `AttributeError: 'Namespace' object has no
attribute 'force'` before any query runs.

Fails safe — the crash happens before the overwrite it's guarding against, so
nothing was lost — but the guard is unusable on its one intended trigger
(re-running into an existing `--out` path on purpose). Fix is one line
(`ap.add_argument("--force", action="store_true", ...)`, matching the other
four harnesses); not applied in cycle 007, which did not touch this file.

## Supervisor's route-parse fallback iterates a `frozenset` — added 2026-08-24, audit 007 (F-007-7)

`orchestrator/nodes/supervisor.py::_extract_route`'s regex fallback (used
when the LLM's response isn't valid JSON) does `for candidate in
_VALID_ROUTES: if re.search(...)`, where `_VALID_ROUTES` is
`frozenset({"sql", "docs", "hybrid"})`. Python randomizes string hashing per
process (`PYTHONHASHSEED` unset), so the iteration order — and therefore
which route wins when the response text matches more than one candidate —
changes between orchestrator restarts. Confirmed live in the running
container: three separate `python -c` invocations printed three different
orders.

**No evidence this has ever changed a routing decision** — the fallback only
runs on a JSON parse failure, whose rate was not measured here, and M9's
published 0.983 was measured within one process where the order was fixed
for the run's whole duration. The finding is a reproducible source of
cross-restart nondeterminism in a component whose headline property is
determinism, not a demonstrated wrong route. Fix is trivial (iterate a tuple
in a fixed priority order instead of a set); not applied in cycle 007, which
did not touch `supervisor.py`.

## The shipped-flag guard does not cover `config/statesync.toml` — added 2026-08-24, audit 007 (F-007-9)

`scripts/shipped_flags.py`'s `SHIPPED_DEFAULTS` guards five keys, all in
`config/flags.toml`. Metric-moving constants that live in
`config/statesync.toml` instead — `citation_max_chunks` (the constant behind
M6's 0.667 context-precision ceiling, audit 003 F-003-4), `semantic_top_k`
(the constant cycle 006's addressability result is stated against) — are read
by nothing in the guard and appear in no `flag_provenance` block. A change to
either would produce a report that looks byte-identical in provenance to
every one before it.

**Never fired.** `git log -- config/statesync.toml` shows 13 commits, all
additions; `git log -p` shows zero modifications to either constant since
each was introduced. No published number is affected by this gap as things
stand. Cycle 007 itself *added* two new constants to this same file
(`max_tokens_rewriter`, `rewrite_history_turns`, `history_get_turns_hard_cap`
— BD10) without adding statesync.toml to the guard's coverage, which is the
same class of omission, made again rather than closed. Left open rather than
scope-crept into this cycle: closing it means deciding which subset of
statesync.toml's many values are actually metric-moving (a real design
question, not a mechanical one), which cycle 007 did not have budget to do
carefully.

## `_signature`'s exact-match may false-fail AVG-style ground truth in bench_multilingual.py too — added 2026-08-25, found building cycle 007

Building `bench_multiturn.py` (Workstream A) found that reusing
`bench_multilingual.py`'s `_signature` verbatim (justified by audit 007
F-007-3 for alias-insensitivity) produces a **second**, different false
failure: a ground truth that rounds an aggregate (`ROUND(AVG(...),2)`, for
readability) does not match generated SQL that computes the same `AVG`
unrounded — confirmed live, `19.99` vs Postgres's own
`19.9903199289835775` for the identical query. Fixed in
`bench_multiturn.py` (`_round_floats`, rounds any float value to 2dp before
comparison).

**Not fixed in `bench_multilingual.py` itself — not verified to matter
there either.** `fixtures/golden/phase_d_multilingual.json` contains 4
occurrences of `AVG`/`avg(` in its ground truths, so the same false-failure
shape is *possible* in D-V1's published numbers. This was not checked: audit
007 F-007-10 already found D-V1 has an unrelated ±7.5pp run-to-run noise
floor, and chasing a second, unverified defect class in a harness this
cycle did not otherwise touch would have been scope creep on a build cycle,
not an audit. Whoever next touches `bench_multilingual.py` should check the
4 AVG-bearing queries' ground truth against generated SQL directly before
assuming this doesn't apply.
