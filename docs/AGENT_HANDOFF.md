# Agent handoff — read this first

You are picking up StateSync mid-project. This file exists so you do **not**
re-derive decisions that are already made, re-litigate questions already
settled, or re-discover findings already written down. The owner has spent real
time on those conversations and does not want them replayed.

## ▶ Before you read anything else

**`docs/CYCLE_PROTOCOL.md` governs how a session here is run.** Read it now.
In short:

1. **Audit first, on this file and the repo alone.** Write
   `docs/audits/NNN.md`. `docs/cycles/*` — the previous agents' briefings — are
   **sealed** until you have. `python3 scripts/cycle.py unseal NNN` enforces it.
2. **Then unseal, plan, and freeze acceptance criteria** before writing code:
   `python3 scripts/cycle.py freeze NNN`.
3. **Then build**, appending deviations as they happen.
4. **Then brief** the next cycle, in the same file.

The seal withholds the previous agent's *confidence*, never its *claims*.
Everything you need in order to check something is in this file. Nothing that
tells you where to look is.

`python3 scripts/cycle.py status` shows where the current cycle stands.

---

**Read in this order:**

1. **This file** — history, what is settled, what the owner actually thinks.
2. **`docs/V2_STRATEGY.md`** — the CURRENT strategy. What v2 is, what v1
   established, the three workstreams with completion conditions, the metrics,
   the success bar, and what is explicitly cut. **Start here for direction.**
3. `docs/PROJECT_DIRECTION.md` — the **v1 record, superseded**. Read only for
   how the thinking changed; do not act on its priorities.
4. `docs/StateSync_Strategy_Document.md` — the *original* vision and the
   15-source literature grounding. Still the reference for *why* components
   exist; **out of date on priorities**.
5. `docs/metrics-report.md`, `docs/limitations.md`, `docs/open_items.md` — the
   measured state, honestly recorded.
6. `docs/StateSync_Build_Spec.md` — the locked spec (binding rules A0.1–A0.6).

---

## 0. This is context, not a cage

The "SETTLED" list below exists to stop the same debates being re-run from
scratch — not to make them unchallengeable. **Every item in this document is
open to challenge if you have evidence.**

What is actually binding:

- Do not break the product.
- Do not alter a verification, fixture or scorer to make a number look better.
- Verify with commands; do not assert from memory.
- Report regressions honestly, including your own.
- Do not move an acceptance criterion after it has been frozen. Append a
  DEVIATION instead (`CYCLE_PROTOCOL.md` §4.4) — moving a goalpost mid-build is
  the same class of act as editing a scorer, and is prohibited on the same
  grounds.

Everything else — including this file, `PROJECT_DIRECTION.md`, the priorities,
and my reasoning — is a starting position. **If you find a fault, a loophole, a
contradiction, or a claim that does not survive checking, chase it and fix it.**
That is wanted, not tolerated.

Two contradictions were already found this way and corrected: CSR-RAG was
listed as settled-closed while being described elsewhere as required at scale
(§5a), and D-V2 turns out to encode a data-model assumption that may invert one
of its own conclusions (§5). Both were caught by the owner reading critically.
Assume more remain.

**When challenging something the owner decided**, say so and ask before acting
— several items here reverse earlier calls, and reversing them again without
discussion wastes their time. When challenging something *I* concluded, just
check it and correct it.

---

## 1. What the project is meant to be

An **e-commerce / logistics data analysis assistant** over ONDC-style network
data. A user asks a question in plain language; the system routes it to SQL, to
documents, or to both; runs read-only queries; and answers with prose, a
citation, and a chart.

The owner's intent, in their words, is **a real product** — "an end-to-end
deployable architecture/service that companies CAN use", not a paper artifact.
Research output is desirable but secondary to the thing actually working.

---

## 2. Version timeline

35 commits, three tags, 2026-08-13 → 2026-08-16.

| Date | Milestone |
|---|---|
| 08-13 | Phase B — walking skeleton, MCP, provider seam (BD1) |
| 08-14 | Phase B closed 28/28 · **C1** trajectory memory + schema fingerprint · **C2** interrogation + correction loop |
| 08-15 | **D** multilingual + DIGIPIN spatial · **E1** ingestion + hybrid route · E-gap close · **F** evaluator, adversarial suite, routing experiment |
| 08-15 | 🏷 **v1.0.0-phase-f** |
| 08-16 | Supervisor routing fix · **Task 10** chart emission (E2 D1) · F-V3 9/9 · F-V1 36/36 re-run |
| 08-16 | 🏷 **v1.1.0-post-f-fixes** |
| 08-16 | CSP hardening · frontend API fix · multi-tenancy built · D11 rendering half |
| 08-16 | 🏷 **v1.2.0-stretch-items** |
| 08-16 | Schema semantics measured harmful → reverted · **standard EX scorer** · **M4 diagnosis** · projection fix |

**Notable corrections in history — do not repeat these:**

- `d8a8a86` an overclaim (JSON fix "recovered 7 of 8 nulls") corrected by
  measurement to **1 of 8**. Extrapolating from probes without measuring.
- `a4293e1` schema column comments: reasoned well, spot-checked well, **measured
  harmful**, reverted.
- `9d73b7c` the frontend **could never reach its API** — every verification
  tested the API directly; nobody tested the user path.
