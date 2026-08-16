# Agent handoff — read this first

You are picking up StateSync mid-project. This file exists so you do **not**
re-derive decisions that are already made, re-litigate questions already
settled, or re-discover findings already written down. The owner has spent real
time on those conversations and does not want them replayed.

**Read in this order:**

1. **This file** — history, what is settled, what the owner actually thinks.
2. `docs/PROJECT_DIRECTION.md` — the revised strategy (2026-08-16). **This
   supersedes the original strategy document wherever they disagree.**
3. `docs/StateSync_Strategy_Document.md` — the *original* vision and the
   15-source literature grounding. Still the reference for *why* components
   exist; **out of date on priorities**.
4. `docs/metrics-report.md`, `docs/limitations.md`, `docs/open_items.md` — the
   measured state, honestly recorded.
5. `docs/StateSync_Build_Spec.md` — the locked spec (binding rules A0.1–A0.6).

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
| **Do not add features.** Beckn ingestion, HEAR node, richer spatial, CSR-RAG | Zero value for either path right now. Discussed and closed. |
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

**Not yet actioned. Two concrete moves were proposed and not started:**
1. Re-score D-V2 under a Latin-only data assumption — cheap, and may vindicate
   the reverted change.
2. Build a **Hinglish / code-mixed fixture** over the existing Latin columns.
   Code-mixed text-to-SQL is *less* studied than Indic-script text-to-SQL, more
   realistic, and harder in an interesting way (no clean language boundary for
   the normalizer). Keep Devanagari as a supported path with a smaller fixture,
   not the headline metric.

---

## 6. Technical state in one screen

**Running:** 7 services, `docker compose up -d`. UI at **http://localhost:3000**
(a real query returns prose + SQL + chart + trace in ~20–70s on the local 7B).

**Stack:** LangGraph orchestrator · MCP tool server (sole credential holder) ·
Postgres + PostGIS + pgvector · Qdrant · evaluator · nginx frontend · Ollama.

**Data: 100% synthetic.** 550 orders, 30 sellers, 18 documents, 36 chunks.
All fixtures self-authored (~287 queries). Never run against a standard benchmark.

**Model:** `qwen2.5:7b` local. Every quality number is provisional on it.

**Current honest metrics** (local 7B, post projection fix):

| | |
|---|---|
| standard EX (BIRD/Spider definition) | **44%** |
| strict EX (this project's scorer) | 36% |
| mean value recall | 74% |
| M8 adversarial | 36/36 |
| M9 routing | 0.983 |
| gates | seams 24/24 · E2 30/30 · tenancy 12/12 |

**Two prompt interventions were measured this session.** Schema comments: net
**negative**, reverted. Projection discipline: net **+8pp**, kept. Both *moved*
failures rather than removing them. On a 7B model, prompt engineering
redistributes errors more than it eliminates them — that is the argument for
spending the Azure credits rather than more wording.

---

## 7. Next steps, in order

1. **Resolve §5** — re-score D-V2 under a Latin-data assumption; decide whether
   the schema-comments revert stands. Ask the owner before acting; it changes a
   decision already taken.
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
  contaminated once by a script that restarted the orchestrator mid-run.
- **Commit after each item closes**, with the reasoning in the message.
