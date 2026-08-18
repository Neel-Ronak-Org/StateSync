# Project direction — honest state and what to do next

---

# ⚑ SCOPE REVISION — 2026-08-17, after the first hosted-model probe

**This section supersedes the priorities below it.** Everything under it stands
as the record of how the project got here.

## 1. What the probe established, and the reframe it forces

Paired on the same 15 queries, a hosted model beat the local 7B by **+20pp on
both scorers** (standard EX 40.0% → 60.0%). So the 7B was a real part of the
ceiling. That was the expected finding.

**The unexpected finding is what the residual failures turned out to be.** All
six queries that still failed were opened and checked against the database:

| Query | What it actually is |
|---|---|
| `c2g_04` | **Ground truth is WRONG.** "Which cities have both a seller and a logistics hub located in them" — the model's `INTERSECT` matches by CITY (8 rows); the ground truth requires both at the identical `location_id` (5 rows). The model was marked wrong for being right. |
| `c2g_08` | **Question is ambiguous.** 550 fulfillments, 323 delivered. "What percentage of fulfillments were delivered after their promised time" — ground truth divides by 323, the model by 550. Both defensible; the question does not say. |
| `c2g_12` | **Projection.** "Which fulfillments required more than one delivery attempt" names no columns. Model returned the id; reference returns 3 columns. Same 362 rows. |
| `c2g_13` | **Projection.** Model returned 5 columns, reference 3. Same 5 rows. |
| `c2g_14` | **Projection.** Model grouped by `buyer_app_id`; reference joins for the legal name. Same 5 rows. |
| `c2g_15` | **Genuine model error** — `~ '\p{Devanagari}'` is not valid in PostgreSQL (`[\u0900-\u097F]` is). Note the reference is also weak: `display_name_local IS NOT NULL` equals "has Devanagari" only on this data. |

**Five of six are instrument defects. One is a model error.**