- `d5a5d35` D11's rendering half was **committed but never shipped into the
  image**. Code in the repo ≠ code running.

---

## 3. What is SETTLED — do not reopen

| Decision | Why |
|---|---|
| **Do not add features.** Beckn ingestion, HEAR node, richer spatial | Zero value for either path right now. Discussed and closed. |
| **CSR-RAG / hierarchical schema retrieval: NOT settled — conditional** | Do **not** build it for the current 12-table schema; there is nothing to solve. It becomes **required** the moment a real schema is loaded. See §5a. |
| **Do not chase SOTA on BIRD** | Will lose to teams with 1000× compute. Report for context, not competition. |
| **Do not quote accuracy without naming the scorer** | Three scorers with different semantics exist. See PROJECT_DIRECTION §3. |
| **Do not tune against the fixture you report from** | `f_hybrid_004` deliberately left failing for this reason. |
| **`memory.enabled` default stays `false`** | Candidate-1 separation run refuted the alternative. |
| **Write capability stays absent** | Load-bearing: three SELECT-only layers and M8 assume it. |
| **Relational-plane tenant isolation is blocked, not deferred** | No `tenant_id` column; adding one changes the A5 fingerprint and invalidates every trajectory (D4). |

---

## 4. The owner's actual position — informal, and important

Read this before proposing anything. These are their views, not inferred.

**They are skeptical this is a worthwhile project.** They asked directly
whether it is "just a quick RAG + SQL project". The honest answer given was:
architecturally yes, it is a common pattern executed unusually rigorously; the
security and verification work is above the norm; the *pattern* is not novel.
They are aware of this and do not want it flattered.

**They want production over research**, while remaining open to a paper as a
byproduct. Their words: they'd "prefer production grade systems that actually
have real world use, not just on paper". If you find yourself optimising for
publishability at the expense of the thing working, you have drifted.

**They are frustrated by the accuracy numbers** and were right to be — but the
numbers were partly a measurement artifact. See PROJECT_DIRECTION §2. **Do not
present 28% as the system's capability**; that figure came from a scorer that
counts column width.

**They pushed back on advice and were right.** Two examples worth internalising:
- Told CSR-RAG wasn't worth building (true at 12 tables) — they correctly
  identified this **inverts at real scale**.
- Told the multilingual work was a strength — they correctly identified that
  **real Indian enterprise data is Latin-script**, which undermines the premise
  of the test (see §5).

Treat their domain instincts as strong evidence. They know the ONDC/Indian
enterprise context better than the model does.

**Constraints they operate under:** final semester, this is crucial to them,
$100 Azure credits, Gemini access, $5 Anthropic. Time is the scarce resource,
not compute.

---

## 5. The most recent open question — raised by the owner, NOT yet resolved

**Claim (owner):** no real company stores Devanagari. Proper nouns, addresses,
customer and product names in Indian enterprise systems are overwhelmingly
written in Latin script. Real user queries are **Hinglish / code-mixed**, with
bad grammar and spelling variance — not grammatical Devanagari.

**Assessment: substantially correct**, and it exposes a design flaw.

`D-V2` (`scripts/bench_multilingual.py`) asserts that a query's proper noun
appears **verbatim in the generated SQL**. That is only correct because this
project's synthetic schema has `locations.city_local` storing Devanagari. On a
realistic Latin-only schema, the correct SQL for a question naming दिल्ली is
`city = 'Delhi'` — **and D-V2 would score that correct answer as a failure.**

**This has a live consequence.** The schema-comments change (`migrations/005`)
was reverted in `a4293e1` largely because D-V2 fell 81.25% → 62.5%, driven by
exactly that transliteration. On Latin-script data that behaviour is *right*.
**The revert rests on a premise that may be wrong.** The pre-committed
acceptance condition in `open_items.md` inherits the same flaw.

**Two concrete moves were proposed. Both are now resolved — 2026-08-16,
fresh-context audit. Read this before acting on the section above.**

