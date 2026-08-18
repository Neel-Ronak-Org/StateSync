# Agent handoff — read this first

You are picking up StateSync mid-project. This file exists so you do **not**
re-derive decisions that are already made, re-litigate questions already
settled, or re-discover findings already written down. The owner has spent real
time on those conversations and does not want them replayed.

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

**Data: TWO corpora as of 2026-08-18.**
- *synthetic* — 550 orders, 30 sellers, 18 documents, 36 chunks, in `public`.
- *real* — **Olist, 1,451,414 rows** (99,441 orders / 3,095 sellers / 100,000
  reviews) in schema `olist`, checksum-verified, CC BY-NC-SA (NON-COMMERCIAL).
  Point the agent at it with `schema_retriever.target_schema = "olist"`.
  It is deliberately outside `public`, so the A5 fingerprint cannot see it.

Fixtures remain self-authored. Still never run against a standard benchmark
(BIRD/Spider).

**Model:** `qwen2.5:7b` local. Every quality number is provisional on it.

**Current honest metrics** (local 7B, post projection fix):

| | | |
|---|---|---|
| standard EX — **real data** (olist, n=18) | **local 7B 88.9% · Gemini 94.4%** |
| standard EX — synthetic, CORRECTED fixture (`phase_c2_v2`, n=25) | **60.0%** |
| standard EX — synthetic, original fixture (n=25) | 44% | re-verified 2026-08-16 under the *shipped* `ExecutionAccuracyScorer`; the earlier figure came from a looser in-script definition (`f46e0bf`) and the number happens to be identical |
| strict EX (this project's scorer) | 36% | unchanged |
| mean value recall | 74% | **weak metric — do not read as capability.** Unaligned and de-duplicated: a query returning 30 rows for a 1-row answer scores 1.00. Mean *row* recall is **48%** |
| M8 adversarial | **40/40** | was 36/36; +4 payload-count assertions that close a class-empties-silently hole (`dc2e31f`) |
| M9 routing | 0.983 | unchanged |
| gates | seams **25/25** · E2 30/30 · tenancy 12/12 · **deployed 19/19** | seams +1 (flag liveness); `verify_deployed.py` is new (`5301b89`) |

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
2. **Gemini + Azure providers** behind the BD1 seam. Model-independent, no cost.
3. **Load Olist** (real Brazilian e-commerce, ~100k orders, 9 tables) — real
   data, right domain, surfaces schema-scale retrieval immediately.
4. **Spider dev → BIRD dev** on Gemini free tier; Anthropic $5 as a
   cross-provider check. The `execution_accuracy` scorer already exists so the
   numbers will be comparable.
5. **TPC-DS** 1GB → 10GB if production is the chosen path.

**What breaks first at scale is schema size, not row count.** `describe_table`
over 200 tables / 2000 columns will not fit a context window. That is the real
engineering problem and solving it is a genuine contribution.

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
