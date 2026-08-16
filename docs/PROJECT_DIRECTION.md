# Project direction — honest state and what to do next

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

| Failure | Trigger | Severity |
|---|---|---|
| schema will not fit in context | `describe_table` over 200 tables / 2000 columns | **fatal — the real problem** |
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
