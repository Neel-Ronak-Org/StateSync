# Schema retrieval at scale — the decision, with numbers

**Status: decided. Do not build CSR-RAG. Build candidate-table selection when a
measured trigger fires, not before.**

Written 2026-08-16 to answer a question that had been left as a slogan: *"CSR-RAG
was discarded — what's the alternative, and what's the justification?"*
`PROJECT_DIRECTION.md` §6 and `AGENT_HANDOFF.md` §5a both said hierarchical
schema retrieval "becomes required" and "is the FIRST thing that breaks," and
neither said what would replace it or at what size. This file does both, and
corrects a claim in those two documents that does not survive measurement.

---

## 1. What the docs claimed, and why it is wrong

> "`describe_table` over 200 tables / 2000 columns will not fit a context
> window." — `PROJECT_DIRECTION.md` §6, severity **fatal — the real problem**

**Measured, not estimated.** Every application table described through the live
MCP and formatted exactly as `DirectSchemaRetriever` formats it:

| | |
|---|---|
| tables | 12 |
| columns | 75 |
| formatted schema context | **3,733 chars ≈ 933 tokens** |
| per column | 49.8 chars |
| per table | 311 chars |

Extrapolated at the same density:

| Schema size | Schema context | Fits in 32k (local 7B)? | Fits in 128k (GPT-4o)? |
|---|---|---|---|
| 50 tables / 500 cols | ~6,200 tokens | yes | yes |
| 100 tables / 1,000 cols | ~12,400 tokens | yes | yes |
| **200 tables / 2,000 cols** | **~24,900 tokens** | **yes, tightly** | **yes, easily** |
| 500 tables / 5,000 cols | ~62,200 tokens | no | yes |

**The 200-table case is ~25k tokens.** That is not a context-window failure on
any current model. The claim is wrong by roughly an order of magnitude, and it
was load-bearing: it is the stated reason CSR-RAG "becomes required."

---

## 2. What actually breaks first — in order

`orchestrator/nodes/sql_agent.py` calls
`retriever.retrieve(session, table_names)` where `table_names` is **every table
returned by `schema.list_tables`**, unnarrowed. There is no candidate selection
anywhere in the pipeline today. So at 200 tables, per miss-path query:

1. **Accuracy — the real first failure.** The model receives 200 table
   definitions and must pick the right two or three. This project already has
   direct evidence that table selection is its weak point: the single largest
   M4 failure category is wrong values from wrong joins, and BD6 records a
   2-hop join weakness across four independent checks *at twelve tables*.
   Multiplying the haystack by seventeen attacks exactly that.
2. **200 sequential `describe_table` round-trips per query.** `trajectory_steps
   += len(table_names)` — the trajectory grows linearly with schema size, so
   memory records get larger too. This is latency and storage, not correctness.
3. **Cost.** ~25k tokens × 5.2 LLM calls per query, on every miss-path query.
4. **Context window.** Only past ~500 tables, and then only on small-context
   models.

The ordering matters because it changes the fix. A context-window problem needs
compression or hierarchy. An accuracy-and-cost problem needs **selection** —
send fewer tables, not smaller descriptions of all of them.

---

## 3. The decision

**Build candidate-table selection behind the existing seam. Do not build
CSR-RAG's hypergraph.**

The seam already exists and resolves from config:
`schema_retriever.provider` → `orchestrator/schema_retriever.py`, with
`DirectSchemaRetriever` as the only registered implementation. Adding a second
is a config change plus one class; no agent code changes. That property has
been verified continuously by F-V4 and is the seam's entire purpose.

**What a `SelectiveSchemaRetriever` does:** given the question and the full
table list, score tables for relevance, `describe_table` only the top *k* (plus
anything reachable by a foreign key from them, so join paths survive), and
format as today. Scoring can start as embedding similarity over table and
column names — the stack already runs `mxbai-embed-large` for two other
purposes — and fall back to sending everything when the schema is small enough
that selection is pure risk.

