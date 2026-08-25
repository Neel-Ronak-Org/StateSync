# StateSync v2 — strategy

**Status: current. Written 2026-08-18, after v1 was declared complete.**

This supersedes `docs/PROJECT_DIRECTION.md`, which is retained as the **v1
record** and should not be edited further. That file is now three layers of
corrections stacked on an original — accurate history, unusable as direction.
This one is written once, flat, and revised in place.

No dates anywhere. Every workstream has a **completion condition** and a
**cut-line** instead, because "we could also add X" without "X is done when Y"
is how v1 ended with eight seams and six empty ones.

---

## 1. What v2 is

> **A conversational analytics agent over real e-commerce data, with verifiable
> read-only guarantees.**

Two words are new. **Conversational** — v1 answers one question and forgets it;
every real analytics tool is a dialogue. **Real** — v1 was 100% synthetic until
its final day.

The third clause is not new and is the reason the project is worth continuing:
the security and verification properties are genuinely above the norm, and they
survived contact with a corpus they were never designed for.

## 2. What v1 established — do not re-derive these

| | |
|---|---|
| single-turn EX, real data (Olist, 18 q, **schema-guided — see ⚠⚠ below**) | **local 7B 88.9% · hosted 94.4%** ⚠⚠ |
| single-turn EX, synthetic corrected fixture (25 q) | 60.0% (54.5% over the 22 references that can fail — 3 are unfailable empty-vs-empty matches, audit 004 F-004-3; 52.4% over the 21 that can also discriminate — a 4th, `c2g_15`, has a filter that is a no-op on this data, audit 005 F-005-2 — `AGENT_HANDOFF.md` §6) |
| single-turn EX, synthetic original fixture | 44.0% |
| adversarial suite | 40/40 |
| corpora | synthetic 550 orders · **Olist 1,451,414 real rows** |
| providers behind one seam | local · anthropic · azure · gemini |

**⚠⚠ 88.9%/94.4% is NOT "88.9% at text-to-SQL on real data," and the word
"well-specified" that used to sit in that table row was carrying far too much
weight.** `scripts/gen_olist_fixture.py` states the design as a goal: *"EVERY
question names its expected columns"* and *"EVERY ambiguous denominator is
stated IN the question."* Over the 18 questions: **12 name a real schema column
verbatim**, the remaining 6 state the filter in prose, and **16 of 18 are
single-table aggregates** — two involve a join at all. A representative
question reads *"Considering ONLY orders that have a delivery date recorded,
how many were delivered LATER than their estimated delivery date? Return a
single count"* — a WHERE clause and an output shape dictated in English.

Those safeguards exist for a **legitimate** reason — the scorer punishes
projection mismatch, so under-specified questions manufacture false failures
(the `phase_c2` 16pp instrument defect, designed out) — but the consequence is
that this figure measures schema-guided SQL **dictation**, with schema linking
and ambiguity resolution removed by construction. **The tell:** a free local 7B
at 88.9% would beat published BIRD dev-set state of the art (~70–75%, for far
larger and heavily engineered systems). It does not — the task is different,
not the model better. **The honest capability signal is the hard fixture: the
same 7B falls to 50.0%, −38.9pp, the moment multi-table joins are required.**

This is the same defect class audit 002 F-002-6 caught for the *hard* fixture
(ambiguity eliminated by construction, `100.0%` published without saying so);
it was simply never applied to the easy one. `docs/metrics-report.md` § "What
it does establish" has always stated this correctly and in full — the failure
was **propagation**, not measurement. Logged 2026-08-23 for cycle 006's audit.

**⚠ Separately: 88.9%/94.4% was measured with `normalization.enabled = false`,
against a shipped default of `true`** — both arms, so the paired comparison
itself is unaffected, but neither number is the shipped configuration. Full
caveat and evidence: `docs/metrics-report.md` "Real-data results", added by
audit 001 F-001-2.

