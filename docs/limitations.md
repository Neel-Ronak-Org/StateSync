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
| Vision Score | `evaluation.vision_scorer.enabled` (`Scorer` registry, `"vision"`) | **Half built.** Registered in both scorer modules; the **rendering half is implemented and deployed** — `vision.render_spec_to_png()` uses `vl-convert` (self-contained Rust, no browser, no network, so the evaluator keeps working without an outbound path per A1). Two distinct failure modes so a half-built feature cannot masquerade as unbuilt: `ChartRenderUnavailable` (no renderer) vs `VisionModelUnavailable` (no VLM). **Unbuilt: the VLM judging call itself**, which needs a vision-capable model — the local stack has none. |
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
- **M6 (E1-V3, grounding): moderate.** Context precision 0.622, faithfulness
  0.8125 (F-V3 run; 0.769 at E1, 0.831 at the first Phase F run). One clearly-diagnosed, partially-fixed root cause (aggregate queries
  not echoing an ID column for targeted retrieval); a second, deeper cause
  (a specific entity-name-to-table-mapping failure, "FastShip 1") accepted as
  measured model variance after four distinct, root-caused fix attempts.
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
