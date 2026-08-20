# Metrics report — M1 through M10

Phase F Deliverable 7. Every value as measured, with procedure and date.
Per Strategy §7.0: **model-independent** metrics stand as measured on any
model and are reported as final here. **Model-sensitive** metrics are
measured on the local model (`qwen2.5:7b`) but are **local, provisional**
until the graded (Anthropic) re-baseline runs (`docs/rebaseline_runbook.md`)
— never overwritten, only appended to, per the runbook's own §5 rule.

Model-sensitive metrics below carry two columns: **Local (provisional)** —
this build's own measured number — and **Graded (pending)** — filled in only
after `docs/rebaseline_runbook.md` executes. An empty Graded column is not a
missing measurement; it is the honest state of a metric that has not yet
been re-baselined.

Date: 2026-08-15. Local model: `qwen2.5:7b` (generation), `mxbai-embed-large`
(embeddings), via the BD1 provider seam (`flags.llm.provider = "local"`).

---

## Model-independent metrics (final as measured)

| Metric | Value | Procedure | Verified |
|---|---|---|---|
| **M1 direction** — memory reduces tokens/trajectory | −39.2% tokens, −73.1% trajectory steps (control vs. treatment A/B) | `scripts/bench_memory.py` | C1 (2026-08-14); Phase-F re-run below is not directly comparable — see note |
| **M8** — adversarial security suite | **PASS IN FULL — 36/36, 0 failed** (binary metric; see F-V1 section) | `scripts/verify_security.sh` | Part F (this run) |
| **M9** — routing determinism ≥ 90% | **PASS — 0.983 (59/60)** on the hardened fixture, `sql` = 1.000 including all six overlap-vocabulary cases (earlier run on the pre-fix fixture: 60/60 100% — retained, and NOT a clean before/after: prompt and fixture changed in the same step). Over-exposure (9→40 surface) shows no degradation, honestly recorded as a non-reproduction | `scripts/experiment_tool_ceiling.py` | Part F baseline (`docs/f_v2_report.json`); re-run after the Supervisor routing fix `c735457` (`docs/f_v2_report_postfix.json`, `docs/phase_f_log.md`) |
| **M10** — reproducibility | Stack comes up from a clean bring-up; golden set executes | B-V1 (`scripts/verify_b.sh`), re-run at F-V3 | Part F (this run) |
| Structural verification (network isolation, credential absence, 3 SELECT-only layers, role separation, tool ceiling, seam resolution, output-contract enforcement) | All pass | B-V3..B-V9, E1-V6/V7/V8, E2-V3..V6, F-V4/F-V5/F-V6 | Part F (this run) |

**M1 comparability note.** The Phase-F gap-close session added one extra
LLM call to the SQL Agent's miss path (draft-first interrogation targeting,
`docs/phase_e_gap_log.md` Task 4) — the miss path *is* M1's control arm, so a
fresh `bench_memory.py` run measures a heavier control than the C1-era
−39.2%/−73.1% figures did. Both numbers are retained on record; they are not
directly diffable against each other. See F-V3 below for the fresh run.

---

## ⚠ Build provenance: the SQL prompt changed under this table