**⚠ Separately again: 5 of the 18 easy-fixture questions (plus 2 of the 16
hard-fixture questions) state a restriction the data makes invisible** —
verified by executing the ground truth with the restriction removed and
getting an identical result set every time (audit 006 F-006-4). Three passes
are demonstrably credited to a model that dropped the stated restriction
entirely. Does not move 88.9%/94.4%/50.0%/100.0% — every generated query
still answers its question correctly; this is a fixture-instrument caveat,
same class as `c2g_15` (audit 005 F-005-2), not a wrong pass. Separately, 2
of the 16 hard-fixture questions are answerable by fewer tables/a looser
definition than their own `hard_classes` label implies (audit 006 F-006-5).
Full write-up: `docs/metrics-report.md`, `docs/open_items.md`.

**Ranked by measured effect — this table is the reason v2 is shaped the way it
is:**

| intervention | effect |
|---|---|
| fixing the fixture's ground truths | **+16.0pp** |
| 7B → hosted, under-specified questions | +20.0pp |
| 7B → hosted, **well-specified** questions | **+5.5pp — one query** |
| 7B → 14B general (local) | +0.0pp |
| 7B → 14B coder (local) | −8.0pp |

Read down that column: **instrument quality is worth as much as a frontier
model, and a bigger local model is worth nothing.** Twelve of twenty-five
questions in a fixture this project authored were defective. That finding is
the spine of the paper in §6 and the reason §3's fixture work is not optional.

## 3. Workstream A — Multi-turn conversation (the capability leap)

v1 is single-turn by construction. Real usage is
*"how many orders?"* → *"and by state?"* → *"just the delivered ones."*

**Build:**
- session-scoped conversation state (LangGraph state and `MCPSession` already
  carry a session id; the history does not persist across turns)