**1. Re-score D-V2 under a Latin-only data assumption — DONE. The revert
stands.** Relaxing only the script requirement (an entity counts as preserved
in *either* script; both halves of D-V2's own test kept):

| | D-V2 as written | Latin-only assumption |
|---|---|---|
| shipped config | 26/32 = 81.2% | 26/32 = **81.2%** |
| migration 005 | 20/32 = 62.5% | 24/32 = **75.0%** |

The concern was legitimate — 4 of the 12 failures under 005 *are* pure
transliteration artifacts. But 005 still lands 6.2pp under the pre-committed
≥81.25% bar, and **D-V1 treatment fell 65.0% → 55.0%** under 005, which is
execution-based equality with no script assumption anywhere in it. Nothing was
reversed; the decision is confirmed, not overturned.

**2. Build a Hinglish / code-mixed fixture — ALREADY BUILT. This proposal was
based on a factual error about the repo.**
`fixtures/golden/phase_d_multilingual.json` is **20 `hi` + 20 `hinglish`** and
has been since Phase D: `"Seller Store 9 ne kitne orders receive kiye hain?"`,
`"Jaipur mein kitne orders deliver hue hain?"`, `"Pichhle 30 dino mein kitne
orders fail ya return hue hain?"`. The missing thing was never the fixture — it
was **disaggregation**. M3 was always reported as one averaged number.

**And the split inverts this section's premise:**

| | Devanagari (n=20) | Hinglish (n=20) |
|---|---|---|
| D-V1 treatment | **75.0%** | **55.0%** |
| D-V2 entity preservation (n=16 each) | **93.8%** | **68.8%** |

The Latin-script half is the weak half, by 20pp. Confirmed live on the shipped
build: `"Lucknow mein kitne sellers located hain?"` → the model **invents a
Devanagari string** for a Latin place name (`city_local = 'उख्लौंग'`), gets 0
rows, and the Synthesizer answers "Lucknow mein 0 sellers located hain." The
correct answer is 4. That is a silently wrong answer on the input class you
identified as the realistic one — the strongest single finding of this audit.

Your instinct in this section was right for a better reason than the one given:
the multilingual path really is mis-aimed, but the fault is not D-V2
over-protecting Devanagari. It is that **nothing tells the model which of the
`x` / `x_local` column pair a value lives in**, so it guesses from the
question's script — and migration 005 broke it in one direction while the
shipped default breaks it in the other. Full working: `docs/phase_d_log.md`
§ "Audit findings, fresh-context review".

---

## 5a. CSR-RAG / schema-scale retrieval — conditional, not closed

This was nearly recorded as "settled: don't build". That would have been wrong,
and the owner caught it.

**The distinction that matters:**

| Context | Verdict |
|---|---|
| Building CSR-RAG's hypergraph pre-computation for the **current 12-table synthetic schema** | **No.** Nothing to solve. Its purpose is massively denormalised enterprise schemas, and it carries a continuous re-computation cost. Building it here would be citing a paper, not fixing a problem. |
| Needing **scalable schema retrieval** once a real schema is loaded (Olist, TPC-DS, BIRD, Spider 2.0) | **Yes — and it is the FIRST thing that breaks.** |

`DirectSchemaRetriever` calls `describe_table` on candidate tables and puts the
result in the prompt. At 12 tables that is fine.

> **✎ CORRECTED 2026-08-16.** This paragraph continued: "At 200 tables / 2000
> columns it does not fit in any context window, and no amount of prompt tuning
> helps." **Measured: that is wrong by about an order of magnitude.** The
> 12-table schema formats to 933 tokens (49.8 chars/column), so 200 tables /
> 2,000 columns is **~24,900 tokens** — comfortable in every current model, and
> workable even in the local 7B's 32k.
>
> The conditional verdict on CSR-RAG survives; its stated *mechanism* does not.
> What breaks first is **accuracy** — the model choosing wrong tables out of
> 200, which is already this project's measured weakness at twelve — followed
> by 200 sequential `describe_table` round-trips per query, since
> `sql_agent.py` passes every table with no candidate selection at all.
>
> Consequence for what gets built: the answer is **top-k candidate selection**
> behind the existing seam (~one class, the embedding model is already in the
> stack), not a hypergraph. With a pre-committed trigger so it is not a
> judgment call later. See **`docs/schema_retrieval_decision.md`**, which also
> gives a build/don't-build verdict for all eight seams.

**The seam already exists**: `schema_retriever.provider = "hypergraph"` resolves
through `orchestrator/schema_retriever.py`. Only `DirectSchemaRetriever` is
implemented. Adding a scale-aware implementation behind that seam is a config
change plus one class — it does not require touching the agents.

**Sequencing:** do not build it speculatively. Load a real schema first (next
steps §7 item 3), watch it break, then implement against the observed failure.
Solving that concretely is a genuine engineering contribution and is the
strongest technical claim available on the production path.

**Original reasoning, for the record:** `docs/StateSync_Strategy_Document.md`
deferred CSR-RAG because a synthetic capstone schema does not have the problem
it solves. That reasoning was correct *for that schema* and does not survive
contact with a real one.

---

## 6. Technical state in one screen

**Running:** 7 services, `docker compose up -d`. UI at **http://localhost:3000**
(a real query returns prose + SQL + chart + trace in ~20–70s on the local 7B).

**Stack:** LangGraph orchestrator · MCP tool server (sole credential holder) ·
Postgres + PostGIS + pgvector · Qdrant · evaluator · nginx frontend · Ollama.

**Data: TWO corpora as of 2026-08-18, document corpus enlarged 2026-08-21
(cycle 005).**
- *synthetic* — 550 orders, 30 sellers, in `public`. The document corpus
  within it is no longer 18/36: **18 baseline documents (36 chunks) plus
  2,567 per-seller review-digest documents (13,125 chunks) built from
  `olist.order_reviews`**, same `tenant_id='t_capstone_default'`, isolated
  by `doc_type='review_digest'` (see §7's cycle 005 entry, DEVIATION 1, for
  why tenant_id isolation was specified and then abandoned mid-build). A5
  fingerprint unaffected — verified before and after the schema change that
  admitted the new `doc_type` value. Reversible: `DELETE ... WHERE
  doc_type='review_digest'` removes exactly the addition, demonstrated live.
- *real* — **Olist, 1,451,414 rows** (99,441 orders / 3,095 sellers / 100,000
  reviews) in schema `olist`, checksum-verified, CC BY-NC-SA (NON-COMMERCIAL).
  Point the agent at it with `schema_retriever.target_schema = "olist"`.
  It is deliberately outside `public`, so the A5 fingerprint cannot see it.
  The review digests above are DERIVED from this schema's `order_reviews`
  table but live in `public`'s `documents`/`document_chunks`, not in `olist`
  itself — they are retrievable by the live agent today without
  `target_schema` needing to point anywhere but its shipped default.

Fixtures remain self-authored. Still never run against a standard benchmark
(BIRD/Spider).

**Model:** `qwen2.5:7b` local. Every quality number is provisional on it.

**Current honest metrics** (local 7B, post projection fix):

| | | |
|---|---|---|
| standard EX — **real data** (olist, n=18) | **local 7B 88.9% · Gemini 94.4%** | ⚠⚠ **DO NOT READ THIS AS "88.9% AT TEXT-TO-SQL ON REAL DATA." The fixture removes the two hardest parts of the task by construction**, and `gen_olist_fixture.py`'s own docstring says so as a design goal: *"EVERY question names its expected columns"* and *"EVERY ambiguous denominator is stated IN the question."* Measured over the 18: **12 name a real schema column verbatim**, the other 6 state the filter in prose, and **16 of 18 are single-table aggregates** — only two involve a join at all. A representative question is *"Considering ONLY orders that have a delivery date recorded, how many were delivered LATER than their estimated delivery date? Return a single count"* — a WHERE clause and an output shape dictated in English. Those safeguards were added for a **legitimate** reason (the scorer punishes projection mismatch, so under-specified questions manufacture false failures — the `phase_c2` 16pp defect, designed out) but the consequence is that this number measures schema-guided SQL *dictation*, not schema linking or ambiguity resolution. **The tell:** a free local 7B at 88.9% would beat published BIRD dev-set state of the art (~70–75% for far larger engineered systems) — it does not; the task is different, not the model better. **The honest capability signal is the HARD row below: the same 7B falls to 50.0% (−38.9pp) the moment questions require multi-table joins.** Full accounting, which has always been correct and is the source for the above: `docs/metrics-report.md` § "What it does establish". ⚠ Also measured with `normalization.enabled=false`, shipped default is `true` — both arms, so the pairing holds but neither number is the shipped config; audit 001 F-001-2. **Reproducible as of 2026-08-20:** the local 7B figure has been measured **three times** on the shipped corpus state (cycle 001; cycle 002 before-arm; cycle 002 after `migrations/009`), scoring 88.9% with an identical per-query pass set every time |
| standard EX — **real data, HARD fixture** (olist_hard, n=16: joins ≥3 tables / correlated subqueries / window functions) | **local 7B 50.0% · Gemini 100.0%** | cycle 001 Workstream B2, 2026-08-20. **88.9% does not survive harder questions (−38.9pp); 94.4% does (+5.5pp on this sample).** Same flag deviations as the row above, plus `target_schema=olist`, guarded since cycle 001 A1 / cycle 002 A3. ⚠ Established for **unambiguously-worded** hard questions — the fixture's safeguards remove ambiguity by construction, not the "deliberately ambiguous phrasings" its own spec called for (cycle 002, audit 002 F-002-6). Full write-up incl. corrected failure classification: `docs/metrics-report.md` § "Hard-fixture results" |
| **join-graph experiment — RUN AND REVERTED** (`migrations/008` → `009`) | **easy 88.9% → 77.8% (−11.1pp) · hard 50.0% → 50.0%** | cycle 002 Workstream B, 2026-08-20, audit 002 F-002-10. **Declaring six zero-orphan foreign keys did not help and net hurt, so it does not ship** — reverted by `migrations/009`, the same treatment `a4293e1` got via `006`. Neither targeted schema-grounding failure (`oh_j08`/`oh_j09`) was fixed; the one edge they need was deliberately left undeclared (13 orphans, never tested). **Third** documented instance of a schema/prompt-context intervention reasoned helpful and measured otherwise, after `a4293e1` (harmful) and `da6e49f` (the one exception, helpful). Three independent runs of the constraint-free state agree on all 18 easy-fixture queries, so the −11.1pp is very unlikely to be noise; **which half of 008 (primary keys or foreign keys) caused it was never isolated** — see `docs/cycles/002.md` §5.1. Single-provider; Gemini not re-run. Full write-up: `docs/metrics-report.md` § "Real-data join graph" |
| standard EX — synthetic, CORRECTED fixture (`phase_c2_v2`, n=25) | **60.0%** | ⚠ **3 of the 25 references return zero rows** (`c2g_03`, `c2g_10`, `c2g_18` — each empty for a real reason: 0 sellers with a null SLA, `max(quantity)=5` against a `>5` predicate, all 19 `pending` fulfillments have delivery events) and `ExecutionAccuracyScorer` scores any empty generated result correct against an empty reference regardless of projection or semantics — confirmed by direct call and by all three passing in **23/23** scoring opportunities across every committed run and model config this project has ever produced. Over the 22 references that can fail: **54.5%**. ⚠ **A fourth reference, `c2g_15`, cannot discriminate either, by a different mechanism**: "sellers with a Devanagari display name" matches all 30 sellers, 0 NULLs — the shipped 7B's pass is `WHERE display_name_local IS NOT NULL`, not a script test. Over the 21 that can both fail and discriminate: **52.4%**. The fixture's own `_audit.known_remaining_weakness` names the first three and routes the fix to the owner as a judgment call (leave the queries, or replace them); `c2g_15` is not in that list — audit 004 F-004-3, audit 005 F-005-2, `fixtures/golden/phase_c2_v2.json` unedited |
| standard EX — synthetic, original fixture (n=25) | 44% | re-verified 2026-08-16 under the *shipped* `ExecutionAccuracyScorer`; the earlier figure came from a looser in-script definition (`f46e0bf`) and the number happens to be identical |
| strict EX (this project's scorer) | 36% | unchanged |
| mean value recall | 74% | **weak metric — do not read as capability.** Unaligned and de-duplicated: a query returning 30 rows for a 1-row answer scores 1.00. Mean *row* recall is **48%** |
| M8 adversarial | **40/40** — re-run live 2026-08-23 (cycle 005 G4) | was 36/36; +4 payload-count assertions that close a class-empties-silently hole (`dc2e31f`). ⚠ **Was FAILING 39/40 for the whole of cycle 003**, unnoticed until audit 004 (F-004-1): `56a23bf`'s frontend cache-buster broke `verify_e2.py`'s E2-V4 check, and M8 Class 4 delegates to `verify_e2.py`, failing on its exit code. Cause fixed in cycle 004 A1/A2 (below); classes 1–3 and table checksums were clean throughout — this was a reporting failure, not a security regression, but a binary gate reading FAIL is still failing |
| M9 routing | 0.983 | unchanged |
| **M6 — grounding (the RAG half)** | context precision **0.622** (n=15, over *cited* chunks) · recall **0.80** (n=15) · faithfulness **0.8125** (n=16) · **4** ungrounded-but-true | **This row did not exist in this table until cycle 003 — the unstructured half was absent from the handoff's own metrics list, which is part of why three cycles skipped it.** The four figures above are the published ones and are UNCHANGED (`docs/bench_grounding_report.json` untouched). ⚠ **Audit 003 found three of the four measured by a defective instrument**, repaired in cycle 003 without altering them: precision divides by *cited* chunks (capped at `citation_max_chunks=3`, so 7 of 8 doc queries sit on a 0.667 ceiling **with recall 1.0**; over *retrieved* chunks it is **0.364** and the attainable ceiling was **0.778**, not 1.0); the judge's factual-correctness "ground truth" was the string `row_count=N`, not an answer value, which caused at least one verified live scoring error; and `4` was the judge's own unvalidated self-report, disagreeing with its itemised claims on 2 of 16 — recounting the published run's own claims gives **2**. A second faithfulness score that also shows the judge the SQL a hybrid answer used is now reported alongside. ⚠ **Corpus is no longer 18 docs / 36 chunks — cycle 005 added 2,567 per-seller review-digest documents (13,125 chunks), same tenant as baseline (`t_capstone_default`; see cycle 005 DEVIATION 1 for why a genuinely distinct tenant would have made the corpus unretrievable), isolated instead by `doc_type='review_digest'`.** The four figures above are UNCHANGED by that ingest — measured both before and after, live, on this same `phase_e1.json` fixture: every summary figure and every one of the 16 queries' retrieved chunk IDs identical, zero review chunks ever retrieved. That is interference on the EXISTING fixture, not a measurement of review-corpus retrieval quality — no ground truth for review-topic questions exists (`docs/limitations.md` § "The review-digest corpus"). Full account: `docs/limitations.md` § M6 entry and § review-digest corpus, `docs/audits/003.md`, `docs/cycles/005.md` C6/C7 |
| gates | seams **26/26** · pytest **93/93** · **deployed 19/19** · **E2 46/46** · **tenancy 12/12** · **M8 40/40** — all six re-run live 2026-08-23 (cycle 005 G4, after an environment gap — Docker itself had gone down between the build and the final gate run; treated the first, unverifiable completion signal as not done and re-ran everything fresh once the stack was confirmed healthy and the data confirmed to have survived the restart) | ⚠ **E2 was FAILING 29/30 from cycle 003's `56a23bf` until cycle 004 A1** — `verify_e2.py`'s E2-V4 check asserted a literal substring (`'<script src="app.js"'`) that a legitimate cache-busting query string broke, and that had never once tested for the CDN it was named against (audit 004 F-004-2). Fixed by replacing it with the real two-directional property (`scripts/frontend_asset_checks.py`), now also pinned in `tests/` so a frontend-only commit can't silently break it again. pytest was 68 going into cycle 004 (24 at cycle 000, additions through cycles 001–003, +15 of those at cycle 003's `test_grounding_scorer.py`), +20 at cycle 004 (`tests/test_frontend_asset_checks.py`) = 88, **+5 at cycle 005** (`tests/test_nginx_header_checks.py`) = 93. E2's count also moved, **30 → 46**: cycle 005 added a live-response header check (`e2_v4_csp_live`) after finding the frontend had sent **zero** of its four claimed security headers (CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy) on every path since `56a23bf` — nginx does not inherit `add_header` into a location block that declares one of its own, and `56a23bf`'s two new location blocks did exactly that. Every prior E2-V4 check read the config file, never a response; fixed in `frontend/nginx.conf`, verified against the live container, not just the repo (audit 005 F-005-1, `docs/cycles/005.md` A1-A5). **This is the first cycle any audit has run tenancy** was cycle 004's claim; still true, tenancy reproduced 12/12 again here |

**Of the 16 remaining M4 failures, 9 are genuine model errors** — not 5. The
"unrecovered SQL error" category never fired correctly, and correcting it moved
queries *into* the genuine column. See PROJECT_DIRECTION §2's updated table.

**Two prompt interventions were measured this session.** Schema comments: net
**negative**, reverted. Projection discipline: net **+8pp**, kept. Both *moved*
failures rather than removing them. On a 7B model, prompt engineering
redistributes errors more than it eliminates them — that is the argument for
spending the Azure credits rather than more wording.

---

## 7. Next steps, in order

1. ✅ **§5 resolved 2026-08-16 — the revert stands.** D-V2 re-scored under a
   Latin-only assumption: shipped 81.2%, migration-005 75.0%, still under the
   pre-committed bar, and D-V1 treatment fell 65.0% → 55.0% on a measure with
   no script assumption in it. Nothing was reversed. **The live item this
   uncovered instead:** the system silently answers Hinglish + Latin-entity
   queries *wrongly* (it invents Devanagari for Latin place names and matches
   `city_local`), and that half of the fixture is 20pp weaker than the
   Devanagari half. That is the multilingual work's real problem. See §5.

   **Not yet done, and the obvious next move:** the open item's
   "script-triggered, symmetric wording" hypothesis now has a measured failure
   mode on *both* sides, so it can be tested against a pre-committed condition
   that covers both — English line up, D-V2 ≥ 81.25%, D-V1 treatment ≥ 65.0%,
   **and** the Hinglish half no worse. Budget two ~35-minute runs.
2. ✅ **Gemini shipped** behind the BD1 seam (`ea65162`). **Azure is blocked,
   not deferred:** Azure for Students cannot provision AI resources, so the
   adapter exists and is unexercised. Anthropic remains unexercised too
   (the $5 has not been spent).
3. ✅ **Olist loaded** (`cb2450e`) — 1,451,414 rows in schema `olist`,
   checksum-verified, CC BY-NC-SA. It did surface a real-data problem
   immediately, though not the predicted one: see the join-graph experiment
   in item 6.
4. **Spider dev → BIRD dev** on Gemini free tier; Anthropic $5 as a
   cross-provider check. The `execution_accuracy` scorer already exists so the
   numbers will be comparable.
5. **TPC-DS** 1GB → 10GB if production is the chosen path.
6. **The join-graph question is half-answered and worth finishing cheaply.**
   `migrations/008` declared six foreign keys plus four primary keys on
   `olist`, measured net-harmful (easy fixture −11.1pp, hard fixture net 0,
   neither targeted failure fixed), and was reverted by `migrations/009`.
   **It added PKs and FKs in one step and no arm isolated them** — and the
   query that regressed most legibly (`ol_018`) began joining through
   `products`, which 008 had just given a primary key. Two paired runs
   (PKs-only, FKs-only) would say which half is responsible, or clear both.
   The seventh, dirty edge (13 orphans) was never declared and never tested.
   See `docs/cycles/002.md` §5.1 and §6.

**What breaks first at scale is ACCURACY, not context size.** An earlier
version of this line said `describe_table` over 200 tables / 2000 columns
"will not fit a context window" — **§5a measured that and it is wrong by
about an order of magnitude** (~24,900 tokens, comfortable everywhere,
workable even in the local 7B's 32k). What actually breaks first is the model
choosing wrong tables out of 200 — already this project's measured weakness
at *twelve*, and visible on real data at *nine*: two of the eight
hard-fixture failures are the model inventing a column rather than joining
the table it was shown (audit 002 F-002-5). Solving that is the genuine
contribution; the fix is top-k candidate selection behind the existing seam,
not a hypergraph. See §5a, which is the authoritative version of this
paragraph.

### ✅ Direction for cycle 003 — owner decision, 2026-08-20. DONE; what it found is below.

**It was:** go after the RAG / unstructured half, because three consecutive
cycles had avoided it — cycle 000 §3.2 named it the thin half; cycle 001 did
not touch it; audit 001 called it *"the largest hole in this audit"*; audit
002 did not reach it either and named it `CYCLE_PROTOCOL.md` §7's **shared
blind spot** with three cycles of evidence.

**The premise every cycle had inherited was wrong, and that is the finding.**
Cycle 000 §3.2 diagnosed the weakness as **scale** ("M6 context precision
0.622 is over 18 documents and 36 chunks — at that scale the number is close
to uninformative"). Audit 003 opened `bench_grounding.py`, the rubric and the
LLM-judge path for the first time and found the problem is **instrumentation**,
independent of corpus size: **three of M6's four published components were
measuring something other than their name.** See the M6 row in §6, the
`docs/limitations.md` M6 entry, and `docs/audits/003.md`.

Consequence for sequencing, recorded so it is not re-derived: the obvious
next move — ingesting `olist.order_reviews`' 41,755 reviews, which
`V2_STRATEGY.md` §3a proposes as *"1,160× the current chunk count, already
loaded, already checksummed"* — was **deliberately NOT done in cycle 003**,
on the grounds that scaling a corpus 1,160× under a broken instrument
produces a bigger wrong number and destroys the attribution of the repair.
That was the owner's call to overrule and was not overruled. **The instrument
is now repaired, so that objection no longer applies** — the ingest is the
natural next workstream whenever the owner wants it, and it is the one thing
that would make the RAG half's corpus real.

**Still outstanding on the RAG half after cycle 003** (none of it blocks the
ingest): the corpus is still 18 docs / 36 chunks; M6 has never been run on
any provider but the local 7B; the repaired harness has **no** repeated-trial
baseline, so its own run-to-run variance is unmeasured (cycle 003 measured
the *unmodified* harness's, which was zero on one sample); and
`e1_doc_004`/`e1_hybrid_005`'s "FastShip 1" retrieval miss is a real,
already-root-caused model failure that cycle 003 did not touch.

### ✅ Direction for cycle 004 — owner decision, 2026-08-20. DONE; what it found is below.

**It was:** repair, not growth. Audit 004 ran the two gates nothing runs and
found **both failing** — `verify_e2.py` at 29/30 and **M8 at 39/40**, the one
metric the spec defines as binary (*"a partial pass is a fail"*). Both had
been failing since cycle 003's `56a23bf`, a frontend cache-buster that broke
`verify_e2.py`'s E2-V4 literal-substring check; M8 Class 4 delegates to
`verify_e2.py` and fails on its exit code. The security posture itself was
never affected — classes 1–3 passed 39/39, no write reached the engine, no
credential leaked, no injection was obeyed, table checksums unchanged — but a
binary gate reading FAIL is still failing.

**The institutional finding, which outlives the bug.** Cycle 003 was not
careless: it ran its four gates after *every* commit and named the longer
suites it skipped. Cycles 001 and 002 both wrote the M8 re-run trigger as
*"if any data-plane or MCP code is touched"*, and B1 was frontend-only — yet
M8 Class 4 reads `frontend/index.html`. **A frontend commit can break the
adversarial security gate while satisfying every stated condition for not
re-running it.** The fix is not a tighter hand-maintained trigger; it is that
the invariant now lives in `tests/`, which CI runs.

**Also corrected:** `limitations.md`'s M6 variance sentence cited "8 of 16"
where it is **5**, all hybrid, all in the set the A1 repair touched — the
clean same-code comparison (M1 vs published) is **0 of 16**, which argues the
*opposite* of what the paragraph claimed. And audit 003 F-003-1's `0.9375`
regrade estimate **did not reproduce**: the measurement it asked for came out
at 0.875, *below* the docs-only 0.8958.

**Gate state at handoff, all six re-verified live 2026-08-20:** seams 26/26 ·
pytest 88/88 · deployed 19/19 · E2 30/30 · tenancy 12/12 · M8 40/40. Tenancy
was run for the first time by any cycle or audit.

**Still outstanding after cycle 004** (none of it blocks anything): the five
items audit 004 carried to `docs/open_items.md` — `c2g_04`'s row-count drift,
the unasserted fixture-corpus invariant, the misnamed
`m4_diagnosis_v2_corrected.json`, `build_citations`' missing support relation
(label fixed, mechanism open by design), and the three unfailable fixture
queries (disclosed everywhere, fix routed to the owner). Everything the RAG
half still owed after cycle 003 is *also* still owed: 18 docs / 36 chunks,
M6 on one provider only, no repeated-trial baseline of the repaired harness.
**New, and it bears on the ingest:** the chunker has never split a document —
95-word max narrative body against a 512-token target — so a review ingest
would leave that path just as unexercised (F-004-6).

### ✅ Direction for cycle 005 — owner delegated the call mid-session, 2026-08-21. DONE; what it found is below.

**It was:** the owner's own instinct, stated while handing off move 3's
direction call — *"I think it is the review ingest, redesigned"* — which
audit 005 (below) independently arrived at too, from a different angle
(F-005-1's CSP finding came first and was fixed ahead of it, but the ingest
was always going to be the larger piece).

**The redesign mattered more than the ingest.** A raw ingest of the 41,746
commented `olist.order_reviews` — the form `V2_STRATEGY.md` §3a and cycle
003 both described — was measured and rejected before being built: comments
average 11.9 words (max 45) against the 512-token chunk target, so it would
have left `_chunk_words` exactly as unexercised as F-004-6 found it, one
document indistinguishable from one chunk. Built instead as **2,567
per-seller review digests** (one document per seller, all that seller's
reviews concatenated) — 1,072 of them produced more than one chunk, the
largest 294, so the chunker split a document for the first time in this
project's history.

**A real conflict in the plan's own isolation mechanism, found and resolved
mid-build (DEVIATION 1, `docs/cycles/005.md` §3).** The frozen criteria
specified a tenant_id genuinely distinct from `t_capstone_default` for
isolation. Building it surfaced that `orchestrator/main.py` and
`mcp/tools/vector_tools.py::semantic_search` thread and filter on
`cfg.tenancy.default_tenant` **unconditionally**, regardless of
`tenancy.enforced` (A0.6, verbatim) — so a document under a distinct
tenant_id would never be retrieved by anything, and the whole point of the
measurement (does adding this corpus interfere with existing retrieval?)
would have measured nothing. Resolved by ingesting under the same tenant as
baseline and isolating by `doc_type='review_digest'` instead — zero changes
to A0.6 or tenant threading, reversibility demonstrated live (`DELETE`
removed exactly 13,125 chunks / 2,567 documents, verified back to exactly
18/36, then restored).

**The interference measurement came back a clean null, not a partial one.**
~365× more competing chunks in the same retrieval space (13,125 vs 36)
moved *nothing* on the existing 16-query `phase_e1.json` fixture — not the
aggregate scores, not one query's retrieved chunk IDs, not even a single
review chunk appearing in any retrieved set that didn't affect a score. The
plan predicted the opposite (*"I expect context precision to fall"*) and
required no criterion to move; it didn't. Read plainly, not overclaimed:
this says the review corpus and the fixture's topics sit far enough apart
in embedding space that top-k retrieval never crosses between them for
these 16 queries specifically, which were never designed to probe
cross-topic interference — it does not establish retrieval is robust to
scale in general, and no query that WOULD plausibly retrieve a review chunk
was ever tried (`docs/cycles/005.md` §5.1, the confidence note I would
weight most).

**Separately, a real product defect fixed ahead of the ingest.** Audit 005
found the frontend had sent none of its four claimed security headers on
any path since `56a23bf` — the same commit audit 004 already knew broke two
other things. `nginx.conf`'s `add_header` does not inherit into a
`location` block that declares one of its own; `56a23bf`'s two new blocks
did exactly that. Fixed, and — the actual institutional gap — a check that
reads a live HTTP response was added where the project had **zero**
anywhere before; every previous CSP check read the config file as text.

**Gate state at handoff, all six re-verified live 2026-08-23** (after a
genuine environment gap mid-cycle — real wall-clock time passed, Docker
itself had gone down, and a background task's "completed" notification
pointed at a log file that no longer existed; treated as unverified rather
than trusted, and re-run from a freshly confirmed-healthy stack): seams
26/26 · pytest 93/93 · deployed 19/19 · E2 46/46 · tenancy 12/12 · M8 40/40.

**Still outstanding after cycle 005** (none of it blocks anything): the four
remaining items audit 004 carried to `docs/open_items.md` (`c2g_04`'s
row-count drift, the unasserted fixture-corpus invariant, the misnamed
`m4_diagnosis_v2_corrected.json`, `build_citations`' missing support
relation) plus a new one this cycle added alongside the original three
unfailable queries — `c2g_15`'s filter is a no-op on this data, the
shipped 7B's pass on it is credited to `WHERE display_name_local IS NOT
NULL`, and the standard-EX figure this project can defend without caveat
drops one more notch, to 52.4% over the 21 references that can both fail
and discriminate. **The RAG half's corpus is no longer synthetic-only** —
that specific item, outstanding since cycle 000, is closed — but everything
else it owed is unchanged: M6 on one provider only, and while the repaired
harness now has a genuine repeated-trial baseline (three runs — cycle 003's
M2, cycle 005's before-arm, cycle 005's after-arm — all identical on every
corpus-invariant field), that baseline has never been challenged by a query
that could plausibly move it. `_embed_allowing_split`'s 2,119 fallback-split
chunks have never had their boundary quality checked — only that they
successfully embedded.

**No direction is recorded here for cycle 006.** That is the owner's to set,
and cycle 000 established the reason it is not pre-written from inside a
finished cycle: *"Pre-planning it from this side would anchor exactly what
the seal exists to prevent."*

---

## 8. How to work on this

- **Verify with commands, don't trust recall.** Context has been compressed
  repeatedly; every number in this file came from a command, not memory.
- **Measure before and after every change**, and report regressions. The repo's
  norm is that a metric that got worse is stated, not smoothed.
- **Never alter a verification to make a number look better.** The M4 scorer
  was deliberately left strict; a *third* scorer was added alongside it instead.
- **Check for running work before starting anything** — a measurement was
  contaminated once by a script that restarted the orchestrator mid-run. This
  has now happened **three times**, so it is a guard rather than advice:
  `scripts/shipped_flags.py` makes every harness assert the shipped flag state
  (file **and** liveness) and refuse otherwise, and each report carries a
  `flag_provenance` field. Check `ps aux | grep -E "bench_|check_|diagnose_"`
  and `ListAgents`/other sessions before starting a run anyway — the third
  instance was two Claude sessions in one repo, which no in-repo guard can see.
- **Never edit a shell script while it is executing.** Bash reads a script
  incrementally by file offset; rewriting it in place shifts that offset and
  the run dies mid-suite on a syntax error. Cost one M8 run (`dc2e31f`). Run
  from a frozen copy inside `scripts/` — not `/tmp`, since `SCRIPT_DIR` must
  resolve to the repo.
- **Run `python3 scripts/verify_deployed.py` before trusting any measurement.**
  "The code in the repo is the code that is running" was an unverified
  assumption under every other verification, and it has been false twice.
- **Commit after each item closes**, with the reasoning in the message.
- **Run the cycle protocol.** `docs/CYCLE_PROTOCOL.md` — audit before briefing,
  freeze criteria before building, one `docs/cycles/NNN.md` per session written
  in three passes rather than one end-of-session dump. Its §6 checklist is ten
  failure classes this repo has actually produced, each with the commit that
  produced it; walking it is the cheapest audit available.
