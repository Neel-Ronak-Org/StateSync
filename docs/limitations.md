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
  `qwen2.5:7b` and non-deterministic in general. **Corrected 2026-08-20
  (audit 004 F-004-4):** this paragraph previously said 8 of the 16 v1
  faithfulness scores changed between M1 and M2 "even though the scoring code
  for that pass is byte-identical," offered as evidence of judge variance. Both
  the count and the framing were wrong. It is **5**, not 8, all five
  `e1_hybrid_*`, and M2 is the **repaired** harness — item 2 above already
  states that its reference-text repair "shares its prompt text with this
  judge call too," so the code producing those five scores was not
  byte-identical to what produced the published run. **The clean same-code
  comparison is M1 against the published run**, both on the *unmodified*
  harness: **0 of 16** v1 faithfulness scores differ, exactly reproducing
  0.622/0.80/0.8125/4. That is the actual noise-band evidence this paragraph
  should have cited, and it argues the opposite of the deleted sentence — the
  0.8125 → 0.8958 move is *more* likely attributable to the A1 repair, not
  less, on the one sample available. Judge variance on the *repaired* harness
  specifically remains genuinely unmeasured — no second M2-equivalent run
  exists — and that gap, not an 8-of-16 drift figure, is the real limitation.

  **The all-sources measurement audit 003's F-003-1 asked for was made, and it
  did not go the direction predicted.** F-003-1 hand-regraded four claims and
  estimated a corrected faithfulness of `0.9375` if the judge were shown the
  SQL rows the synthesizer saw. Cycle 003 built that measurement
  (`mean_faithfulness_all_sources`, item 1 above). It came out **lower** than
  the docs-only score on the same 16 answers: **0.875** against **0.8958**, a
  −4.2pp move concentrated entirely in the hybrid half (0.7917 → 0.7500; the
  doc half is 1.0000 under both rubrics). Two per-query verdicts changed
  (`e1_hybrid_005` down, `e1_hybrid_007` up); the four claims F-003-1
  originally flagged score identically under both rubrics in M2 — they were
  already fixed by the A1 reference repair, not by revealing the SQL. Neither
  score is treated as superseding the other; both are published.

  Full write-up and per-query detail: `docs/audits/003.md`, `docs/audits/004.md`,
  `docs/cycles/003.md`.

- **Every M6 number is measured in a corpus regime where a "chunk" is a whole
  document — the chunker has never split one.** `config/statesync.toml` sets
  `chunk_target_tokens = 512` / `chunk_overlap_tokens = 64`; the largest
  narrative body in the shipped 18-document corpus is **95 words**, so
  `_chunk_words()` (`scripts/ingest.py`) returns a single-element list every
  time it has ever run — verified live: all 18 documents produce exactly one
  narrative chunk (plus one table chunk), 36 chunks total, and
  `chunk_overlap_tokens` has never had an effect on any stored chunk. Context
  recall / precision therefore measure *did retrieval find the right document,
  and within it the right one of two halves* — not passage-level location
  inside a document, because no document in this corpus has an inside. This is
  independent of the M6 defects `docs/audits/003.md` fixed and of corpus
  *size* per se: `V2_STRATEGY.md` §3a frames the weakness as scale (18
  documents / 36 chunks against 1.45M relational rows), which is the same
  framing `docs/cycles/000.md` §3.2 originally gave the whole half before
  audit 003 found the problem was instrumentation, not scale. It bears
  directly on the deferred `olist.order_reviews` ingest (§7 of
  `AGENT_HANDOFF.md`): a review is short, so ingesting 41,755 of them would
  leave the split path exactly as unexercised as 18 PDFs do, buying 1,160×
  scale without buying the thing scale was supposed to test. Not changed here:
  no test exists for the chunker either (`grep -rn "chunk_words\|chunk_overlap"
  tests/` is empty), and none is added by this entry — audit 004 F-004-6.