- context carryover — prior question, prior SQL, prior result shape
- **reference resolution** — anaphora (*"those"*, *"it"*), ellipsis (*"and by
  state?"*), and refinement (*"just the delivered ones"*)
- **clarification** — when a follow-up is genuinely unresolvable, ask rather
  than guess. A wrong guess on turn 2 poisons every later turn.

**Test with established vocabulary, not invented metrics.** Multi-turn
text-to-SQL has prior art (SParC, CoSQL) and their metrics are the right ones:

| metric | definition | why |
|---|---|---|
| **QM** (question match) | % of individual turns correct | comparable to single-turn EX |
| **IM** (interaction match) | % of conversations where **every** turn is correct | the honest headline; compounds errors |
| **reference resolution rate** | of turns containing an anaphor/ellipsis, % resolved to the right entity | isolates the new capability |
| **error propagation rate** | given a wrong turn *n*, how often turn *n+1* is wrong **because of it** | diagnosis, not just verification |

**QM is never reported without IM.** Quoting the flattering half of a pair is
precisely the failure v1 spent three days undoing.

**Completion condition.** The system sustains ≥3-turn conversations over the
Olist corpus with reference resolution measured; QM, IM, reference-resolution
rate and error-propagation rate all reported from one run, with per-role model
provenance.

**Cut-line.** If clarification proves expensive, ship without it and record the
guess-rate instead — but never ship silent guessing unrecorded.

## 3b. Workstream A — design decisions (added 2026-08-18)

### Multi-turn is NOT a new route

Routes decide **which engine answers this question**. Multi-turn is orthogonal
state that *every* route needs. A `conversation` route would leave the `sql`
route with no memory — a category error.

**The design: contextual query rewriting, placed BEFORE the supervisor.** A
follow-up is rewritten into a standalone question — *"and by state?"* → *"How
many orders by customer state?"* — after which **the entire existing pipeline
runs unchanged**. Supervisor routes it, the specialist answers it, nothing
downstream is touched. Largest coverage for the smallest change, and
`/query` already accepts `session_id` and threads it to `MCPSession`, so half
the plumbing exists.

**Its failure mode is this project's signature one, so it gets its own metric.**
A wrong rewrite is *invisible*: it yields a well-formed question that routes
correctly, executes correctly, and answers something nobody asked. Silent wrong
answer. **Rewrite fidelity** — did the standalone question preserve the user's
intent — is therefore measured separately and never folded into turn accuracy.
A run may not report QM/IM without it.

### Session state, history, and the audit log are ONE table

Conversation history is the **audit log** `limitations.md` records as missing.
One artifact, two claims: user-facing history, and the compliance record a
regulated buyer asks for first.

**Gated on auth.** "Whose history?" is unanswerable without identity, which
fixes the order: auth → history → everything else.

**It is also an exfiltration target**, and that is a design input rather than an
afterthought: stored questions describe the business, stored answers contain
real rows. Same tenant scoping as the data plane, and a breach costs more once
this exists.

### OPEN QUESTION — resume and staleness. Decide before history ships.

A conversation resumed days later sits on data that has moved.

- Show the stored answer → a stale number in a fresh-looking session. Silent
  wrong answer, in a new place.
- Re-run the query → the conversation contradicts what it said last time.

**There is no free option.** Whichever is chosen must be visible in the UI
(answers stamped with when they were computed, at minimum). Recorded as open
rather than guessed.

### DEFERRED — session-scoped document upload, with a specific reason

Genuinely useful, demos well, and it is **the largest security change available
in this project**. Not deferred out of general caution:

- **M8 Class 1 tests injection through data** — adversarial text seeded into
  columns the agent later retrieves. A user-uploaded document is that exact
  attack surface, except the attacker supplies it directly. Shipping upload
  hands over the seeding mechanism the adversarial suite currently owns.
- There is **no file ingress of any kind** today, and no write path. The
  strongest claim this project has is three SELECT-only layers with write
  capability deliberately absent.
- Needs isolation finer than tenant scoping, plus a deletion policy or storage
  grows without bound.

**Comes after auth, with its own threat model and its own M8 class.** Not
before, and never casually.

### PDF export — solves a DIFFERENT problem than the one stated

The stated motivation was *"why would you want it lost at every refresh."* That
is a **persistence** problem and **history storage solves it completely**; PDF
does nothing for refresh-loss. PDF solves *getting the analysis to someone who
does not have the tool* — sharing and archiving. Both are real; they are not the
same need, and this is the lower-value half.

Pure polish for the research and security stories; genuinely valuable for the
demo and employability ones. Cheaper than it looks — `vl-convert` already
renders Vega specs to PNG for the vision scorer, so chart-to-image is solved and
only assembly remains. **Last in the sequence, and first to be cut.**

### Sequence

**auth → history/audit → multi-turn rewriting → cross-modal turns → session
upload → PDF.** Auth first because history is meaningless without identity;
multi-turn before upload because upload is the security-expensive one and
multi-turn is the capability that matters; PDF last because it is the only item
that is purely polish.

## 3a. Workstream A2 — The unstructured half (CORRECTION, added 2026-08-18)

**This section exists because the first draft of this document omitted it, and
the owner caught it.** §3 described multi-turn as if the system were SQL-only.
It is not, and the omission mattered.

**What actually exists** — it is not forgotten, and "no tests" is wrong:
`orchestrator/nodes/unstructured_knowledge.py`, the `docs` and `hybrid` routes,
`scripts/ingest.py`, and M6 in `scripts/bench_grounding.py`, which DOES include
a hallucination check — faithfulness scored by an LLM judge against a rubric
frozen in `fixtures/rubrics/faithfulness.json`, plus context precision, recall,
and a count of ungrounded-but-true answers.

**What is genuinely wrong with it, and it is worse than "forgotten":**

| | |
|---|---|
| corpus | **18 documents, 36 chunks** — while the SQL side now has 1,451,414 real rows |
| context precision | **0.622** — ~38% of retrieved context is irrelevant |
| faithfulness | 0.8125 |
| provenance | measured **pre-`da6e49f`**, never re-measured; flagged stale in `metrics-report.md` |

The asymmetry is the finding: v2's predecessor spent three days making the
structured half real and did not touch the unstructured half once. A
"conversational analytics agent" with a 36-chunk knowledge base is half a
product.

**The corpus problem is already solved and nobody noticed.** `olist.order_reviews`
holds **41,755 real free-text customer reviews** (Portuguese, messy, embedded
carriage returns) — 1,160× the current chunk count, already loaded, already
checksummed. Ingesting them makes the retrieval side real at zero acquisition
cost.

**Caveat that decides what it can be used for:** reviews average 70 characters
(max 208). That is **short-form text, not documents**. It is a real corpus for
retrieval-at-scale, grounding and faithfulness — *"what are customers saying
about late delivery"* is a genuine analyst question. It is **not** a substitute
for policy/SOP retrieval, where a threshold lives in a paragraph. Those stay
synthetic, and no real source has been found.

### Cross-modal multi-turn — the owner's point, and it sharpens §3

The interesting conversation is not SQL-only:

> **Turn 1** *"What does our SLA policy say about late deliveries?"* → `docs`
> **Turn 2** *"How many orders breached it last month?"* → `sql`, **with the
> threshold carried from turn 1's retrieved document**

That is **cross-modal context carryover**: a SQL turn whose correctness depends
on a value extracted from a document turn. It is strictly harder than the
SQL-only carryover §3 describes, it is what the `hybrid` route was built for
and has never been tested conversationally, and it is what an analyst actually
does.

It also creates an evaluation problem worth reporting: **how do you score a
turn whose ground truth depends on retrieval succeeding two turns earlier?**
Error propagation *across modalities* — a wrong threshold from turn 1 produces
perfectly-formed, confidently wrong SQL in turn 2. The error-propagation metric
in §3 must therefore attribute across routes, not just across turns.

**Completion condition.** Reviews ingested with the same discipline as the
relational load (row counts asserted, dirt surveyed). M6 re-measured on the
current build over the REAL corpus, reported next to the 36-chunk figure so the
change from toy to real is visible. At least one cross-modal conversation in
the multi-turn fixture, with its error propagation attributed by route.

**Cut-line.** If cross-modal carryover proves expensive, ship same-modality
multi-turn and report the cross-modal case as measured-and-failing — never
quietly omit it from the fixture.

## 4. Workstream B — Correctness

**B1. The `x` / `x_local` defect.** A live wrong-answer bug: asked
*"Lucknow mein kitne sellers hain?"* the system invents a Devanagari string,
matches `city_local`, returns 0, and answers confidently. Reproduced on three
independent models, so the cause is the schema carrying no signal about which
column holds which script — not any model's weakness.

*Completion:* the acceptance condition in `open_items.md`, which is the
authoritative version of this line — this copy went stale (audit 007
F-007-8) and is corrected to match rather than repeated: **D-V2 entity
preservation ≥ 81.25%** (unchanged, absolute); the **English reference line
must improve** over the paired pre-measurement; **D-V1 treatment must not
fall below** that same pre-measurement, taken on the build immediately
before the attempt, replacing the absolute `≥ 65.0%` this line used to state
(audit 001 F-001-3: `da6e49f` shipped and took D-V1 to 55.0%, turning "hold
65.0%" into an undisclosed +10.0pp demand against the build that actually
ships) — **plus** the Hinglish half no worse. Pre-committed so it cannot be
renegotiated after seeing results.

**B2. The hard fixture.** Every fixture here is mostly single-table aggregates.
BD6 records 2-hop joins as this system's measured weakness across four
independent checks, and the 94.4% headline has never been tested against them.
15–20 questions over Olist: three-table joins, correlated subqueries, window
functions, deliberately ambiguous phrasings — built with `gen_olist_fixture.py`'s
safeguards (projections named, denominators stated, zero-row references
rejected).

*Completion:* run on ≥2 providers, failures classified with
`attribute_failures.py`. **This tells us whether 94.4% is real or an artifact of
easy questions, and it must happen before any number is put in the report.**

**✎ MEASURED 2026-08-20 (cycle 001, Workstream B2).** `fixtures/golden/olist_hard.json`,
16 questions, safeguards from `gen_olist_hard_fixture.py`. Run paired on local
`qwen2.5:7b` and Gemini, failures classified. **The answer diverges by
provider: 88.9% does not survive harder questions (falls to 50.0%, −38.9pp);
94.4% does (100.0% on this sample, +5.5pp).** Full numbers, failure
classification (including a genuine `attribute_failures.py` false-positive
found and corrected by reading the actual SQL), and the fixture's own defect
audit (1/16, found before scoring): `docs/metrics-report.md` § "Hard-fixture
results".

**⚠ Two corrections, cycle 002 (audit 002 F-002-2, F-002-6), neither moves the
accuracy numbers above.** First: the automated LIMIT-boundary tie check (this
fixture's fourth safeguard) was credited above with finding `oh_wf01`'s
defect and fixing it pre-shipment — re-run over all 13 questions it applies
to, it has never fired; it checks a different tie than the one `oh_wf01`
actually had. A fifth safeguard added this cycle (a partition-internal tie
probe) is the one that catches it, and is demonstrated to actually refuse,
not just pass (`--self-test`, 10/10). Second: this section's own spec called
for *"deliberately ambiguous phrasings,"* and the fixture was not built that
way — its safeguards remove ambiguity by construction, so `100.0%` is
established for unambiguously-worded hard questions, not for questions worded
the way a user actually writes them. Full accounting:
`docs/metrics-report.md` § "Hard-fixture results".

**B3. Hinglish over real data.** *"Sao Paulo mein kitne sellers hain?"* — asking
code-mixed questions of the Olist corpus isolates the **language** capability
from the **data origin**, and lands it on real Latin-script city names with 611
spellings including `sao paulop`. That is the Bangalore/Bengaluru/Banglore
variance the v1 docs called realistic and untested.

**✎ MEASURED 2026-08-20 (cycle 002, Workstream B, audit 002 F-002-10).**
Every real-data number above was measured with **no join graph** in the SQL
agent's prompt — `olist` declared zero foreign keys where `public` declares
17. Paired before/after `migrations/008_olist_join_graph.sql` (six
zero-orphan edges declared; the one dirty edge, 13 orphans, deliberately left
undeclared — owner decision), local `qwen2.5:7b`, one variable, both arms
freshly measured (not read from the committed report):

| | easy (`olist.json`, n=18) | hard (`olist_hard.json`, n=16) |
|---|---|---|
| before 008 | 88.9% (16/18) | 50.0% (8/16) |
| after 008 | **77.8% (14/18), −11.1pp** | 50.0% (8/16), net 0 |

**Declaring the join graph did not help, and net hurt.** The easy fixture
regressed 11.1pp on two single-table/simple-join questions unrelated to any
newly-declared edge. The hard fixture's headline held at 50.0%, but the
underlying set shifted: `oh_j09` — previously correct — regressed into
*exactly* the schema-grounding failure Workstream B hypothesised the join
graph would fix (inventing a column instead of joining
`product_category_name_translation`), while an unrelated correlated-subquery
question improved. Neither of the two schema-grounding failures this
experiment targeted (`oh_j08`, `oh_j09`) was fixed — expected, since the one
edge they actually need (`products` → `product_category_name_translation`)
is the edge left deliberately undeclared; the six edges that *were* declared
cover different tables entirely. **A plausible confound under 50.0% is not
retired — a different one is exposed**: extra schema-context volume appears
to cost the 7B accuracy on questions unrelated to the added edges, the same
shape as `a4293e1`'s schema-comments regression. Third documented instance of
a schema/prompt-context intervention this project reasoned would help and
measured did not. Single-provider (Gemini not re-run — already 100.0% on the
hard fixture with no headroom to show improvement, per `docs/cycles/002.md`
B6). Full accounting: `docs/metrics-report.md` § "Real-data join graph".

## 5. Workstream C — Credibility

**C1. Production floor.** No authentication, no rate limiting on an
unauthenticated `/query` that costs tokens per call, no audit log of who asked
what. None of these differentiate — their **absence** disqualifies.
*Completion:* auth on the UI and API, per-identity audit log, rate limit;
adversarial suite re-run and still 40/40.

**C2. Indian corpus.** With a Kaggle account, order-level Indian e-commerce data
is reachable. **Caveat that decides how it is used:** these sets are typically
*flat, denormalized single tables* — weak for text-to-SQL, which needs joins.
Olist keeps the relational job; the Indian set carries the domain angle
(geography, ONDC vocabulary, DIGIPIN).
*Completion:* loaded with the same checksum + row-count discipline as Olist,
licence recorded, dirt surveyed before any question is written.

**C3. The three deliverables.** A repo a reviewer can navigate, a report that
carries the findings, a demo that shows a conversation. *Completion:* a README
finishable in five minutes containing architecture, one demo, the effect table
from §2, and the honest limitations.

## 6. The paper track — design discipline required FROM NOW

The aim is a **submission**, so the experimental design tightens immediately.
A paper kept "in the back of the mind" does not get written — not for lack of
discipline, but because the data was collected in non-comparable ways and by
the time you want to write, you cannot.

**The claim:** *fixture specification quality accounts for as much accuracy
variance in text-to-SQL evaluation as a frontier-model upgrade.*

Evidence held: 12/25 defective in one self-authored fixture; repair worth
+16.0pp; the model gap collapsing +20.0pp → +5.5pp once questions are
well-specified.

**What makes it publishable rather than an anecdote:** replication. v2 authors
**two new fixtures** (B2 hard, and A's multi-turn set). Auditing both with the
same protocol and reporting the defect rate and its accuracy cost turns one
observation into three independent measurements on independently authored
fixtures — including one on a *harder task*.

**Four rules, binding from now:**
1. **One variable per run.** The coder:14b conclusion was over-claimed because
   two things changed at once. Never again.
2. **Paired runs.** Same questions, same corpus, same scorer; vary the provider
   or the fixture, never both.
3. **Provenance in every artifact.** `flag_provenance` already records provider,
   per-role models and flag state. Do not add a report that lacks it.
4. **Never edit a fixture in place.** Corrected copies alongside, with an
   `_audit` trail and the original preserved — the `phase_c2_v2` protocol.

## 7. What "successful" means — the bar, per deliverable

**Repo.** CI green on every push. Every claim in the README traceable to a
committed artifact. A stranger can bring the stack up from the README alone —
the clean-machine condition v1 never actually performed.

**Report.** Three findings, each with the run behind it: the effect table from
§2; whether 94.4% survives hard questions; multi-turn QM **and** IM. Every
number names its fixture, its scorer and its model. No number appears without
its caveat attached at the point of use.

**Demo.** A ≥3-turn conversation over 1.45M real rows, ending in a chart, with
the trace visible. If a viewer cannot tell it is a conversation, the workstream
is not done.

**Paper.** Defect rates and their accuracy cost measured on **three
independently authored fixtures**, at least one multi-turn. Below that it is a
technical report, which is still worth writing — but it is not a submission and
should not be called one.

## 8. Explicitly cut — with reasons, so they are not silently reopened

| cut | why |
|---|---|
| **BIRD / Spider** | Three blockers measured (`benchmark_readiness.md`). The dialect one mostly dissolves — gold result sets can be computed offline in SQLite — so a subset run becomes cheap **once C1's per-request database routing exists**. Revisit then, as a line item, never as the project. |
| **TPC-DS** | Proposed for licence-free scale; made redundant by the Indian corpus and Olist. Row count is the weakest of our gaps. |
| **A bigger local model** | Measured: +0.0pp for double the parameters, −8.0pp for a coder tune. Retired. |
| **CSR-RAG / schema-scale retrieval** | Trigger pre-committed at 8,000 tokens; Olist measures **449**. Stays unbuilt until something fires it. |
| **Devanagari as the headline multilingual metric** | Real Indian enterprise data is Latin-script. Devanagari stays supported, Hinglish becomes the metric. |

## 9. Rules carried forward from v1 — non-negotiable

- Do not break the product.
- **Never alter a verification, fixture or scorer to make a number look better.**
  Add a correctly-defined one alongside and report both.
- Verify with commands; do not assert from memory.
- Measure before and after anything that could move a metric.
- Report regressions honestly, including your own.
- Restore shipped flag state after every experiment; the guard enforces it.