**Why not CSR-RAG specifically:** its hypergraph pre-computation targets
massively denormalised enterprise schemas and carries a continuous
re-computation cost against a schema that changes. At the sizes this project
will plausibly see, that is a large amount of machinery for a problem that
top-*k* selection addresses. Building it here would be citing a paper, not
fixing a problem. If selection is measured as insufficient on a real schema,
CSR-RAG becomes a live option again — with evidence behind it.

**Why not now:** at 12 tables there is nothing to select from. Sending all 12
(933 tokens) is strictly better than any retrieval that might omit the right
one. A selector built today could only be tuned against a schema where it
cannot help, which is the "tuning against the fixture you report from" failure
in a different costume.

---

## 4. The trigger — pre-committed, so it is not a judgment call later

Build the selective retriever when **either** fires, measured not estimated:

| Trigger | Threshold | How to measure |
|---|---|---|
| Schema context size | **> 8,000 tokens** (~32k chars) — roughly 160 tables at current density, or fewer if wide | the measurement in §1, re-run on the real schema |
| Table-selection accuracy | EX drops **> 10pp** when moving to a real schema, with failures attributable to wrong-table selection rather than wrong SQL | `diagnose_m4.py` categories on the new schema |

The second is the one that matters. The first is a proxy that can be checked
before any model call.

**Loading Olist (9 tables) will not fire either trigger.** That is expected and
is not an argument against loading it — Olist is for real data and real
messiness, not for schema scale. TPC-DS (24 tables) will not fire them either.
The trigger realistically needs a genuine enterprise schema or a deliberately
wide synthetic one.

---

## 5. What this means for the other unbuilt seams

Same reasoning, stated once so it is not re-litigated per item:

| Seam | Verdict |
|---|---|
| `schema_retriever = "hypergraph"` | **Conditional** — see the trigger above. Selection first, hypergraph only if selection is measured insufficient. |
| `normalization.provider = "finetuned"` (GRPO) | **No.** A fine-tune is a large investment against a defect that is currently a *prompt/schema-semantics* problem (`x` vs `x_local`), not a model-capability one. Fix the cheap cause first and re-measure. |
| `evaluation.vision_scorer` (D11) | **Half built, correctly.** Rendering works; judging needs a vision model the deployment does not have. Unblocked the moment a multimodal provider is configured — which the Azure adapter now makes possible. |
| `routing.table = "rca"` (HEAR node) | **No.** No user need identified. |
| `spatial.provider` (richer geocoding) | **No.** DIGIPIN covers the India-specific case, which is the differentiating one. |
| `ingestion.source = "beckn"` | **No.** Live network ingestion serves no current measurement or demo. |
| write capability | **Deliberately absent, permanently.** Three SELECT-only layers and M8 depend on it. |

The honest summary of the seam count: eight seams, three with real
implementations behind them (normalizer, spatial, ingestion-local), one built
in Part F (tenancy), one half-built (vision), one deliberately empty (write),
and two genuinely unbuilt (hypergraph, HEAR). "Seam exists" is a real property
— `verify_seams.py` proves each resolves at a live dispatch point — but it is
not the same claim as "feature exists," and this table is where the difference
is written down.

---

## 6. Corrections filed against other documents

- `PROJECT_DIRECTION.md` §6 — "schema will not fit in context / **fatal**" is
  wrong at the size given (~25k tokens at 200 tables). The row should read
  *accuracy degradation from table over-supply*, severity severe, with context
  overflow a distinct and later problem.
- `AGENT_HANDOFF.md` §5a — "it does not fit in any context window, and no
  amount of prompt tuning helps" is the same claim and is wrong for the same
  reason. The conditional verdict on CSR-RAG stands; the mechanism does not.

Both are corrected in place with a pointer here rather than rewritten, per the
repo's append-don't-overwrite norm.