On a sound fixture the same run lands somewhere between the measured **60%**
and **~93%** (14/15, if every defect resolved in the system's favour). The
truth is in between and is not yet known — but the gap is larger than the
+20pp the model change bought.

**The reframe: `phase_c2` has been measuring fixture quality as much as system
capability, and nobody could see it while a 7B was failing for its own
reasons.** A weak model masks a weak fixture. The M4 story has now moved twice —
first from "28% capability" to "a strict scorer" (`PROJECT_DIRECTION` §2), now
from "a strict scorer" to "an unsound fixture". Each move was found by opening
the evidence rather than re-reading the number.

## 1a. RESULTS — 2026-08-18, after real data landed

| | |
|---|---|
| **1** — audit every ground truth | ✅ done. 12 of 25 defective. Corrected fixture worth **+16pp** on the same model. |
| **2** — specify projections | ✅ done, folded into `phase_c2_v2`. |
| **3** — real data | ✅ **done.** Olist, 1.45M rows, loaded, agent-queryable, fixture built, both providers measured. |
| **4** — Hinglish `x`/`x_local` | ❌ still open. Live wrong-answer bug. |
| **5** — auth + CI stack tier | ⚠️ CI deterministic tier shipped; auth not started. |
| **6** — M2/D-V6/C2-V1 | ⏸ model-gated, still waiting. |
| **7** — schema retrieval | ⏸ trigger measured at 449 tokens vs the 8,000 threshold. Correctly unbuilt. |

**The headline finding, and it reframes the spending plan.** Paired on the same
18 real-data questions, local `qwen2.5:7b` scored **88.9%** and Gemini
**94.4%** — a one-query difference at n=18. The same comparison on synthetic
`phase_c2` showed **+20pp**. The model gap did not shrink because the models
changed; it shrank because the *questions* did. Most of what looked like a
capability gap was a fixture punishing under-specification, and a stronger
model was partly buying its way out of ambiguity.

Ranked by measured effect on this project's numbers:

| intervention | effect |
|---|---|
| fixing the fixture's ground truths | **+16.0pp** |
| 7B → hosted model (synthetic, under-specified questions) | +20.0pp |
| 7B → hosted model (real data, well-specified questions) | **+5.5pp = one query** |
| 7B → 14B general, local | +0.0pp |
| 7B → 14B coder, local | −8.0pp |

**So: instrument quality is worth as much as the frontier model, and a bigger
local model is worth nothing.** Spend credits on hard multi-hop joins and
genuinely ambiguous questions — not on ordinary aggregates over real data,
where a free local model is equivalent.

## 2. Model-independent priorities, ranked

**1 — Audit every ground truth in the reported fixtures. Highest value.**
Three defects in six inspected queries, and only 15 of 25 have been examined at
all. Until this is done, no accuracy number from `phase_c2` means what it says,
on any model, and paying for frontier credits buys precision on a bent ruler.

**Protocol, non-negotiable:** do NOT edit `fixtures/golden/phase_c2.json`.
Build `phase_c2_v2.json` alongside it with corrected ground truths and
projection-specified questions, run both, and report both numbers with the
delta. That is the `execution_accuracy` precedent — add a correct instrument
beside the flawed one and show the difference — not a quiet edit that raises a
score. Every correction needs its evidence recorded (the SQL, the row counts,
why the old one was wrong).

**2 — Specify projections in questions, or accept that EX is unmeasurable here.**
Three of six residual failures are the question not saying which columns to
return. Both scorers compare column-bearing results, so an under-specified
question makes the test unanswerable rather than hard. This is the same defect
`da6e49f` chased with a prompt rule and *half*-fixed — the projection prompt
bought 2 queries on `phase_c2` and cost 4 on M3, because it was treating a
fixture problem as a model problem.

**3 — Load real data (Olist).** Unchanged in rank and still the biggest single
credibility lever. Kills "all data is synthetic", surfaces real schema
messiness. Zero model dependence. ~1 day.

**4 — The Hinglish / `x`/`x_local` defect.** Still a wrong-answer bug on the
input class the owner identifies as realistic. Partly model-sensitive — Gemini
detected `hinglish` where the 7B said `other` — but the root cause is
model-independent: nothing tells any model which of the column pair holds which
script.

**5 — Production-grade gaps.** No auth of any kind. CI covers only the
deterministic tier. Both cheap, both are claims a reviewer will test.

**6 — Known FAILs on the local model** (M2 consistency, D-V6 spatial, C2-V1
2-hop join). These ARE model-gated — BD6 says so — so they wait for the graded
run rather than leading it.

**7 — Schema-retrieval selection.** Trigger not fired and will not be by Olist
(9 tables) or TPC-DS (24). See `docs/schema_retrieval_decision.md`.

## 3. What this changes about the project's claims

- **Do not quote 44%, 60%, or any EX figure as capability** until the ground
  truths are audited. Quote it as "measured on `phase_c2`, whose ground truths
  contain at least one demonstrated error and several under-specified
  questions."
- **The fixture-authoring lesson generalises.** `limitations.md` already records
  "a fixture authored by the same reasoning as the prompt shares the prompt's
  blind spots" (M9's 60/60 while routing was broken). This is the same defect in
  the fixture's ANSWERS rather than its questions, and it went unseen for the
  same reason: nothing independent ever checked them.
- **Frontier credits are still worth getting** — but their value is now
  *after* the fixture audit, not before it.

## 4. Unchanged

The security, verification and seam work stands. Four providers behind one
seam, 40/40 adversarial, deployment parity, flag-state guards, CI. None of that
is affected by the fixture finding, and it remains the strongest part of the
project.

---


**Purpose.** Everything technical about this build is recorded in
`metrics-report.md`, `limitations.md`, `open_items.md` and the phase logs. What
was *not* written down anywhere is the **reasoning** — what this project
actually is, what its numbers mean, and which way it should go. A reader
starting cold gets the code and the metrics from those files and the decisions
from this one.

Written 2026-08-16. Supersedes nothing; nothing supersedes it silently.

**New agent?** Read `docs/AGENT_HANDOFF.md` first — it carries the history,
what is already settled, and the owner's own position. This file is step 2.

---

## 1. What this project actually is

A stateful multi-agent analyst over ONDC-style logistics data: LangGraph
orchestrator, MCP tool server as sole credential holder, Postgres + PostGIS +
pgvector, Qdrant trajectory memory, an evaluator service, and a browser UI.
Seven services, `docker compose up`.

**Be precise about the category.** Architecturally this is a well-executed
instance of a common pattern (agent + text-to-SQL + RAG). The *pattern* is not
novel. What is above the norm:

- **Security**: three independent SELECT-only enforcement layers, with the
  Postgres role grant as the actual guarantee rather than prompt instructions;
  network isolation; 36/36 adversarial suite; a real cross-tenant read found
  and closed in `vector.hybrid_select`.
- **Verification**: 52 verification steps, every number traceable to a run,
  regressions reported rather than smoothed.
- **Seams**: swapping the model, the normalizer, the schema retriever or the
  scorer is a config change, not a refactor.

**What it is NOT:** production-ready, tested on real data, or benchmarked
against anything standard. All data is synthetic. All fixtures are
self-authored.

---

## 2. The single most important finding about our own numbers

**M4 was never measuring what everyone assumed it measured.**

M4 has been reported as **28% query correctness**, and that number drove a
great deal of pessimism about the project. `scripts/diagnose_m4.py` captured
the generated SQL, the generated rows, the ground-truth SQL and the
ground-truth rows — evidence the previous harness discarded — and scored the
same 25 queries three ways:

| Definition | Result |
|---|---|
| **strict EX** (this project's C2-V8 signature: `{n_columns, row multiset}`) | **7/25 = 28.0%** |
| **standard EX** (BIRD/Spider: result sets as value tuples) | **10/25 = 40.0%** |
| **mean value recall** (are the ground truth's values present at all) | **76.8%** |

Failure breakdown of the 18 "failures":

| Category | n | Nature |
|---|---|---|
| right values, wider/narrower projection | 6 | **fixable — prompt discipline** |
| right row COUNT, wrong values | 5 | genuine model error |
| correct under standard EX (strict scorer counts column width) | 3 | **pure measurement artifact** |
| right values, WRONG row count (missing aggregation/filter) | 1 | genuine logic error |
| misrouted | 1 | routing |
| unrecovered SQL error | 1 | genuine |
| wrong shape and values | 1 | genuine |

**Ten of eighteen "failures" were not capability failures.** Three were the
scorer alone. Six returned the correct values with extra columns.

### Updated 2026-08-16: the current build, on the corrected diagnostic

The table above is the FIRST run, kept because it is what the argument was
built on. Two things have changed since: the projection-discipline prompt
(`da6e49f`) and three defects fixed in the diagnostic itself (`f46e0bf`).
Clean serial re-run under the shipped flag state, `docs/m4_diagnosis_v2_corrected.json`:

| Definition | First run | **Current** |
|---|---|---|
| strict EX (C2-V8 signature) | 7/25 = 28.0% | **9/25 = 36.0%** |
| standard EX — shipped `ExecutionAccuracyScorer` | — | **11/25 = 44.0%** |
| standard EX — legacy in-script definition | 10/25 = 40.0% | 11/25 = 44.0% |
| mean value recall | 76.8% | 74.0% |
| mean row recall (new) | — | 48.0% |

**The BIRD-definition correction does not move the number here.** `f46e0bf`
found that the published "standard EX" came from an in-script definition that
sorted values *within* each row — so a generated row (5, 3) matched a
ground-truth (3, 5) — and not from the registered scorer. On this fixture the
two agree exactly, 11/25 both ways. That was established twice, independently:
by re-executing the stored SQL through MCP with no model calls, and then by
this full run. The definitions genuinely differ (`tests/test_scorer_semantics.py`
pins a case where they disagree); `phase_c2` just contains no query that
discriminates them. **The published 44% survives the correction** — worth
stating plainly, because the correction was expected to lower it.

**The honest failure count went UP, and that is the point.** Fixing the
"SQL execution error (unrecovered)" mislabel — it fired on
`bool(state_trace["errors"])`, which stays non-empty after the correction loop
*succeeds* — emptied that bucket and moved both queries into genuine-error
categories:

| Category | First run | Current |
|---|---|---|
| correct | 7 | **9** |
| right row COUNT, wrong values | 5 | **7** |
| right values, wider/narrower projection | 6 | **4** |
| correct under standard EX only (scorer artifact) | 3 | 2 |
| right values, WRONG row count (missing aggregation) | 1 | 1 |
| misrouted | 1 | 1 |
| wrong shape and values | 1 | 1 |
| **unrecovered SQL error** | **1** | **0 — the category never once fired correctly** |

So the "ten of eighteen failures were not capability failures" framing does not
survive intact on the current build: of 16 remaining failures, **9 are genuine
wrong-value or wrong-shape model errors**, 4 are projection width, 2 are the
strict scorer alone, 1 is routing. The measurement artifact was real and is now
smaller than the model error.

**Why this happened, and it is the project's real weakness:** the system is
rigorously *verified* and poorly *diagnosable*. `c2v8_report.json` stored
`match: false` and a row count, discarding the SQL and the rows. This is the
third instance of the same defect class in this build — the Synthesizer logged
*that* a parse failed without *what* failed; the evaluator recorded errors as
empty strings. **Verification tells you something broke. Diagnosis tells you
what to fix. This project built the first and skipped the second.**

Fix the instrumentation before trusting any pessimistic number.

---

## 3. Scorer semantics — read before quoting any accuracy figure

Three registered scorers, deliberately kept separate because they answer
different questions. Collapsing them hides exactly the gap described above.

| Name | Compares | Use for |
|---|---|---|
| `execution` | `(column_NAME, value)` pairs — alias-sensitive | strictest internal check |
| C2-V8 signature | ignores names, **includes column count** | historical M4 |
| `execution_accuracy` | result sets as value tuples — **the published definition** | **anything compared to BIRD/Spider** |

Quoting an accuracy number without naming the scorer is meaningless. Prior
reporting did this and it cost the project months of misplaced pessimism.

---

## 4. Resources actually available

| Resource | Capacity (at ~4,811 in + 296 out tokens/query, measured) |
|---|---|
| **Azure $100 student credits** | ~6,600 GPT-4o-class queries, or ~110,000 mini-class. **The real asset.** |
| **Gemini free tier** | rate-limited, not cost-limited — carries bulk benchmark runs |
| **Anthropic $5** | ~265 Sonnet queries. A comparison point, not a benchmark budget |
| local `qwen2.5:7b` | free, and the small-model floor |

**The project is not compute-constrained.** It was experimental-design
constrained, and partly instrument-constrained.

---

## 5. The two paths

Both need the same next three weeks, so the choice does not have to be made
yet.

**Production** — an actually deployable analytics service.
Differentiator is *not* "natural language analytics" (crowded: Databricks
Genie, Snowflake Cortex Analyst, ThoughtSpot). It is **self-hosted, no data
egress, with verifiable read-only guarantees** — viable for organisations that
legally cannot send warehouse data to a US API.
Blockers: answer quality, real data, auth/multi-user/audit, and schema-scale
retrieval.

**Research** — a focused empirical paper.

> **CORRECTED 2026-08-16.** This section previously read: "schema documentation
> **improved English text-to-SQL** and destroyed multilingual entity
> preservation". The first half is **false against this project's own
> measurement**, which was already in the repo when the claim was written.
> `docs/phase_d_log.md`'s table for the same experiment records:
>
> | | before 005 | after 005 |
> |---|---|---|
> | D-V1 control (does **not** ship) | 40.0% | **50.0%** |
> | D-V1 treatment (**ships**) | 65.0% | **55.0%** |
> | D-V2 entity preservation | 81.25% | **62.5%** |
> | English reference line | 60.0% | **55.0%** |
> | M4 cold | 28.0% | 28.0% |
> | M4 warm | 32.0% | **24.0%** |
>
> The English reference line went **down**, 12/20 → 11/20. The only arm that
> improved is D-V1 control — normalization *disabled* — which is not the
> shipped path. The "English gain" was a three-query spot-check
> (`city = 'Bengaluru'` replacing `city_local = 'Bengaluru'`), and the
> spot-check impression was carried forward as if it were the measured line.
> `phase_d_log.md`'s own prose makes the same slip two lines under its own
> table ("bought a small English gain"); a correction note is filed there.
>
> **This matters for a live decision.** §7a and `AGENT_HANDOFF.md` §5 argue
> the migrations 005/006 revert "rests on a premise that may be wrong" —
> namely D-V2's Devanagari-verbatim requirement — and propose re-scoring D-V2
> under a Latin-only assumption to possibly vindicate the change. **That
> cannot vindicate it.** D-V2 is only one of five metrics that moved, and it is
> the only one the Devanagari critique touches. The shipped treatment arm fell
> 10 points, the English reference line fell, and M4 warm fell 8 points — none
> of which involve D-V2's scoring rule at all. **The revert stands on grounds
> entirely independent of the D-V2 design flaw.** The design flaw is real and
> worth fixing; it is not load-bearing here.

What survives as a candidate result is narrower and still genuine: **schema
documentation traded multilingual entity preservation for nothing measurable
elsewhere** — 81.25% → 62.5%, mechanistically explained ("an English name will
never match `city_local`" made the model transliterate दिल्ली → 'Delhi'),
measured, and reverted (`migrations/006`). "Documentation that helps in one
language silently destroys entity fidelity in another" is the publishable
shape. "It improved English" is not available.

Blocker: **statistical power.** D-V1 is 40 queries; the English reference line
is 20, so one query is 5 percentage points — and the English movement being
corrected here is a *single query*, well inside noise. Publication needs ~200+
per condition across multiple schemas.

---

## 6. What breaks first at real scale

Row scale is easy; **schema scale is the problem.**

> **✎ CORRECTED 2026-08-16 — the first row of this table is wrong, and it was
> load-bearing.** "Schema will not fit in context" was never measured. Measured
> now against the live MCP: the 12-table schema formats to **3,733 chars ≈ 933
> tokens**, i.e. 49.8 chars per column. At that density **200 tables / 2,000
> columns is ~24,900 tokens** — which fits comfortably in every current model's
> context window, and even in the local 7B's 32k. The claim is off by roughly an
> order of magnitude.
>
> What actually breaks first, in order: **accuracy** (the model picks the wrong
> tables out of 200 — and table selection is already this project's measured
> weak point, per BD6's 2-hop join weakness at *twelve* tables), then **200
> sequential `describe_table` round-trips per query** (`sql_agent.py` passes
> every table, unnarrowed), then **cost**, then — only past ~500 tables —
> context.
>
> That reordering changes the fix: a context problem needs compression or
> hierarchy; an accuracy-and-cost problem needs **selection**. Full reasoning,
> the pre-committed build trigger, and a per-seam verdict:
> **`docs/schema_retrieval_decision.md`**.

| Failure | Trigger | Severity |
|---|---|---|
| schema will not fit in context | `describe_table` over 200 tables / 2000 columns | ~~**fatal — the real problem**~~ — **corrected: ~25k tokens, fits. See the box above.** |
| model picks wrong tables from an over-supplied schema | 200 tables in one prompt, no candidate selection | **severe — the real problem** |
| join-path explosion | many tables, more wrong joins | severe |
| 15s statement timeout | 50M-row tables | easy fix |
| cost/latency per query | 5.2 LLM calls × large context | moderate |

**Note a reversal:** CSR-RAG-style hierarchical schema retrieval was correctly
dismissed as unnecessary for a 12-table schema. That reasoning **inverts** at
real scale — it becomes the thing you need, not a citation.

---

## 7. Ordered next steps

1. ✅ **Standard `execution_accuracy` scorer** — done. Without it, a correct
   BIRD run still produces incomparable numbers.
2. ✅ **`diagnose_m4.py`** — done. Failure categories, not just pass/fail.
3. ✅ **Projection discipline** — done, and the prediction on this line was
   **wrong**, which is why it is left visible. It predicted "if it converts
   them, standard EX ≈ 64%". Measured: **44.0%** (11/25, from 40.0%). It
   converted 2 of the 6 targets, regressed 1 previously-correct query
   (under-selection, the mirror failure), and dropped mean value recall
   76.8% → 74.0%. The prompt-interventions-redistribute-errors pattern in §2
   is the generalisation of that miss.
4. **Gemini + Azure providers** behind the BD1 seam. Model-independent.
5. **Load Olist** (real Brazilian e-commerce data, ~100k orders, 9 tables) —
   real messiness, right domain, surfaces schema retrieval immediately.
6. **Spider dev**, then **BIRD dev**, on Gemini free tier; Anthropic $5 as a
   cross-provider check.
7. **TPC-DS** at 1GB then 10GB if the production path is chosen — industry
   standard, 99 official queries, arbitrary scale.

---

## 7a. A design flaw in D-V2, found 2026-08-16 — read before trusting M3

**D-V2 may penalise correct behaviour on realistic data.**

D-V2 asserts that a query's proper noun appears **verbatim in the generated
SQL** (`scripts/bench_multilingual.py`: `in_map` and `in_sql`). For a Hindi
question naming दिल्ली it requires `दिल्ली` in the SQL. That is only correct
because this project's synthetic schema carries `locations.city_local` storing
Devanagari.

**Real Indian enterprise data is overwhelmingly Latin-script.** Customer names,
addresses, product titles and ONDC's own protocol fields are ASCII. In a
realistic database with only `locations.city`, the correct SQL for a question
naming दिल्ली is `city = 'Delhi'` — and **D-V2 would score that correct answer
as a failure.** The metric encodes a data-model assumption that usually does
not hold.

**Consequence for a decision already taken:** the schema-comment experiment
(migrations 005/006) was reverted largely because D-V2 fell 81.25% → 62.5%,
driven by the model transliterating दिल्ली → 'Delhi'. On Latin-script data that
is the *right* answer. **That revert rests on a premise that may be wrong**, and
the pre-committed acceptance condition in `open_items.md` inherits the same
flaw. Re-score before writing the idea off.

**The more valuable multilingual problem is code-mixed input over Latin-script
data**, not Devanagari over Devanagari. Hinglish/code-mixed text-to-SQL is
*less* studied than Indic-script text-to-SQL, more realistic for enterprise
analytics, and harder in an interesting way (no clean language boundary for the
normalizer to detect). Devanagari support stays — it is real for government/PSU
contexts and is what IndicDB studies — but as a supported path, not as the
headline metric.

### ✎ Corrected 2026-08-16 by the fresh-context audit — read this before acting on §7a

Two things above were wrong, and one of them was steering the next move.

**1. The Hinglish fixture already exists.** The table this section originally
carried claimed "what is currently tested: grammatical Devanagari". False:
`fixtures/golden/phase_d_multilingual.json` is **20 `hi` + 20 `hinglish`**, and
has been since Phase D — `"Seller Store 9 ne kitne orders receive kiye hain?"`,
`"Pichhle 30 dino mein kitne orders fail ya return hue hain?"`. Latin-script
entities, code-mixed grammar, informal framing. What was missing was not the
fixture; it was **disaggregation** — M3 has always been reported as one number
averaging the two halves. (The spelling-variance point —
`Bangalore`/`Bengaluru`/`Banglore` — *is* genuinely untested and stands.)

**2. Split, the Latin half is the WEAK half** (re-derived from the shipped-config
run, no re-run):

| | Devanagari (n=20) | Hinglish (n=20) |
|---|---|---|
| D-V1 treatment | **75.0%** | **55.0%** |
| D-V2 entity preservation (n=16 each) | **93.8%** | **68.8%** |

So the premise of §7a inverts. D-V2 is not mainly over-protecting Devanagari;
the Latin-script half is 20pp worse and was never reported separately. Confirmed
live: `"Lucknow mein kitne sellers located hain?"` makes the model invent a
Devanagari string (`city_local = 'उख्लौंग'`) for a Latin place name, return 0
rows, and answer "Lucknow mein 0 sellers located hain." The true answer is 4.

**3. The D-V2 concern is real but does NOT overturn the migrations 005/006
revert.** Re-scored under a Latin-only data assumption (script requirement
relaxed, both halves of D-V2's own test kept): shipped **81.2%**, migration-005
**75.0%** — 005 still misses the pre-committed ≥81.25% bar. And D-V1 treatment
fell 65.0% → 55.0% under 005, a measure with no script assumption in it at all.
**The revert stands.** Full working: `docs/phase_d_log.md` § "Audit findings".

---

## 8. What NOT to do

- **Do not add features.** Beckn ingestion, HEAR node, richer spatial — zero
  value for either path now. **CSR-RAG is NOT in this list**: hierarchical
  schema retrieval is unnecessary at 12 tables but becomes the first thing that
  breaks on a real schema (§6). Do not build it speculatively; do expect to
  need it at step 5. See `docs/AGENT_HANDOFF.md` §5a.
- **Do not chase SOTA on BIRD.** You will lose to teams with 1000× the
  compute. Report the number for context, not competition.
- **Do not spend API credits before step 3.** Paying a better model to fix a
  prompt problem is waste.
- **Do not quote an accuracy number without naming its scorer.**
- **Do not tune against the fixture you report from.** `f_hybrid_004` was left
  failing for exactly this reason; the schema-comment retry has a pre-committed
  acceptance condition in `open_items.md` for the same reason.