- **The frontend's citations card lists the first `citation_max_chunks`
  retrievals with no relevance filter against the answer, and (until cycle
  004 C1) called them "Sources."** `build_citations`
  (`orchestrator/nodes/synthesizer.py`) applies no support check: it takes
  `retrievals[:citation_max_chunks]` in retrieval order, full stop. On a
  retrieval miss the card lists whatever was retrieved instead, presented with
  the same confidence as a genuine citation. Live instance: `e1_doc_004` asks
  *"What committed transit time does FastShip 1's SLA annexure specify for the
  Metro Mumbai zone?"* — FastShip 1 is **LP001**; the fixture's own
  `expected_chunk_ids` are `doc_sla_annexure_LP001_c000/c001`; the published
  run's actual `cited` list is `doc_sla_annexure_LP003_c000/c001`,
  `doc_sla_annexure_LP007_c000` — two other logistics providers' contracts, not
  a topic drift but a different company's SLA annexure with a different number
  in it. **Fixed here:** the card's heading is relabelled "Retrieved Context"
  (cycle 004 C1), which stops claiming a support relation the mechanism doesn't
  provide. **Not fixed here, and deliberately:** `build_citations` itself is
  unchanged — filtering what gets listed would alter the exact retrieval set
  M6's `mean_context_precision` measures, which is a metric change wearing a
  UI fix's clothes and is out of this cycle's scope. Audit 004 F-004-5.

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

**A 9.1MB (later 18.4MB) verbatim derivative of this data — `fixtures/
documents/olist_review_digests_manifest.json` — was committed to this
repository for two cycles (005 and the start of 006) before this rule was
checked against it.** Untracked in cycle 006 workstream E (`git rm
--cached`, added to `.gitignore` beside `data/olist/`); the generator is
deterministic and reproduces it byte-for-byte in ~40s, so nothing is lost
by not committing it — see `README.md`'s ingestion section for the exact
regeneration command. **Untracking does NOT purge git history.** The blob
is still reachable from every commit between when it was added and when it
was removed; anyone with access to this repository's history — including
this repository if it is ever made public — can still retrieve it with
`git show <commit>:fixtures/documents/olist_review_digests_manifest.json`
or by checking out an earlier commit. If that matters, it requires a
history rewrite (e.g. `git filter-repo`), which was NOT done here: it
rewrites every commit SHA this project's own documentation cites by
number, and that tradeoff was not this workstream's to make unilaterally
(`docs/audits/006.md` F-006-3, `docs/cycles/006.md` §2 workstream E).

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

## Vector retrieval runs on an approximate index — added cycle 006, found investigating an M6 movement

`document_chunks.embedding` is indexed HNSW (`pg_indexes`:
`USING hnsw (embedding vector_cosine_ops)`) — an **approximate**
nearest-neighbor structure. This was true before cycle 006; nothing in this
cycle changed the index type. What changed is that it is now **measured**,
because cycle 006's B4 re-ingest (adding 7,084 vectors to the existing index)
moved two of M6's sixteen `phase_e1.json` queries' cited chunk lists, where
cycle 005's identical before/after comparison had found an exact null.

**Confirmed the movement is the index, not the review corpus.** Zero review
chunks appear in either affected query's cited list, before or after — the
diffs are entirely among pre-existing baseline documents this cycle never
touched. Confirmed the index is deterministic given a fixed graph (repeating
the same query against the same, current index returns byte-identical
results) — the movement happened at *ingest* time (when the graph changed),
not at *query* time.

**Confirmed, by disabling the index and scoring exactly, that the
approximation is not merely different — it was silently wrong.** For
`e1_hybrid_005`, the true top-3 by exact cosine similarity is `LP007_c001
(0.7532) > LP001_c000 (0.7113) > LP007_c000 (0.6693)`; cycle 005's HNSW
index returned `LP003_c000 (0.6454)` in place of the true rank-2 item —
which happens to be this query's own expected answer. Cycle 006's index,
after the unrelated bulk insert, returns the correct top-3. **This was
never a property of the review ingest being good or bad — it is that this
project's retrieval has been silently approximate the whole time, and
nobody had checked whether HNSW's answers were exact before now.**

**Not uniformly a correction — checked the other direction too, so this
isn't reported one-sided.** For `e1_hybrid_001` (unscored — no ground
truth in this fixture), the true ranking is a genuine near-tie (`S001_c000
0.7143 ≈ S005_c000 0.7137 ≈ S025_c000 0.7134`, all within 0.001, with
`S005_c001` clearly worse at `0.5678`). Cycle 005's index returned the
correct near-tied trio; cycle 006's substituted the one candidate that
scores clearly worse. This specific query carries no scored ground truth
so it moves no published number, but it means the SAME bulk insert made
one query's retrieval more correct and another's less correct — the net
effect on any future query is not predictable from this cycle's evidence.

