# Known limitations, stretch items, and open gaps

Phase F Deliverable 8 / F-V8 honesty gate. Every claim in
`docs/metrics-report.md` traces to a measurement recorded somewhere in this
repo; every weak measurement and every unbuilt stretch item is listed here,
not smoothed over. Cross-check: nothing in `docs/metrics-report.md` should
be unsupported by a recorded measurement, and nothing weak should be absent
from this file.

## Stretch items and their seams (Strategy §6 / Appendix 4)

All eight seams exist and resolve from config at a real dispatch point, per
F-V4 (`scripts/verify_seams.py`, see `docs/phase_f_log.md`).

**Two are no longer unbuilt**, so the old blanket claim ("the content behind
each is deliberately unbuilt") no longer holds and has been replaced by
per-row status:

- **Multi-tenancy enforcement — BUILT** and verified 12/12, including a fix
  for a real cross-tenant read that existed in `vector.hybrid_select`.
- **Vision Score — HALF BUILT.** The rendering half runs; the VLM judging
  half does not, and the two failure modes are deliberately distinct so the
  half-built state cannot be mistaken for either "done" or "unbuilt".

The remaining six are genuinely seam-only. Reporting them all as "unbuilt"
after building two would have been the same silent-overwrite failure this
project keeps catching elsewhere.

| Stretch item | Seam | Status |
|---|---|---|
| GRPO fine-tuned multilingual model | `normalization.provider = "finetuned"` (`LanguageNormalizer`) | Seam exists (`orchestrator/language_normalizer.py`); only `PromptNormalizer` is implemented. Unbuilt: the fine-tuned model itself. |
| Vision Score | `evaluation.vision_scorer.enabled` (`Scorer` registry, `"vision"`) | **Half built.** Registered in both scorer modules; the **rendering half is implemented and deployed** — `vision.render_spec_to_png()` uses `vl-convert` (self-contained Rust, no browser, no network, so the evaluator keeps working without an outbound path per A1). **Corrected 2026-08-16:** "deployed" was true of the *evaluator only*. `orchestrator/vision.py` imports `vl_convert` and `orchestrator/pyproject.toml` never declared it, so the orchestrator shipped a registered scorer whose renderer was absent — `VisionScorer.score()` raised `ChartRenderUnavailable` there, not the `VisionModelUnavailable` that marks the genuine half-built state. Found by CI on its first run (the clean-machine condition F-V7 has been owing since Phase F); declared, rebuilt, and verified inside the container. Two distinct failure modes so a half-built feature cannot masquerade as unbuilt: `ChartRenderUnavailable` (no renderer) vs `VisionModelUnavailable` (no VLM). **Unbuilt: the VLM judging call itself**, which needs a vision-capable model — the local stack has none. |
| CSR-RAG hypergraph retrieval | `schema_retriever.provider = "hypergraph"` (`SchemaRetriever`) | Seam exists (`orchestrator/schema_retriever.py`); only `DirectSchemaRetriever` is implemented. Unbuilt: the hypergraph pipeline. |
| Multi-tenancy enforcement | `tenancy.enforced` (MCP session policy layer) | **Built and verified (12/12, `scripts/verify_tenancy.py`).** Enforcement now does three real things: admission control (unknown tenant → 403, not an empty result set); a **mandatory tenant predicate on `vector.hybrid_select`**, which closes a genuine cross-tenant read — it executes model-authored SQL and, unlike `semantic_search`/`get_passage`, never filtered `tenant_id`; and a refusal to start unless `relational_plane_shared_ack` records that the business tables are shared. **Structurally unbuildable here:** row isolation on the relational plane — those tables have no `tenant_id`, and adding it changes the A5 fingerprint and invalidates every stored trajectory (D4). |
| HEAR root-cause node | `routing.table` entry `"rca"` (`SpecialistNode`) | Registry exists (`orchestrator/routing/registry.py`), only `sql`/`docs`/`hybrid` registered. Unbuilt: the specialist node itself. |
| Richer spatial resolution | `spatial.provider = "<other>"` (`SpatialResolver`) | Seam exists (`orchestrator/spatial_resolver.py`); only `DigipinResolver` is implemented. Unbuilt: free-text/reverse geocoding. |
| Live network ingestion | `ingestion.source = "beckn"` (`IngestionSource`) | Seam added in Part F (`scripts/ingestion_source.py`) — E1 shipped `scripts/ingest.py` without wiring this at all; only `LocalFileSource` is implemented. Unbuilt: the Beckn-protocol source. |
| Write capability | — | Deliberately absent. F-V4 confirms no such interface exists anywhere (Strategy §6: "the absence of an extension point is itself a design decision"). |

## A0.1 gaps raised, and their disposition

Full text in `docs/spec-gaps.md`; summarized here for the honesty gate.

| ID | What was ambiguous | Disposition |
|---|---|---|
| GAP-1 | Does a memory-hit successful query also write a trajectory back? | Decided (not a BD): no — only `hit_count` bumps. Reversible if C1-V1/V2 suggest otherwise. |
| GAP-2 | `join_cardinality` has no engine-observable signal the deterministic classifier could match on. | BD3: out of scope for the taxonomy; `mq_10` retained and reported as "never reaches the classifier," not a repair-rate failure. |
| GAP-3 | The literal `mq_08` SQL in the spec didn't reproduce its described failure against this Postgres/PostGIS. | Fixture-content correction: substituted a verified-working same-family SRID-mismatch trigger. |
| GAP-4 | Two pre-existing Phase B fixture gaps (stub digipin encoder; no seeded location had ≥3 neighbors within 2km), invisible until D pinned the real library. | Both fixed: real `digipin` library pinned, seed fixture regenerated with tighter jitter + one added hub location, `digipin_reference.json` frozen against `decode(encode(...))`'s own output. |
| GAP-5 | Does hybrid's decomposed SQL sub-task engage C2's correction loop? | Implementation choice: single-shot, no retry (decomposition already simplifies the sub-question); failures are still classified and surfaced honestly, never silently treated as empty. |
| GAP-6 | Does "the application schema" (A5 fingerprint) include PostGIS's `spatial_ref_sys`? | **Open, escalated** — see `docs/open_items.md`. Fixed on the `schema.list_tables` side (A4.1, extension-ownership filter); the fingerprint formula itself is untouched, since changing it invalidates every stored trajectory (D4) and that blast-radius decision isn't this build agent's to make unilaterally. |
| GAP-7 | F-V2's over-exposure experiment names surfaces (9/15/25/40) matching `mcp.tool_ceiling`'s own scale, but M9 measures Supervisor routing, which D1 decoupled from MCP's tool list entirely — no code path connects the two. | Implementation choice: the experiment crowds the Supervisor's own classification prompt with decoy capability descriptions (reusing the spec's 9/15/25/40 as decoy-count targets), since that's the only surface that can actually move M9. |
| GAP-8 | F1 Scope never defines "the full golden set" the evaluator's continuous pipeline runs. | Implementation choice: the union of the two fixtures shaped for the two enabled scorers — `phase_c2.json` (ExecutionScorer) and `phase_e2.json` (SpecScorer), named in config. |

`NOTE-1` (`docs/spec-gaps.md`) is not a gap — a reversible flag-default change
(`memory.enabled` true→false), logged for traceability per `flags.toml`'s own
rule that a default change is not a BD.

## Metrics whose measured value is weak

Recorded honestly in `docs/metrics-report.md`; summarized here per F-V8:

- **M2 (consistency, C1-V3): FAIL on the local model.** Treatment mean
  distinct-result-sets **1.8** > control 1.0 — memory-hit SQL is less reliable
  than a fresh full-schema read on `qwen2.5:7b`. Recorded as BD6 (provisional,
  self-voiding, re-baseline-gated). The F-V3 re-run reproduced **1.8 exactly**,
  so this is a stable property of the model, not run-to-run noise — which
  strengthens the BD6 attribution rather than leaving it a one-off.
- **C2-V1 conditions 3–4: FAIL.** The correction loop never redirects a 2-hop
  join through `orders` on this model, despite a documented, verified prompt
  improvement attempt. Same BD6 disposition.
- **D-V6: FAIL (exact count).** Generated SQL omits the `fulfillments` join +
  `status='delivered'` filter (41 vs. hand-computed 30). Same BD6 disposition;
  mechanism (digipin decode, predicate construction, GIST index) independently
  confirmed correct.
- **M4 (C2-V8, query correctness): low on both paths** — 28.0% cold / 32.0%
  warm on the local model (F-V3 run). Not BD6-covered (BD6 is scoped to exactly
  three checks), but the same root cause class (2-hop join / filter omission) —
  see `docs/rebaseline_runbook.md`. **Read alongside the instrument limitation
  below: M4 is a lower bound on semantic correctness**, and 15 of the 18 cold
  misses return exactly the expected row count.

  **Updated 2026-08-16 (cold path only).** The projection-discipline prompt
  (`da6e49f`) and a corrected diagnostic (`f46e0bf`) both landed after the F-V3
  run above. Clean re-run, `docs/m4_diagnosis_v2_corrected.json`: **cold
  36.0% (9/25)**, standard EX 44.0%, mean value recall 74.0%, mean row recall
  48.0%. **The warm path has not been re-measured since either change**, so the
  32.0% above is now attributed to a build that no longer ships.

  **What got worse in the re-read, stated because it is the finding that
  matters:** the "unrecovered SQL error" category was empty in truth — it fired
  on `bool(state_trace["errors"])`, which stays non-empty after the correction
  loop *succeeds*. Correcting it moved both of its queries into genuine-error
  categories. Of the 16 remaining misses, **9 are genuine wrong-value or
  wrong-shape model errors** (was 5 by the old labelling), 4 are projection
  width, 2 are the strict scorer alone, 1 is routing. The measurement artifact
  is now smaller than the model error, which is the opposite of the direction
  `PROJECT_DIRECTION.md` §2 originally argued.
- **M6 (E1-V3, grounding): moderate, and three of its four published
  components were measured by a defective instrument until cycle 003 —
  see the entry below.** Context precision **0.622 over 15 scored** (not
  16 — `e1_hybrid_001` is excluded by design), recall 0.80 (F-V3 run; 0.769
  at E1, 0.831 at the first Phase F run). One clearly-diagnosed,
  partially-fixed root cause (aggregate queries not echoing an ID column for
  targeted retrieval); a second, deeper cause (a specific
  entity-name-to-table-mapping failure, "FastShip 1") accepted as measured
  model variance after four distinct, root-caused fix attempts.
- **M7 (E2-V1, chart quality): measured, and low at 0.4850** (local,
  provisional). Task 10 built the chart emission this metric was waiting on.
  The A7 validity half is a clean binary **PASS at 30/30**. The quality figure
  is dragged mostly by 7 `chart:null` results, and **5 of the original 8 nulls
  were upstream SQL returning 0 rows or the wrong shape** — so M7 on this model
  is substantially a downstream reflection of M4, not a clean measure of chart
  competence. Per-query accounting: `docs/phase_e2_log.md`.

## Limitations of the measuring instruments themselves

The metrics above describe the system. These describe the *scorers*, and they
matter just as much: a number is only as trustworthy as the thing producing it.

- **SpecScorer (M7) cannot see a channel bound to the wrong column.** The
  encoding key is `<channel>|<type>|<aggregate>`; the `field` NAME is
  deliberately excluded, because a reference spec describes what the chart
  should *look like*, and the column's name is a text-to-SQL artifact — the
  same correct chart is produced whether the agent named a column `seller_id`,
  `seller`, or `s_id`, and scoring the name would measure SQL naming under the
  guise of measuring chart quality. The cost is real and is stated rather than
  hidden: **a bar chart plotting the right shape from the wrong column scores
  as correct.** This is precisely the gap the deferred VLM Vision Score (D11)
  exists to close — VegaChat reports ~0.71 Pearson for VLM judging vs ~0.65 for
  deterministic spec scoring, and this blind spot is a concrete instance of
  that difference. Mitigating factor, not a fix: `build_chart()` drops any
  channel naming a column absent from the result set, so the wrong column must
  at least be a column that genuinely exists in the query output.

- **M4 (C2-V8) measures exact frame equality, not "did it answer the
  question".** The signature is `{n_columns, sorted row-multiset}`. Column
  *names* are deliberately ignored (independently authored SQL aliases
  differ), but the column *count* is not, so a generated query that returns a
  correct superset projection scores as a miss. Measured on the F-V3 run: **15
  of the 18 cold misses have a row count exactly equal to the expected row
  count**, and **3 of those return zero rows on both sides** — where the row
  values are trivially identical, so the miss is caused by projection width
  alone. `c2g_10` is the worked example: it routes correctly, generates
  `SELECT ... FROM sellers WHERE contracted_sla_hours IS NULL` (semantically
  the ground truth), returns the expected 0 rows, and scores 0 because it
  projected 4 columns where the reference projected 2.

  **The scorer was NOT changed.** Loosening it after seeing the results, to
  raise M4, would be editing the instrument to fit the number — precisely what
  A0.4 forbids. The strictness is defensible on its own terms (a different
  projection *is* a different answer frame). What is recorded here is that
  **M4 is a lower bound on semantic correctness**, and the gap is not small:
  for the remaining 12 same-row-count misses the cause (projection width,
  differing values, or both) was not determined and is not asserted.

  **Recomputed 2026-08-16 on the current build**, now that the diagnostic
  persists the rows instead of their length (`f46e0bf`), so this no longer has
  to be left undetermined. Of the **16** strict misses: **13 return exactly the
  expected row count**, 2 of those return zero rows on both sides, and **11 of
  the 13 differ in column count**. But column width is not the whole story —
  only **2** of the 16 are correct under standard EX, and **11 of the 13
  same-row-count misses have row recall 0.00**, i.e. the values genuinely
  differ, not just the projection. The lower-bound claim stands; the gap is
  **narrower than the earlier framing implied**, and the previously
  undetermined cause is now determined and is mostly wrong values.

  The scorer is now pinned from the other side too:
  `tests/test_scorer_semantics.py` asserts C2-V8 keeps distinguishing empty
  results with different column counts, so a future "cleanup" cannot quietly
  relax it and raise M4.

- **M6 (E1-V3, `scripts/bench_grounding.py`) was the only metric with no
  test, no entry in this section, and a gate that cannot fail — audit 003
  found three of its four published components measuring something other
  than their name, and cycle 003 repaired the instrument without moving the
  published numbers.** `docs/bench_grounding_report.json` (0.622 / 0.80 /
  0.8125 / 4) is unchanged; two fresh runs sit alongside it as
  `docs/bench_grounding_report_003_m1.json` (unmodified harness, a noise
  baseline — reproduced 0.622 / 0.80 / 0.8125 / 4 exactly, and all 16
  synthesizer answers were byte-identical to the published run) and
  `docs/bench_grounding_report_003_m2.json` (repaired harness).

  1. **The faithfulness judge never saw the SQL rows a hybrid answer's own
     synthesizer prompt included**, while being told document passages were
     "what the system actually saw." 5 of 6 ungrounded claims in the
     published run were SQL-derived; 4 were single values independently
     verified exactly correct against the corpus. **Not fixed by changing
     `faithfulness_score`** — that stays exactly as specified (verified by
     `tests/test_grounding_scorer.py::test_faithfulness_score_untouched_by_repair`
     and by `fixtures/rubrics/faithfulness.json`'s md5 being unchanged). A
     second score, `mean_faithfulness_all_sources`, is computed alongside it
     under a new rubric (`fixtures/rubrics/faithfulness_v2.json`) that also
     shows the SQL. M2: **0.8958** (docs-only, unchanged definition) /
     **0.875** (all-sources) over the same 16 queries and the same 16
     synthesizer answers as the published run.

     The docs-only number moving from the published 0.8125 to 0.8958 is
     **not itself the all-sources fix** — that pass is the separate,
     additional score above. It happened because item 2's reference repair
     shares its prompt text with this judge call too, and at least one
     instance of it is not noise: `e1_hybrid_006`'s true answer is **0**
     disputes (verified live against the corpus), and the OLD row-count
     reference caused the judge to credit the wrong claim "1 dispute" as
     both grounded and factually correct — identically in both the
     published run and the M1 noise baseline. Under the repaired reference
     the same judge call correctly marks it ungrounded and incorrect,
     which is `e1_hybrid_006`'s own score moving 1.0 → 0.5 — the one query
     in this comparison that moved down, and the reason is a fix, not
     drift. Four others moved up. This is read as evidence the repair
     changes judging quality for the better on at least this instance, not
     as proof the whole 0.8125 → 0.8958 delta is signal rather than
     variance — see "What this does not establish" below.
  2. **The "database ground truth" the judge's factual-correctness check was
     shown was the string `row_count=N`** — a row COUNT, not an answer
     value — for every query with a SQL ground truth. Three claims that are
     exactly correct (S014 = 2 disputes, S001 = 17 orders, S025 = 11 orders,
     each independently re-verified) were scored factually incorrect against
     it. Fixed: the ground-truth SQL is now executed read-only through the
     same MCP path the orchestrator itself uses, and the judge is shown the
     actual returned rows.
  3. **`total_ungrounded_but_true` was an unvalidated integer the judge typed
     once**, separate from and sometimes disagreeing with the itemised
     `claims` array it also emitted — 2 of 16 published queries disagreed
     (judge said 4 total; its claims supported 2). Fixed: now recomputed from
     the claims array; M2 reproduces the same recount (**2**, against the
     judge's own self-report of 4 on identical inputs) with the disagreement
     count now surfaced (`ubt_disagreement_count`) rather than silently
     absorbed.
  4. **Context precision divides by CITED chunks**, capped at
     `citation_max_chunks = 3`. With 2 `expected_chunk_ids` per fixture entry
     that alone puts a 0.667 ceiling under every doc-route query; 7 of 8 sat
     exactly on it **with recall 1.0** in every run measured. The published
     "~38% of retrieved context is irrelevant" gloss (`V2_STRATEGY.md`) was
     computed from the cited figure but described the retrieved one — over
     the chunks actually retrieved, M2 measures **0.364**, and the per-run
     ceiling given this run's citations was **0.778**, not 1.0. The original
     `mean_context_precision` definition is unchanged and still published;
     `mean_context_precision_over_retrieved` and
     `max_attainable_context_precision` are reported alongside it, not
     instead of it.

  **What this does not establish.** None of the above reproduces on a second
  independent model sample beyond the one M1/M2 pair — the judge is
  `qwen2.5:7b`, non-deterministic, and 8 of the 16 v1 faithfulness scores
  *did* change between M1 and M2 even though the scoring code for that pass
  is byte-identical, which is itself informative about how much run-to-run
  judge variance this metric carries independent of any instrument change.
  Full write-up and per-query detail: `docs/audits/003.md`,
  `docs/cycles/003.md`.

- **`empty_result` is classified as a retryable error even when empty is the
  correct answer.** `c2g_10`'s true answer is zero rows, and the correction
  loop still spent a retry on it (`attempt=1`, `category: empty_result`).
  Costs latency and one LLM call on every legitimately-empty query. Not
  changed here: the taxonomy entry is shared with genuinely-wrong queries that
  return nothing, and distinguishing them needs a signal the system does not
  currently have.

- **M9's routing fixture only measures what it contains.** F-V2 scored 60/60
  for the whole of Phase F while a deterministic routing defect was live in
  production (`c2g_10`), because every `sql` query in the fixture had been
  authored unambiguous. Six overlap-vocabulary cases were added once that was
  discovered. The general lesson is recorded because it will recur: **a fixture
  authored by the same reasoning as the prompt shares the prompt's blind
  spots.** The Phase D fixture, written earlier and independently, is what
  exposed the defect.

- **A7 validation in `run_e2_v1.py` is not independent.** It imports the
  orchestrator's own validator, so it re-checks that the pipeline's output
  matches the pipeline's own rules — it is a regression guard, not a second
  opinion. The genuinely independent second validator is the frontend's,
  hand-written in JavaScript and exercised in-browser (E2-V6). A Python
  reimplementation was deliberately *not* written, since it would test a copy
  of the rules rather than the rules.

## C2-V2 categories with a 0% repair rate

Per F-V8's specific requirement. From the Phase F re-run of
`scripts/bench_correction.py` (`docs/bench_correction_report.json`,
`per_category`):

- **`unknown_column` (mq_01): 0% repaired (exhausted).** The correction loop
  never finds the `orders`-mediated join path from `fulfillments` to
  `sellers` within the retry bound. This is the same 2-hop-join weakness
  BD6 records for C2-V1/M4/D-V6 — one shared root cause across four
  different checks, not four independent failures.
- **`permission_denied` (mq_07): 0% repaired, 100% terminal — correct
  behavior, not a defect.** A3 requires this category to never retry (D15:
  retrying a security-boundary rejection is an agent probing around a
  control). 0% repair here is the classifier working as specified.
- **`unclassified` (mq_12): 0% repaired, 100% terminal — also correct
  behavior**, same reasoning: the fallback category is deliberately terminal.
- **`join_cardinality` (mq_10): 0% repaired, structurally unreachable — not
  a repair-rate failure at all.** Per BD3/GAP-2: this seed executes
  successfully (silently wrong aggregate, no engine-observable failure), so
  it never reaches the classifier to be "repaired" or not. Retained in the
  fixture exactly as the spec requires, reported honestly as out-of-reach
  rather than folded into the repair-rate denominator.

Net: of four 0%-repair categories, one (`unknown_column`) is a genuine,
already-diagnosed local-model weakness; two (`permission_denied`,
`unclassified`) are 0% by design; one (`join_cardinality`) is structurally
outside the classifier's reach by design (BD3). No category fails silently
or unexplained.

## Real data: what it is, and what its licence forbids (added 2026-08-17)

**The corpus is no longer 100% synthetic.** `olist` schema, 1,451,414 rows of
real Brazilian e-commerce transactions (99,441 orders, 3,095 sellers, 100,000
reviews), fetched and checksum-verified by `scripts/fetch_olist.sh`.

**LICENCE: CC BY-NC-SA 4.0 — NON-COMMERCIAL.** Fine for this capstone, for
benchmarking, and for any published measurement with attribution. **It cannot
be the data behind a commercial deployment.** That is a real constraint on the
"self-hosted service companies can use" framing: the *architecture* is
deployable, this *data* is not sellable. Flagged here rather than discovered
later by someone who read the strategy document and not the licence.

**The agent can query it, and could before any code changed.** The Layer 1 gate
is SELECT-only, not table-scoped, so `SELECT ... FROM olist.orders` worked
immediately while `DELETE FROM olist.orders` was refused — the guarantee
extended to a new schema with no change, because it was never built on a table
allowlist. Only *discovery* needed work (`schema` parameter on
`list_tables`/`describe_table`).

**It is NOT in the A5 fingerprint, deliberately.** `mcp/fingerprint.py`
hardcodes `table_schema='public'` in all three queries, so the fingerprint is
structurally incapable of covering `olist` — asserted by
`tests/test_schema_scoping.py`, not assumed. Verified live: `cf4caa5c…` before
and after the load, the same value recorded in `a4293e1`.

**Consequence nothing enforces:** trajectory memory over `olist` is meaningless
(the memory key IS the A5 fingerprint, which does not describe `olist`), so
runs against it must keep `memory.enabled=false`.

**What the real data has that the synthetic corpus never did**, surveyed on
load — this is the evidence for why synthetic numbers do not transfer:

| | |
|---|---|
| 8 orders are `status='delivered'` with **no delivery date**; 2,963 more are not delivered at all | "average delivery time" now has a real denominator ambiguity, not an authoring slip |
| **775 orders have no line items** | "revenue per order" joins them away silently |
| **611 distinct `seller_city` values, and normalising case+whitespace gives 611** — zero improvement | `sao paulo` (694), `sao paulo - sp` (3), `sao paulop` (typo), `santo andre/sao paulo`. The `Bangalore/Bengaluru/Banglore` variance §7a called untested, now real |
| 1,000,163 geolocation rows → **720,154 distinct points** (28% duplicates) | |
| 610 products have a NULL category | |
| `product_name_lenght` is **misspelled in the source** | kept verbatim; renaming would be cleaning |

**The schema carries no join graph — a confound under every real-data number
published so far (added cycle 002, audit 002 F-002-10).** `migrations/007`
declared this deliberately: *"NO CONSTRAINTS, DELIBERATELY … Declaring FKs
would either reject rows at load time or force cleaning — and the DIRT IS THE
POINT."* Measured consequence, not previously stated anywhere:

| | `public` (synthetic, shipped default) | `olist` (real) |
|---|---|---|
| declared foreign keys | **17**, across 10 tables | **0** |
| declared primary/unique keys | present on every table | **0** |

`orchestrator/schema_retriever.py`'s `DirectSchemaRetriever._format_table`
renders a `FOREIGN KEYS:` block in the SQL agent's prompt **only** from
declared foreign keys — it is the sole join information the prompt carries.
**Every real-data accuracy number this project has published — 88.9%/94.4%
(`olist.json`) and 50.0%/100.0% (`olist_hard.json`) — was therefore measured
with no join graph shown to the model**, on fixtures where all but one
question requires a join. `docs/schema_retrieval_decision.md`'s planned
`SelectiveSchemaRetriever` safety mechanism — top-*k* tables *"plus anything
reachable by a foreign key from them, so join paths survive"* — would be
**inert** on this corpus for the same reason.

This is recorded here unconditionally, independent of whether it is ever
measured (`docs/cycles/002.md` Workstream B). `migrations/007`'s reasoning is
sound as a general principle and not reversed by this entry alone — see the
cycle for what changed and why.

**✎ MEASURED AND REVERTED, 2026-08-20 (cycle 002).**
`migrations/008_olist_join_graph.sql` declared the six zero-orphan edges and
was measured paired, one variable, both arms freshly run. It **fixed neither
failure it targeted** and cost the easy fixture 11.1pp (88.9% → 77.8%; hard
fixture net 0). **`migrations/009_revert_olist_join_graph.sql` reverts it** —
same treatment `a4293e1` got via `migrations/006`, for the same reason. The
table above therefore describes the corpus as it stands **today**, not merely
as it stood before the experiment. Three independent runs of the
constraint-free state now agree on all 18 easy-fixture queries, so the 11.1pp
cost is very unlikely to be noise; which half of 008 (primary keys or foreign
keys) caused it was never isolated. Both migrations stay in the repo,
re-appliable. Full accounting: `docs/metrics-report.md` § "Real-data join
graph".

## "Does it work on production-level data?" — measured answer (2026-08-18)

Asked directly, and the honest answer separates two claims that are easy to
conflate.

**REAL data: yes, demonstrated.** 17/18 questions answered correctly over
1,451,414 rows of real Brazilian e-commerce transactions, including 7 questions
that deliberately probe real-world dirt (orders marked delivered with no
delivery date, orders with no line items, 611 spellings of seller_city, NULL
categories, 28% duplicate geolocation rows). A free local 7B scored 16/18 on
the same set.

**PRODUCTION-LEVEL: no. Four dimensions, measured not assumed.**

*1 — Data-plane scale: PASSES, with room.* Probed on the real corpus:

| query | time |
|---|---|
| scan 1,000,163-row table | 0.09s |
| GROUP BY over 1M rows | 0.11s |
| 3-table join, 112K items → aggregate | 0.23s |
| 1M × 99K join (deliberately expensive) | 0.93s |

The 15s statement timeout is nowhere near binding and the 1000-row cap engages
correctly (`truncated: true`). **But 1.45M rows is not production scale.** Real
warehouses are 10²–10⁴× larger, and nothing here has been run against one. The
largest single table is 1M rows.

*2 — Schema scale: UNTESTED at production size.* 9 tables / 52 columns → ~449
tokens of schema context. `docs/schema_retrieval_decision.md` pre-committed a
build trigger at 8,000 tokens; this is 5% of it. Real enterprise schemas are
100s of tables, and the selective retriever that would need is deliberately
unbuilt.

*3 — Operational readiness: ABSENT, and this is the honest blocker.* Verified
by grep, not memory:

| | |
|---|---|
| authentication | **none.** No login, no JWT, no session. Anyone who reaches port 3000 is an analyst |
| API abuse controls | **none.** `/query` is unauthenticated and unthrottled, and every call costs LLM tokens |
| audit log | **none.** No record of who asked what — the first thing a regulated buyer asks for |
| multi-user | not supported; tenancy exists at the MCP boundary but there are no users |
| observability | structured logs only; no metrics endpoint, no alerting |
| backup / restore | not documented, never exercised |
| concurrency | pool_max_size=10, never load-tested; the LLM (~20s/query local) is the real bottleneck, not Postgres (<1s) |

*4 — Workload realism: NARROW.* The 18 questions were authored by this project,
are single-turn, and are mostly single-table aggregates. No follow-ups, no
clarification, no ambiguity resolution, and few multi-hop joins — which BD6
records as the system''s measured weakness.

**The defensible sentence** is: *"an architecture with production-grade security
and verification properties, demonstrated on real transactional data at 1.45M
rows, not yet operationally deployable."* The security work genuinely is
production-grade — three SELECT-only layers held across a schema they had never
seen, 40/40 adversarial. The operational surface around it is not built, and
auth is the single largest gap.

## Environment limitations affecting this build session specifically

Not defects in StateSync's own logic — infrastructure conditions on this
particular development machine, recorded because they shaped how
verification was actually carried out:

- **Docker Desktop BuildKit stalls indefinitely** on `docker compose build`
  for this project, intermittently, with no error output. Root-caused as
  BuildKit-specific (the classic builder, `DOCKER_BUILDKIT=0
  COMPOSE_DOCKER_CLI_BUILD=0`, completes normally every time it was tried).
  Workaround recorded in `README.md` §5.
- **`postgres`/`qdrant` host port-publish mappings intermittently break**
  after container recreation, unrelated to `docker-compose.override.yml`
  (confirmed correct via `docker compose config`). Worked around by running
  host-side administrative scripts inside a container already on `db_net`
  (`mcp`) instead of from the host.
- **Two independent Ollama instances exist on this host**: a native
  `Ollama.app` (has `qwen2.5:7b`, reachable only via `host.docker.internal`,
  i.e. only from inside a container) and the Dockerized `ollama` service
  (has only `mxbai-embed-large`, reachable from the host via `localhost`).
  Any host-side script needing a real LLM completion (not just an embedding)
  must run inside a container.
- **F-V7 (clean-machine reproducibility) was verified by document audit, not
  literal re-provisioning.** A full from-scratch bring-up on a genuinely
  separate machine has **not** been performed, because doing it here would
  require `docker compose down -v`, destroying the seeded state that F-V1's
  adversarial payloads and F-V3's results depend on. Recorded honestly rather
  than claimed as done. **This remains the single outstanding verification
  step in the whole build.**

  Two audit passes were done instead, and both found real defects — which is
  itself evidence the literal test is worth running:

  *Pass 1 (Phase F):* README described Phase B only, and omitted the E1
  document-ingestion step and the Phase F adversarial-seeding step entirely.
  A clean-machine operator would have had a stack that could not answer
  `docs`/`hybrid` queries and could not run the security suite.

  *Pass 2 (post-freeze handoff):* a line-by-line cross-check of every README
  claim against the repo found three more, all now fixed:
  1. **`ollama_url` mismatch (the serious one).** README's config snippet
     showed `http://ollama:11434` while the shipped `config/flags.toml` has
     `http://host.docker.internal:11434`, and README step 4 told the operator
     to pull **both** models into the Dockerized `ollama` container. Following
     the README exactly on a clean machine would put the models in the
     container while the shipped config pointed at a native host Ollama that
     does not exist there — every LLM call would fail with a connection error
     at first query, not at startup. README now documents both topologies,
     states which value ships, and says explicitly which to change.
     (The committed value is left as `host.docker.internal` because this
     development machine genuinely runs a native Ollama; the container here
     holds only `mxbai-embed-large`, not `qwen2.5:7b`.)
  2. **Architecture diagram wrong.** It placed `ollama` on `db_net` only; it
     is on both `db_net` and `app_net`. Corrected, with a note on why that is
     not an A1 isolation concern (ollama holds no credentials, fronts no data,
     is absent from A1's topology table, and does not route between networks —
     `postgres`/`qdrant` stay db_net-only and B-V3 is unaffected).
  3. **`docker-compose.yml` contradicted itself.** The comment above the
     `ollama` service read "only mcp needs it; accessed on db_net so it stays
     off app_net" while the three lines directly below listed both networks.
     Comment rewritten to match, and to explain why both are required
     (orchestrator is app_net-only, so it can only reach a container-hosted
     Ollama on app_net).

  Everything else checked out: all 13 referenced scripts exist, all four
  secrets files are named correctly, and all four published ports
  (3000/8080/8090/8095) match `docker-compose*.yml`.
