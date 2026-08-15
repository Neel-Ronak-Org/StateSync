# Known limitations, stretch items, and open gaps

Phase F Deliverable 8 / F-V8 honesty gate. Every claim in
`docs/metrics-report.md` traces to a measurement recorded somewhere in this
repo; every weak measurement and every unbuilt stretch item is listed here,
not smoothed over. Cross-check: nothing in `docs/metrics-report.md` should
be unsupported by a recorded measurement, and nothing weak should be absent
from this file.

## Stretch items not built (Strategy §6 / Appendix 4)

All eight, per F-V4 (`scripts/verify_seams.py`, 18/18 checks pass, see
`docs/phase_f_log.md`). Every seam exists and resolves from config at a real
dispatch point; the *content* behind each is deliberately unbuilt.

| Stretch item | Seam | Status |
|---|---|---|
| GRPO fine-tuned multilingual model | `normalization.provider = "finetuned"` (`LanguageNormalizer`) | Seam exists (`orchestrator/language_normalizer.py`); only `PromptNormalizer` is implemented. Unbuilt: the fine-tuned model itself. |
| Vision Score | `evaluation.vision_scorer.enabled` (`Scorer` registry, `"vision"`) | Registered (`orchestrator/scorers.py`, `evaluator/scorers.py`); `.score()` raises `NotImplementedError` by design (F-V5 confirms this is the "content unbuilt" failure mode, not "registration missing"). Unbuilt: the VLM call. |
| CSR-RAG hypergraph retrieval | `schema_retriever.provider = "hypergraph"` (`SchemaRetriever`) | Seam exists (`orchestrator/schema_retriever.py`); only `DirectSchemaRetriever` is implemented. Unbuilt: the hypergraph pipeline. |
| Multi-tenancy enforcement | `tenancy.enforced` (MCP session policy layer) | Seam added in Part F (`mcp/session.py::enforce_tenancy_policy`) — the flag existed since Phase B but nothing read it until F-V4 found the gap. Flipping it now raises `TenancyPolicyNotImplemented` per request, a real branch point. Unbuilt: the actual per-tenant isolation policy. |
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
  distinct-result-sets 1.5 > control 1.0 — memory-hit SQL is less reliable
  than a fresh full-schema read on `qwen2.5:7b`. Recorded as BD6 (provisional,
  self-voiding, re-baseline-gated).
- **C2-V1 conditions 3–4: FAIL.** The correction loop never redirects a 2-hop
  join through `orders` on this model, despite a documented, verified prompt
  improvement attempt. Same BD6 disposition.
- **D-V6: FAIL (exact count).** Generated SQL omits the `fulfillments` join +
  `status='delivered'` filter (41 vs. hand-computed 30). Same BD6 disposition;
  mechanism (digipin decode, predicate construction, GIST index) independently
  confirmed correct.
- **M4 (C2-V8, query correctness): low on both paths** — 29.2% cold / 32.0%
  warm on the local model. Not BD6-covered (BD6 is scoped to exactly three
  checks), but the same root cause class (2-hop join / filter omission) — see
  `docs/rebaseline_runbook.md`.
- **M6 (E1-V3, grounding): moderate.** Context precision 0.622, faithfulness
  0.769. One clearly-diagnosed, partially-fixed root cause (aggregate queries
  not echoing an ID column for targeted retrieval); a second, deeper cause
  (a specific entity-name-to-table-mapping failure, "FastShip 1") accepted as
  measured model variance after four distinct, root-caused fix attempts.
- **M7 (E2-V1, chart quality): not yet measured at all.** The Synthesizer
  still emits `chart: null` on every route (Task 10, deferred) — see
  `docs/open_items.md`.

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
  literal re-provisioning.** `README.md` was read end-to-end against
  everything this build actually requires and found stale (still describing
  Phase B only) and incomplete (missing the E1 document-ingestion step and
  the Phase F adversarial-seeding step entirely) — both fixed. A full
  from-scratch bring-up on a genuinely separate machine was not performed
  this session, since doing so from the current machine would require
  destroying already-seeded, verification-relevant state
  (`docker compose down -v`) that this session's own results depend on.
  Recorded honestly rather than claimed as done.