**Does not establish** that HNSW's approximation error is large or small in
general (two affected queries out of sixteen, on one corpus-size change, is
not a characterization of the index's typical recall); that any PAST M6
number is more or less trustworthy than believed (they were all measured
under the same unexamined approximation, cycle 006 did not re-audit prior
cycles' figures for this); or that switching to exact search is warranted
(HNSW's speed/recall tradeoff is a deliberate, common choice, and nothing
here measures whether the tradeoff is wrong for this corpus size). What it
does establish: **a corpus-size change can move retrieval results for
queries that have nothing to do with the added content**, which is a
property future cycles adding to this corpus should expect, not be
surprised by. Full investigation, with the exact-score transcripts:
`docs/cycles/006.md` §4, "B6 — M6 re-run."

## The review-digest corpus: what it can and cannot support (added cycle 005)

Cycle 005 ingested 2,567 per-seller documents (13,125 chunks) built from
`olist.order_reviews`, under `documents.doc_type='review_digest'`, into the
**same** retrieval space the 18-document/36-chunk baseline corpus already
occupies (`tenant_id='t_capstone_default'` — see `docs/cycles/005.md`
DEVIATION 1 for why a genuinely separate tenant would have made the corpus
unretrievable). Three things this corpus does **not** give the system, stated
before anyone reads a number and assumes otherwise:

**1 — It is Portuguese, verbatim, and this does not make the system support
Portuguese-language questions.** `review_comment_message` is reproduced
unsummarised and untranslated (`scripts/gen_olist_review_digests.py`); this
was a deliberate design choice, not an oversight — see the generator's own
docstring. Adding a corpus in a language does not add a language-handling
capability. No routing, normalization, or synthesis code was touched.

**2 — There is no ground truth for review-based questions**, and none was
written this cycle. `fixtures/golden/phase_e1.json` — the fixture M6 has
always been measured against — was authored against the 18-document baseline
and asks nothing about reviews, seller sentiment, or complaint patterns. A
future cycle that wants to measure retrieval or answer QUALITY over the
review corpus needs a new fixture with new expected chunk IDs and expected
claims, authored the way `phase_e1.json` was: by someone who can verify the
expected answer against the source. Building that fixture in the same cycle
as the ingest is exactly the "tune against what you report from" pattern this
project's own §A0.4 forbids, and cycle 003 already made this call once before
for the same reason (see "Explicitly NOT this cycle" in cycle 003's and
cycle 005's plans) — deliberately deferred again here.

**3 — M6 after this ingest measures INTERFERENCE, not review-retrieval
quality**, and the measured answer is a clean null. Both arms below ran the
unmodified `phase_e1.json` — 16 queries with zero review-related content —
once against the 18/36 baseline and once against the enlarged 2,585/13,161
corpus, same flags, same code:

| | before (18 docs / 36 chunks) | after (2,585 docs / 13,161 chunks) |
|---|---|---|
| `mean_context_precision` | 0.6222 | 0.6222 |
| `mean_context_precision_over_retrieved` | 0.3639 | 0.3639 |
| `mean_context_recall` | 0.80 | 0.80 |
| `mean_faithfulness` | 0.8958 | 0.8958 |
| every query's `cited` chunk list | — | **byte-identical to before, all 16/16** |
| review_digest chunks ever retrieved by any of the 16 queries | — | **0** |

Not a rounding coincidence — verified at the per-query, per-chunk-ID level
(`docs/cycles/005.md` C6/C7). ~365× more competing vectors in the same
tenant's retrieval space did not move a single score or a single retrieved
chunk ID. The plan predicted the opposite (*"I expect context precision to
fall"*) and committed no criterion requiring it to; it didn't, and that is
reported as measured, not adjusted after the fact.

**Read plainly, not overclaimed.** This says the review corpus and the E1
fixture's topics (English SLA/penalty/dispute-resolution policy) are far
enough apart in `mxbai-embed-large`'s embedding space that top-k retrieval
never crosses between them **for these 16 specific queries, which were never
designed to probe cross-topic interference**. It does **not** establish that
retrieval is robust to scale in general, that a review-adjacent English query
would retrieve correctly, or that the chunker's now-exercised split path (see
below) produces chunks of good retrieval quality — none of that was measured.

**What the ingest DID newly exercise, measured directly against the
database — corrected 2026-08-23 (cycle 006 A1, audit 006 F-006-1/F-006-1a).**
The published figure here previously read *"1,072 of the 2,567 review
documents produced more than one chunk"* and attributed all of it to
`_chunk_words` finally splitting a document (F-004-6). That count is real but
was measured over the wrong population and attributed to the wrong mechanism.
Measured against all 2,585 documents in the corpus (18 baseline + 2,567
review), it decomposes three ways:

| documents | split by | pre-existing? |
|---|---|---|
| **18** | `_extract_document_chunks`'s table/narrative separation — one table chunk, one narrative chunk each | **Yes — multi-chunk since before this cycle. These are F-004-6's own subject.** |
| **705** | `_embed_allowing_split`, the context-length fallback (see below), on digests that were ≤512 words | No |
| **349** | `_chunk_words`, on digests over the 512-word target | No |

So `_chunk_words` really did split a document for the first time this cycle —
**349**, not 1,072 — and the 18 baseline documents were never evidence of
that: they were multi-chunk *before* this ingest, and are literally the corpus
F-004-6 diagnosed as "the chunker has never chunked." A test pinning this
decomposition lives at `tests/test_review_chunk_split_attribution.py`.

**This table went through two corrections, not one, and the second was caught
building that test, not by a fresh audit — recorded here in full rather than
silently fixed.** The first correction (immediately above) fixed the
population and the attribution. Building the pinning test then surfaced that
its own 313/741 split was *itself* measured wrong: the generator's diagnostic
print computed word count as `content_md.count(" ") + 1`, a literal-space
count, while `_chunk_words` — the function the claim is about — tokenises
with `str.split()`, which also treats a newline as a word boundary. Digest
text is newline-heavy (one line per review header, one per body), so the
proxy undercounts. Calling `_chunk_words` directly against the same manifest
text — which is what the pinning test does, not the generator's diagnostic —
gives the table's real figures, **349** and **705**, confirmed an exact
subset of the 1,054 review documents the live database shows split
(`real_split ⊆ db_multi`, `|real_split| = 349`). The generator's diagnostic
now tokenises the same way `_chunk_words` does
(`scripts/gen_olist_review_digests.py`, cycle 006 A2/A3), so it cannot drift
from the function it describes again. 18 + 349 + 705 = 1,072 exactly — the
arithmetic behind the original published figure was never wrong; only its
attribution, twice now. Full transcript of both corrections:
`docs/audits/006.md` F-006-1a and its second amendment.

Separately, `mxbai-embed-large`'s real context limit (512 tokens, `ollama show
mxbai-embed-large` → `bert.context_length=512`) is smaller than
`_chunk_words`' 512-WORD approximation for this text — Portuguese subword
tokenization is denser than the approximation assumes, confirmed live (a
1,709-word digest's first chunk was rejected outright by the embedding call,
HTTP 500, `"the input length exceeds the context length"`). 2,119 of the
corpus's *input* chunks needed the recursive halving fallback added to handle
this (`scripts/ingest.py::_embed_allowing_split`) before they would embed.
**Splitting — by either mechanism — is not a rare exception to `_chunk_words`'
single-whole-document norm; it is the dominant regime for this corpus.**
Measured directly (audit 006 F-006-1): no chunk in the persisted corpus
exceeds 154 words against the 512-token target, and 11,612 of 13,125 chunks
(88.5%) sit in a document that ended up with more than one chunk — 705 of
those 1,054 documents split only via the fallback, 349 via `_chunk_words`
itself (the table above). That is a materially larger share of the corpus
than the 16%-of-input-chunks figure cycle 005's own confidence section
estimated for the fallback specifically (`docs/cycles/005.md` §5.3 — left as
written there; cycle files are the historical record and are not edited
retroactively, per this file's own practice). Neither path has a quality measurement — only "it eventually
embedded successfully" is verified, not "the resulting chunk boundary is a
good one" — and `docs/audits/006.md` F-006-2 found the boundaries are not
good: 94.9% of continuation chunks begin mid-review. See "Addressability"
below for what cycle 006 did about it.

**Reversible by construction, demonstrated not asserted.** Because the corpus
carries `doc_type='review_digest'` and no baseline document has ever used that
value, `DELETE FROM document_chunks WHERE document_id IN (SELECT document_id
FROM documents WHERE doc_type='review_digest')` followed by `DELETE FROM
documents WHERE doc_type='review_digest'` removes exactly the added corpus and
nothing else — run live this cycle (`DELETE 13125` then `DELETE 2567`),
verified immediately after at exactly 18 documents / 36 chunks, then
re-ingested to restore it. See `docs/cycles/005.md` C5. **Re-demonstrated
2026-08-23 against the re-chunked corpus below (cycle 006 B4): `DELETE 20209`
then `DELETE 2567`, verified back to exactly 18/36, restored by re-running
the same ingest — identical mechanism, identical result, on a corpus with a
completely different internal chunk structure.**

### Addressability — the corpus was retrievable but not addressable; fixed, verified, and only partly effective (cycle 006 B)

Audit 006 F-006-2 found that the corpus above, while genuinely retrievable
(a review-topical query's top-8 results were dominated by review chunks),
could not answer the one question shape a per-seller digest exists to
answer: a query naming a seller's exact id ranked that seller's own digest
**2,365th of 13,161** chunks. Root cause, measured directly: **80.4% of
chunks carried no occurrence of their own seller id** (only `ordinal=0`
did), and **94.9% of continuation chunks began mid-review**.

**What changed.** `scripts/gen_olist_review_digests.py::_digest_sections`
now packs each seller's reviews into sections that (a) never split a single
review's header+body across a boundary and (b) repeat the seller-identity
header on every section — replacing the single whole-blob `content_md` that
`ingest.py`'s generic `_chunk_words` chunked blindly. `ingest.py` gained a
matching `has_sections` path with a review-boundary-aware fallback
(`_embed_one_review_section`) for the rare section that still exceeds the
embedding model's real context length. Full mechanism and the performance
fix this required: `docs/cycles/006.md` §3 DEVIATION 2, §4.

**Both target properties verified directly against the database, at
100%, not inferred from the code.** Post re-ingest: 20,209/20,209 chunks
(100%) contain their own seller id; 0/20,209 chunks (0%) begin mid-review.
The corpus grew from 13,125 to 20,209 chunks (2,567 documents unchanged) —
every chunk is now smaller (mean 48 words, max 63, vs. the prior mean 69 /
max 154) and self-identifying.

**The retrieval-rank fix is real but partial, exactly as predicted before
the scope was chosen.** A fixed, pre-committed set of 20 seller-id probes
(`fixtures/golden/review_probe_set.json`, `scripts/gen_review_probe_set.py`)
measured rank before and after
(`docs/review_probe_rank_before.json` / `_after.json`). **0/20 probes rank
within `semantic_top_k=8` in either arm** — the property a user would
actually notice (does the seller's digest show up) is unchanged. Median
rank did improve materially (597 → 298), but this splits cleanly by review
count: **11/11 low-review-count probes (≤5 reviews) got WORSE** (they were
already single, fully-identified chunks; they only inherit the larger,
54%-bigger retrieval space's extra competition), while **7/9
high-review-count probes (>5 reviews) got BETTER** (they previously had
almost no identified chunks at all; now every one of theirs is). Full
per-probe numbers and the mechanism: `docs/cycles/006.md` §4.

**What this does NOT establish, stated so nobody reads more into it than
was measured:**

- **The 20 probes are a mechanical rank check, not a quality fixture.**
  There is no answer text, no expected claim, no judge involved — the
  "ground truth" is the `document_id` the generator already assigned by
  construction. This was a deliberate scope choice (`docs/cycles/006.md`
  §2, "Explicitly NOT this cycle") for the same reason cycles 003 and 005
  declined to write review-question ground truth in the same cycle as a
  corpus change: it would be tuning against what is reported from.
- **No review-topic answer QUALITY was measured**, this cycle or any
  before it. Addressability (can a retrieved chunk be attributed to its
  seller, does it respect a review's boundary) is a different property
  from answer quality (does the system correctly answer a question about
  a seller's reviews), and nothing here measures the second.
- **The `top_k=8` shortfall is not further diagnosed.** This cycle did not
  investigate whether a larger `semantic_top_k`, a different retrieval
  strategy (e.g. filtering by seller before searching within), or a
  different embedding model would close the remaining gap — B was
  deliberately scoped to the ingestion seam, not new retrieval features.
- **The M6 interference finding above (the null result) was itself
  disturbed by this cycle's re-ingest**, but not by review-topic content —
  see the HNSW section earlier in this file for what actually moved and
  why.

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