**Added by the fresh-context audit, 2026-08-16.** `da6e49f` ("projection
discipline") added a rule to **both** SQL prompts in
`orchestrator/nodes/sql_agent.py`. It was validated on one fixture
(`phase_c2.json`, 25 queries) and shipped.

Every model-sensitive metric below generates SQL, so that change moved the
build all of them were measured against. **Only M4's cold path was
re-measured after it.** M2, M3, M5, M6 and M7 on this table were measured on
the pre-`da6e49f` build and their provenance is now stale — not wrong, but no
longer attributable to the code that ships.

That is a real gap and it is stated rather than smoothed. The commit's own
message records that the change *moved* failures rather than removing them
(two queries recovered, one regressed, value recall −2.8pp), so the direction
of its effect on the other metrics is genuinely unknown, not assumed benign.

| Metric | Measured on | Re-measured after `da6e49f`? |
|---|---|---|
| M4 cold (C2-V8), standard EX, value recall | post-change build | **yes** (re-run again 2026-08-16 on the corrected diagnostic) |
| **M3 (D-V1 / D-V2 / D-V3)** | **post-change build** | **yes — and it REGRESSED, see below** |
| M2, M4 warm, M5, M6, M7 | pre-change build | **no** |

### The risk this section flagged was not a risk. It happened.

M3 re-measured on the current build (`docs/bench_multilingual_report_postprojfix.json`,
the first report carrying `flag_provenance`):

| | pre-`da6e49f` | post | delta |
|---|---|---|---|
| **D-V1 treatment (the arm that ships)** | 26/40 65.0% | **22/40 55.0%** | **−10.0pp** |
| D-V1 treatment · Devanagari half | 15/20 75.0% | 12/20 60.0% | −15.0pp |
| D-V1 lift | +25.0pp | +15.0pp | −10.0pp |
| English reference line | 12/20 60.0% | 11/20 55.0% | −5.0pp |
| D-V2 entity preservation | 26/32 81.2% | 26/32 81.2% | 0.0pp |

**In query counts across both fixtures, `da6e49f` won +2 and lost −5.** It was
validated on `phase_c2` alone and shipped. The control arms are identical by
coincidence, not inaction — 45 of 60 control SQL strings changed between the
runs; checked, not assumed.

**Do not revert on these numbers alone** — both effects are small-n, and the
graded probe is next. Full reasoning and the recommendation:
`docs/phase_d_log.md` § "M3 re-measured on the current build".

The lesson generalises past this instance: a prompt change validated on one
fixture silently re-dates every other model-sensitive number in the repo.
A build identifier belongs in each report so this is visible without a
timestamp archaeology exercise.

## Real-data results (olist corpus, 2026-08-18)

**The project's first numbers over data it did not author.** 18 questions,
1,451,414 real rows (`fixtures/golden/olist.json`, `migration 007`). Paired —
same questions, same corpus, only the provider differs:

| | local `qwen2.5:7b` | Gemini (mixed, per-role) |
|---|---|---|
| strict EX | 16/18 = **88.9%** | 17/18 = **94.4%** |
| standard EX | 16/18 = **88.9%** | 17/18 = **94.4%** |
| dirt-probing subset (7) | 5/7 | 6/7 |

**⚠ Both arms ran with `normalization.enabled = false`, against a shipped
default of `true`** (`docs/m4_diagnosis_olist_local7b.json` and
`docs/m4_diagnosis_olist_gemini.json`, `flag_provenance.shipped: false`,
`disk_deviations: {"normalization.enabled": {"expected": "true", "found":
"false"}}`). Added by audit 001, F-001-2 — the harness recorded this
faithfully; nothing at the point of publication did. **The paired comparison
above (88.9% vs. 94.4%) is unaffected**, since both arms share the same
deviation. What is NOT established is that either number matches what the
shipped configuration would score — that has not been measured. Disabling the
normalizer here is a deliberate choice recorded in `config/flags.toml`
(`[llm.models.gemini]`: "D-V3 proved English passes the gate byte-identical
20/20, so the call is a measured no-op") but that evidence covers the
*synthetic* multilingual fixture on the *local 7B*; extending it to Olist and
to Gemini is an untested extrapolation, not a re-confirmation.

**+5.5pp is ONE QUERY.** At n=18 a single item moves the score 5.6pp, so the
two are **indistinguishable on this fixture** — not "the hosted model is
better".

**Do NOT quote 94.4% next to phase_c2's 60%.** The olist fixture is easier in
three nameable ways, two deliberate: it was authored *after* the phase_c2 audit
with every question naming its columns and every denominator stated (so the
instrument defects worth 16pp there are absent by construction); its questions
are mostly single-table aggregates rather than the 2-hop joins BD6 records as
this system's weakness; and its schema is 9 flat tables against 12 with a
denser FK graph.

**What it does establish:** on real, dirty, 1.45M-row data with well-specified
questions, this system answers 17 of 18 correctly, and a free local 7B is
within one query of a hosted model. The same comparison on synthetic phase_c2
showed a +20pp gap — so most of that gap was the fixture punishing
under-specification, not analytical capability. A stronger model was partly
buying its way out of ambiguity.

**Spending consequence:** frontier credits are worth spending on hard
multi-hop joins and ambiguous questions, not on ordinary analytical aggregates
over real data, where local is free and equivalent.

Failures are non-overlapping — local missed `ol_003`/`ol_017`, Gemini missed
`ol_007` (avg of an INTERVAL: 1,085,071 seconds where the question asked for
days). Neither model is strictly better at this sample size.

Artifacts: `docs/m4_diagnosis_olist_gemini.json`,
`docs/m4_diagnosis_olist_local7b.json`, both carrying per-role model
provenance in `flag_provenance`.

## Model-sensitive metrics (local provisional, pending graded re-baseline)

| Metric | Local (provisional) | Graded (pending) | Procedure | Local date |
|---|---|---|---|---|
| **M2** — consistency (C1-V3) | FAIL — treatment mean **1.8** distinct result sets vs. control 1.0 (fresh, worse than the 1.5 on record — see `docs/phase_f_log.md` for the run-to-run variance discussion) | — | `scripts/check_consistency.py` | 2026-08-15 (Phase F re-run) |
| **M3** — multilingual lift (D-V1) | **PASS: +25.0pp (16/40 40.0% → 26/40 65.0%)** after the Supervisor routing fix. Treatment is now ABOVE the Phase D record (65.0% vs 62.5%); the lift is smaller than +47.5pp **because the control arm rose 15.0%→40.0%, not because treatment fell**. Prior pre-fix run (32.5%→57.5%) retained below | — | `scripts/bench_multilingual.py` | 2026-08-15 (F-V3 post-fix re-run) |
| **M3** — entity preservation (D-V2) | **RECOVERED: 26/32 (81.25%)**, within noise of the 84.4% record (pre-fix run: 18/32 56.2%, retained) | — | same run as D-V1 | 2026-08-15 (F-V3 post-fix re-run) |
| **M4** — query correctness (C2-V8) | Cold **28.0% (7/25)**, warm **32.0% (8/25)** in the F-V3 post-fix run (earlier Phase F run: cold 28.0%, warm 36.0% — retained). **Golden recovery now `still_failing: []`** (b_007, b_008 recovered). **SUPERSEDED for the cold path by the projection-discipline change (`da6e49f`): 9/25 = 36.0% under the same C2-V8 signature** (`docs/m4_diagnosis_projfix.json`). The warm path has NOT been re-measured since that change | — | `scripts/check_c2v8.py`; post-change cold value from `scripts/diagnose_m4.py` (identical signature) | 2026-08-15 (F-V3 post-fix re-run); cold re-measured 2026-08-16 |
| **M4** — standard Execution Accuracy (BIRD/Spider EX) | **44.0% (11/25)**, measured by the shipped `ExecutionAccuracyScorer`. The earlier 40.0% → 44.0% figures came from an in-script definition that sorted values *within* a row and was **not** BIRD's (`f46e0bf`); on this fixture both definitions give 11/25, so the published number survives the correction. Reported alongside M4, never instead of it | — | `scripts/diagnose_m4.py` → `docs/m4_diagnosis_v2_corrected.json` | 2026-08-16 (clean serial re-run) |
| **M4** — mean value recall (diagnostic, NOT a capability metric) | **74.0%**, down from 76.8% pre-projection-fix. Read with the warning the script now carries: it is unaligned, cross-column and de-duplicated, so a query returning 30 rows for a 1-row answer scores 1.00. Mean **row** recall, from the shipped scorer, is **48.0%** | — | `scripts/diagnose_m4.py` | 2026-08-16 |
| **M5** — self-correction repair rate (C2-V2) | **11/12 classified correctly** in the F-V3 post-fix run (earlier Phase F run: 10/12 — retained; Phase C2 original 12/12). The single miss is the `timeout` seed `mq_11`, the same client-side HTTP timeout artifact, not a classification failure | — | `scripts/bench_correction.py` | 2026-08-15 (F-V3 post-fix re-run) |
| **M6** — grounding (E1-V3) | Precision **0.622**, recall **0.80**, faithfulness **0.8125**, **4** ungrounded-but-true — reproduces the earlier Phase F run (0.622 / 0.80 / 0.831 / 4) within noise on 16 scored queries | — | `scripts/bench_grounding.py` | 2026-08-15 (F-V3 post-fix re-run) |
| **M7** — chart quality (E2-V1) | **0.4850** mean spec score, `chart:null` 7/30; over the 23 charts actually produced, 0.633. (First run, pre-JSON-fix: 0.4592 / 8 nulls — kept, not overwritten.) **Read with care: 5 of the 8 original nulls are upstream SQL returning 0 rows or the wrong shape, so this figure is partly a downstream reflection of M4, not a clean measure of chart competence** — see `docs/phase_e2_log.md` for the per-query accounting | — | `run_e2_v1.py` then `score_specs.py --score` | 2026-08-15 (re-run, post-JSON-fix) |
| **M7** — A7 validity (E2-V1, binary) | **PASS — 30/30 responses A7-valid**, 0 invalid, 0 transport errors. Not model-sensitive in the same way: this must hold on any model | — | `scripts/run_e2_v1.py` | 2026-08-15 |
| C2-V1 (2-hop join redirect, BD6) | FAIL, conditions 3–4 | — | part of `bench_correction.py`'s C2-V1 worked example | 2026-08-14 |
| D-V6 (spatial exact count, BD6) | FAIL — 41 vs. hand-computed 30 | — | `scripts/check_dv6_spatial.py` | 2026-08-15 |

**F-V3 fresh-run values** (this session, model unchanged from the dates
above — `qwen2.5:7b`) are recorded in `docs/phase_f_log.md`'s F-V3 section
and in each script's own freshly-overwritten `docs/*_report.json`. Where a
fresh number differs from the value on record above, both are shown in the
phase log; this table is not silently overwritten, per
`docs/rebaseline_runbook.md` §5's rule extended to this report.

### M3 regression, reported as F-V3 requires

> **RESOLVED 2026-08-15 — read this box before the analysis below.** The
> routing defect named at the end of this section was fixed (`c735457`) and
> M3 re-measured in the F-V3 post-fix run. **D-V2 recovered: 56.2% →
> 81.25%** (record: 84.4%). **D-V1 treatment 57.5% → 65.0%, now ABOVE the
> Phase D record of 62.5%.** The lift stayed at +25.0pp because control also
> rose (32.5% → 40.0%) by exactly the same 3 queries — routing sits upstream
> of normalization, so it cannot move the difference between the arms. The
> lift is below +47.5pp because the **baseline improved**, not because
> treatment degraded. Residual genuinely below record: the English reference
> line, 60.0% vs 80.0%. Full write-up: `docs/phase_d_log.md` §"M3
> re-measurement after the Supervisor routing fix".
>
> The analysis that follows is the PRE-FIX investigation, kept intact as the
> record of how the cause was found. Its numbers are the pre-fix numbers.

F-V3: "A metric that regressed since its original Part is reported as a
regression, not silently overwritten." M3 regressed on both halves:

| | Phase D (on record) | Phase F (fresh, isolated) |
|---|---|---|
| D-V1 control | 6/40 (15.0%) | 13/40 (32.5%) |
| D-V1 treatment | 25/40 (62.5%) | 23/40 (57.5%) |
| D-V1 lift | **+47.5pp** | **+25.0pp** |
| D-V2 entity preservation | **27/32 (84.4%)** | **18/32 (56.2%)** |
| English reference line | 16/20 (80.0%) | 9/20 (45.0%) |

**The English reference line is the diagnostic.** English queries pass
through the normalization gate untouched (D-V3 confirms this in both runs:
20/20 `normalized_query_unchanged=True`), so an English-only drop of
80%→45% cannot be caused by anything in the multilingual path. Something
upstream of, or beneath, normalization changed. Two deliberate Phase-E-gap
changes are the candidates, and they are not yet separated:

1. **`memory.enabled` default flipped `true`→`false`** (gap-packet Task 3,
   `docs/spec-gaps.md` NOTE-1). Phase D measured M3 with memory ON; this run
   measured it with memory OFF. Repeated/similar queries within the 40-query
   set that previously hit cached trajectories now each take the full
   miss path.
2. **Draft-first interrogation targeting** (gap-packet Task 4,
   `docs/phase_e_gap_log.md`) changed miss-path SQL generation — and with
   memory off, *every* query is now a miss-path query.

**Separation run completed 2026-08-15 — see `docs/phase_d_log.md` §"M3
separation run" for the full experiment.** Outcome:

- **Candidate 1 (memory flip) is REFUTED.** Re-running with
  `memory.enabled=true` made every absolute measure *worse* (English
  45%→25%, treatment 57.5%→42.5%, entity preservation 56.2%→43.8%).
  **Decision: the shipped default stays `false`** (runbook §4).
- **~20 of the 35 lost English points are a routing defect**, the same one
  tracked as `c2g_10`: four English queries (`sellers.category` and
  `sellers.contracted_sla_hours` lookups) deterministically route to `docs`
  and never execute SQL — identically in both arms, verified live.
- **Residual ~15 points remain unattributed.** Candidate 2 (draft-first
  interrogation) is the only live hypothesis; isolating it needs a
  miss-path-without-draft-first condition that has no toggle today. Not
  guessed at.

*(A first F-V3 run produced worse numbers still — +20.0pp / 43.8% / 25%
English — from a genuine harness bug in `scripts/verify_fv3.sh`: the
previous step, `check_c2v8.py`, deliberately leaves `memory.enabled=true`
on exit for its own golden-recovery assertion, and D-V1 inherited it. That
run is discarded, the bug is fixed (flags reset to shipped defaults before
every step), and the numbers above are from a clean isolated re-run. Both
runs are described in `docs/phase_f_log.md`.)*

---

## F-V1 — M8 adversarial security suite

See `docs/phase_f_log.md` for the full class-by-class breakdown
(`scripts/verify_security.sh`). Binary per the Strategy Document: "This
metric is binary. A partial pass is a fail."

## F-V2 — M9 routing determinism + over-exposure experiment

**Baseline (M9, final): 60/60 (100%)**, all three routes 1.000 — well above
the 90% threshold, through the live, unmodified production Supervisor.

**Over-exposure experiment: no degradation reproduced.** Accuracy held at
1.000 across all four surface sizes (9/15/25/40 total capability
descriptions, i.e. 6/12/22/37 decoys), 5 runs each, 12-query stratified
subsample. `accuracy(40) < accuracy(9)`: **False** — recorded as an honest
non-reproduction per F-V2's own instruction ("do not tune the experiment
until it reproduces"), not adjusted to force a curve.

Full data: `docs/f_v2_report.json`. See `docs/phase_f_log.md` for discussion
of what this non-reproduction plausibly does and doesn't say about the
Supervisor's routing prompt.

---

## Hard-fixture results (olist_hard corpus, 2026-08-20)

**Cycle 001, Workstream B2. Answers the question `V2_STRATEGY.md` §4 B2 asks
before any number goes in the report: does 88.9%/94.4% survive harder
questions, or is it an artifact of easy ones?** For the local model: no, by a
wide margin. For the hosted model: yes.

`fixtures/golden/olist_hard.json`, 16 questions, same real Olist corpus as
`olist.json` but every question requires at least a join across ≥3 tables, a
correlated subquery, or a window function
(`scripts/gen_olist_hard_fixture.py`). Paired with the easy fixture's
configuration exactly — **both arms ran with `normalization.enabled = false`
and `schema_retriever.target_schema = "olist"`**, both deviations from the
shipped default (`flag_provenance.disk_deviations` in both source reports;
`target_schema` guarded since audit 001 F-001-1 / cycle 001 A1, so this
deviation is now on record where it never was before):

| | local `qwen2.5:7b` | Gemini (mixed, per-role) |
|---|---|---|
| strict EX | 8/16 = **50.0%** | 16/16 = **100.0%** |
| standard EX | 8/16 = **50.0%** | 16/16 = **100.0%** |
| mean row recall | 51.2% | 100.0% |

**⚠ This is a 38.9pp drop for the local model, against a 5.5pp GAIN for
Gemini**, comparing to the easy fixture's 88.9%/94.4%. The easy-fixture gap
between providers was one query (`metrics-report.md`, "Real-data results");
here it is 8 of 16. **88.9% does not survive harder questions. 94.4%, on this
one hard-fixture sample, does** — though n=16 with 0 failures means the upper
bound on Gemini's true error rate here is not tightly pinned by this sample
alone.

**Failures classified** (`scripts/attribute_failures.py
docs/m4_diagnosis_olist_hard_local7b.json docs/m4_diagnosis_olist_hard_gemini.json`):

| | local 7B | Gemini |
|---|---|---|
| correct | 8 | 16 |
| wrong_tables (proxy) | 3 | 0 |
| same_tables | 5 | 0 |
| no_sql | 0 | 0 |

**The 3 "wrong_tables" are a tool artifact, not a genuine finding — verified
by reading the actual SQL, not taken from the proxy.**
`attribute_failures.py`'s table-extraction regex
(`\b(?:FROM|JOIN)\s+([a-zA-Z_][a-zA-Z0-9_]*)`) has no awareness of a `WITH`
CTE, so it counted `ranked` and `order_totals` — the CTE names in this
fixture's own ground-truth SQL for `oh_wf01`/`oh_wf03`/`oh_wf04` — as
"missing tables" the model failed to select. Reading the model's actual SQL
for all three shows it selected the CORRECT real tables
(`olist.order_items`, `olist.products`, `olist.sellers`,
`olist.order_payments`, `olist.orders`, `olist.customers` — matching the
reference in every case) and attempted the right window-function shape
(`ROW_NUMBER()`/`RANK() OVER (PARTITION BY ...)`), but the SQL itself failed
to execute: an outer-query alias reference the parser couldn't resolve
(`oh_wf01`, `unknown_table` on `oi`), a bad window-frame clause on the first
attempt (`oh_wf03`, `"frame start cannot be UNBOUNDED FOLLOWING"`), and a
genuine ambiguous-column / wrong-join-key bug (`oh_wf04`, joining back to
`customers` on `customer_state` rather than `customer_id`, which is both
ambiguous and semantically wrong). **The correct classification for all
three is `same_tables`** (intent arrived, the SQL over the right tables was
wrong) **— the true split is 0 wrong_tables / 8 same_tables**, not 3/5. This
is a real, previously-unknown limitation of `attribute_failures.py` on any
ground truth using a CTE — recorded here since the tool's own docstring says
the proxy "does not adjudicate individual cases," and this cross-check is
exactly why not.

**CORRECTED cycle 002 (audit 002 F-002-5).** This section previously stated
*"every one of the local model's 8 failures … is a genuine SQL-writing
capability gap on nested-subquery and window-function syntax, none of them a
misunderstanding of what was asked."* That is true of 3 of the 8. Reading all
eight `generated_sql` values individually (`docs/m4_diagnosis_olist_hard_local7b.json`):

| id | published category | what actually happened |
|---|---|---|
| `oh_wf01` | SQL execution error | nested-subquery scope: `missing FROM-clause entry for table "oi"` — the published class, correct |
| `oh_wf03` | SQL execution error | window-frame syntax: `frame start cannot be UNBOUNDED FOLLOWING` — the published class, correct |
| `oh_wf04` | SQL execution error | window/join: `column reference "order_id" is ambiguous` — the published class, correct |
| `oh_j03` | SQL execution error | **reserved-word alias**: `JOIN olist.order_reviews or ON …` — `or` used as a table alias. Not nested, not a window |
| `oh_j08` | SQL execution error | **schema grounding**: `column p.product_category_name_english does not exist` — invented an English-name column on `products` instead of joining `product_category_name_translation` |
| `oh_j09` | SQL execution error | **schema grounding**, same cause as `oh_j08`; also wrote `LIMIT 1000` for a question asking for "the 5 categories" |
| `oh_j07` | right row count, wrong values | **comprehension**: `SUM(oi.order_item_id)` where the reference is `count(oi.order_item_id)` — the question asks for "the total NUMBER of order_items," the model summed an ordinal id; its `HAVING count(o.order_id) > 1` also counts item rows, not orders, so it drifts from the question's own explicit "REPEAT CUSTOMER" definition. Executed cleanly, no syntax error |
| `oh_wf02` | right row count, wrong values | **comprehension**: `COUNT(*) OVER ()` after `GROUP BY review_score` returns 5 (the number of *groups*), not the review total — the cumulative-percentage denominator is wrong. Executed cleanly, no syntax error |

**Honest split of the 8: 3 nested-subquery/window-function syntax (as
published), 1 reserved-word alias, 2 schema grounding, 2 comprehension.** Two
of eight are the model failing to find
`olist.product_category_name_translation` in a nine-table schema, not a
syntax gap — see the "Real-data join graph" section below (F-002-10): `olist`
declares **zero** foreign keys, so nothing points the model at the table it
needed. Two more are logic errors on questions the fixture went out of its
way to disambiguate (`oh_j07`'s own note explains the fixture defines "repeat
customer" explicitly *because* the corpus sets this trap). Routing every
failure to "SQL syntax" would point a remedy at generation; the schema-
grounding half points at retrieval instead. **`50.0%` and `100.0%` are
unchanged — this correction is to the failure ANALYSIS, not the score.**

**Fixture defect rate, audited the same way `phase_c2` was** (`V2_STRATEGY.md`
§6's replication claim — this is the second independently-authored fixture):
**1 of 16 (6.25%)**, found and fixed BEFORE any model was scored, not
discovered afterward. `oh_wf01` asked for "the single highest-priced
order_item" per category, and the max PRICE is unambiguous in every category
— which `product_id` represents it is not, wherever more than one product
ties at that max. The SQL resolved this deterministically (lowest
`product_id`) but the original question text did not say — fixed by stating
the tie-break in the question. The ground truth as shipped was never actually
wrong (the 5 categories in this fixture's top-5 are not among the tied ones),
but the wording was.

**CORRECTED cycle 002 (audit 002 F-002-1 / F-002-2).** This paragraph
previously stated the tie affected *"15 of ~74 real categories … e.g. `audio`
has 6 products tied at 598.99"*, and credited the automated LIMIT-boundary
check (safeguard 4) with finding it. Both were wrong. The corpus has **71**
categories, not ~74, and the 15-category figure counted `order_item` **rows**,
not distinct products — `audio`'s "6 products tied at 598.99" is 6 rows of
the *same* product (`product_id` constant), which is not ambiguous, since the
projected `product_id` is identical either way. The correct figure, now
verified by an automated partition-tie probe added this cycle
(`gen_olist_hard_fixture.py` safeguard 5) rather than computed by hand: **4 of
71** categories have a genuine multi-*product* tie at the category max price
(`luggage_accessories`, `furniture_mattress_and_upholstery`,
`fashion_underwear_beach`, `fashion_childrens_clothes`, each 2 products).
Safeguard 4 (the LIMIT-boundary check) has never fired — re-run over all 13
questions it applies to, zero ties, zero near-misses — because it asks a
different question (does the ranking metric tie at the output cut) than
`oh_wf01`'s actual defect (does the metric tie *within a partition*, before
the cut). The new safeguard 5 asks the right question and is demonstrated to
refuse, not just pass: `python3 scripts/gen_olist_hard_fixture.py --self-test`
(10/10) confirms it detects all 4 tied categories, does not refuse `oh_wf01`
because the question states its tie-break, and *would* refuse if that
statement were removed. `oh_wf03`/`oh_wf04` are re-verified the same way,
automatically, at zero partition-internal ties. **No score changes**: the 5
categories in `oh_wf01`'s shipped output were never among the tied ones under
either the wrong count or the correct one.

One more item recorded, judged NOT a
defect: `oh_j04` (distinct sellers per `payment_type`) attributes an order's
sellers to every payment type it used when an order splits across types
(2,246 of 99,441 orders do) — the question's wording ("involved in payments
of that type") does not claim exclusivity, so this is the natural reading,
documented for consistency with how `oh_wf04`'s multi-payment-row fan-out was
also called out rather than left implicit.

**CORRECTED cycle 002 (audit 002 F-002-6).** `V2_STRATEGY.md` §4 B2 specified
this fixture with *"deliberately ambiguous phrasings."* It was not built that
way — safeguard 2 (every ambiguous grain or denominator stated in the
question text) was applied aggressively instead, and the fixture's own notes
read as a record of ambiguity being designed out (`oh_j03`, `oh_j07`,
`oh_j09`, `oh_wf01`, `oh_wf04` all document a specific ambiguity the question
text was written to remove). **`100.0%` on the hard fixture is therefore
established for unambiguously-worded hard questions, not for questions worded
the way a user actually writes them** — the opposite of what B2 asked for.
This is a gap in what was built against the spec, not an error in the
measurement: the safeguards that removed the ambiguity are the correct
response to `phase_c2`'s 48% defect rate, and no fixture here has yet
measured accuracy against genuinely ambiguous real-world phrasing.

**Replication table (`V2_STRATEGY.md` §6):**

| fixture | authored | n | defects found | defect rate |
|---|---|---|---|---|
| `phase_c2` (synthetic) | pre-audit | 25 | 12 | 48.0% |
| `olist` (real, easy) | 2026-08-17 | 18 | 0 | 0.0% |
| `olist_hard` (real, hard) | 2026-08-20 | 16 | 1 | 6.25% |

**Does not establish:** a fixture defect rate of 6.25% on 16 questions is one
finding at small n, not a stable rate — the honest reading is "closer to
olist.json's 0% than to phase_c2's 48%," which is itself informative (the
safeguards this project built after the phase_c2 audit continue to work), not
"defects now occur at 6.25% generally." The 38.9pp local-model drop is a
single paired measurement on one model version (`qwen2.5:7b`) and one
provider's mixed-role Gemini configuration; it is not yet re-measured on
Anthropic or Azure, and BD6's 2-hop-join weakness (the reason this workstream
exists) was measured on different fixtures entirely, so this is corroborating
evidence for that finding, not a replication of it in the strict sense.

---

*Report generated as part of Phase F (`docs/phase_f_log.md`). Do not hand-edit
the Local (provisional) column after a graded run — append a Graded value
instead, per `docs/rebaseline_runbook.md` §5.*
