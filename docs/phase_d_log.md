# Phase D — Multilingual & Spatial: completion log

Date: 2026-08-15. Builds on Phase C2 (4d2def4).

## Graph changes

```
entry -> normalize -> supervisor -> [routing dispatch, unchanged] -> ...
```

`normalize` is a new, real graph node (`nodes/normalize.py`), wired by two
direct (non-conditional) edges: `entry -> normalize` and `normalize ->
supervisor`. It always runs before the Supervisor, and always sets
`normalized_query` (even with `normalization.enabled=false`, it sets
`normalized_query = raw_query` rather than leaving it `None`) — which is
what makes Callout 1 ("Supervisor never sees raw_query") structural rather
than conventional. `supervisor_node` was changed to read `normalized_query`
only, with a hard assertion if it's ever unset, in place of the previous
`normalized_query or raw_query` fallback that would have silently masked a
wiring defect.

Per Callout 2, `DigipinResolver` (`spatial_resolver.py`) is **not** a graph
node — it's called inline inside `sql_agent_node`, every attempt (cheap,
deterministic, no LLM/MCP round-trip, unlike memory/interrogation which stay
gated to attempt 0). The routing table still resolves to exactly 3 routes
(D-V7).

## Deliverables

1. `LanguageNormalizer` protocol + `PromptNormalizer` (`language_normalizer.py`),
   resolved via `normalization.provider`. One LLM call per query (new
   `"normalizer"` role, `llm/provider.py`): detects language + confidence;
   above `detect_confidence_min` and only for hi/hinglish, rewrites to
   English while carrying every proper noun through byte-identical into
   `preserved_entities` as a self-mapping, in whatever script it appeared.
2. Gate wired ahead of Supervisor routing (above).
3. `normalization.enabled` flag: disabled means no `PromptNormalizer` call
   at all (a true control condition, not "call and discard" — matching how
   `memory.enabled` gates `memory.get_trajectory`), not just an ignored result.
4. `SpatialResolver` protocol + `DigipinResolver` (`spatial_resolver.py`),
   called inline in `sql_agent_node`. Finds a candidate token via the
   library's own 16-character grid charset, decodes only through the real
   library (never a hand-rolled regex-charset guess standing in for
   `decode()` itself), extracts a stated radius or falls back to
   `spatial.default_radius_m`.
5. Spatial predicate construction: `DigipinResolver` builds the ready-made
   `ST_DWithin(locations.geom, ST_SetSRID(ST_MakePoint(lon,lat),4326)::geography,
   radius)` text and injects it into the SQL Agent's prompt as an
   instruction, rather than trusting the model to get the SRID/geography
   cast right unprompted (C2 already showed it doesn't reliably — the
   `spatial_invalid` category). GIST index usage confirmed in D-V6.
6. `fixtures/golden/phase_d_multilingual.json` — 40 pairs (20 Hindi
   Devanagari, 20 Hinglish), each with an English equivalent and
   live-execution-frozen `ground_truth_sql`. 32/40 contain a proper noun
   (>= 15 required).
7. `fixtures/digipin_reference.json` — 20 pairs, 13 distinct cities (>= 6),
   entry 0 has 3 nearby locations (>= 3 required, and specifically at index
   0 since D-V6 reads "the first entry" literally) — regenerated with the
   real pinned library (see "Bugs found," GAP-4).
8. `fixtures/golden/phase_d_spatial.json` — 15 spatial queries against real
   `digipin_reference.json` pairs, live-execution-frozen ground truth.
9. `scripts/bench_multilingual.py` — control-vs-treatment + entity
   preservation scoring (D-V1/D-V2/D-V3 data source).

## Verification results

**D-V1 — Multilingual lift (M3). PASS.**
Control (`normalization.enabled=false`): **6/40 (15.0%)**.
Treatment (`normalization.enabled=true`): **25/40 (62.5%)**.
**Delta: +47.5 pp.** English reference line: 16/20 (80.0%) — treatment
doesn't fully close the gap to English, which the spec explicitly does not
require. Comparison is execution-based dataframe equality, value-only
(column names/order ignored, same definition as C1-V3/C2-V8), against
ground truth computed once per query id via direct `mcp` execution and
reused across every language variant of the same question.

**D-V2 — Entity preservation (M3, second half). PASS, with two real bugs
found and fixed along the way.**
Final rate: **27/32 (84.4%)**, scored strictly per spec — a query whose
entity was translated fails even if `preserved_entities` shows *some*
mapping and even if the SQL result happened to be correct.

Two genuine plumbing bugs were found and fixed, not just accepted as model
variance (see "Bugs found" below for detail): (1) the SQL Agent's
entity-preservation prompt hint was one-sided (told the model what to do
for Devanagari values, said nothing for Latin-script ones, which the model
then also routed to the Devanagari `_local` column); (2) the normalizer's
own JSON output mapped entities to an English gloss (`{"मुंबई": "Mumbai"}`)
instead of a true self-map about 40% of the time, fixed with a worked
example in the prompt (measured before/after on the same 32 queries:
**59.4% -> 84.4%**, in two bench runs, not a single sample).

Remaining 5 failures (चेन्नई, Jaipur, Lucknow, Patna, Indore) were
investigated individually, not just re-labeled "model limitation" on
sight: 2 of the 5 turned out to be the normalizer's own confidence score
falling below `detect_confidence_min` for short city-name-only Hinglish
sentences, correctly triggering true pass-through (no rewrite) — but the
model's raw JSON also hadn't extracted any entity in those specific cases,
so a follow-up fix (carry through the model's own entity extraction even
on the pass-through path, substring-sanity-filtered) is now in place and is
strictly beneficial, but empirically didn't move these 2 cases since there
was nothing to carry. The other cases showed the opposite direction of the
first bug (a Latin-script entity transliterated into incorrect Devanagari
despite explicit instructions not to). Accepted as measured local-model
variance after two targeted, verified-effective prompt fixes — consistent
with this build's standing practice of one well-reasoned improvement
attempt before documenting a capability ceiling, not chasing every
remaining point.

**D-V3 — English is untouched. PASS.**
All 20 English-equivalent queries: `normalized_query == raw_query` under
treatment, gate logs itself as skipped, result sets identical in row count
between control and treatment (two had 0 rows in both — also identical).

**D-V4 — DIGIPIN round-trip (primary decode test). PASS.**
**200/200** within the 4.0m tolerance (`spatial.roundtrip_tolerance_m`).
Max observed round-trip distance: 2.542m. Sampled within the pinned
library's own bounding box (`minLat=2.5, maxLat=38.5, minLon=63.5,
maxLon=99.5` — the library's own definition of the encodable region, not an
independently invented one), seeded from `spatial.roundtrip_rng_seed`.

**D-V5 — Frozen reference pairs. PASS, after fixing a real fixture bug.**
**20/20** match to 6 decimal places. The first attempt scored 0/20 (~2m-scale
differences on every pair) — root-caused, not dismissed: `decode()` returns
the center of the grid cell containing the input point, not an exact
inverse of `encode()`, so freezing the pre-encode coordinates could never
pass this check by construction, for any correct implementation. Fixed by
freezing `decode(encode(lat, lon))`'s own output as the regression
baseline (see GAP-4) — which is what "a mismatch means the library or
config drifted" is actually testing.

**D-V6 — Spatial aggregation worked example. Mechanics PASS in full;
overall exact-count condition fails on an orthogonal, pre-existing gap —
reported honestly rather than narrowed to fit.**
Reference: `fixtures/digipin_reference.json` entry 0 (digipin `39J43799LK`,
decoded 28.611807/77.207659, 3 nearby seeded locations).
Independent hand-written ground truth (direct `statesync_reader` connection,
since Layer 1 rejects `EXPLAIN` through `data.run_select` and this is also
the only way to check the GIST index): **30** delivered orders within 5km.
- `spatial.digipin`/`lat`/`lon`: exact match. **PASS**
- Generated SQL uses `ST_DWithin` over `geography` with radius `5000`: **PASS**
- `EXPLAIN` shows `Index Scan using locations_geom_gix` (not `Seq Scan`): **PASS**
- English and Hinglish forms return identical counts to each other: **PASS**
  (proves no normalization-specific regression — the gap below affects both
  equally)
- Returned count equals the hand-computed count exactly: **FAIL** — both
  forms returned 41, not 30.

Root cause, confirmed rather than assumed: the LLM's generated SQL omitted
the `fulfillments` join and `status = 'delivered'` filter that the word
"delivered" in the question requires — a general SQL-comprehension gap, not
a spatial or multilingual defect. Confirmed by re-running the *ground
truth* query with the fulfillments/status filter removed (matching the
LLM's actual SQL shape): it also returns 41, exactly matching the LLM's
count. This is the same class of SQL-generation imprecision already
extensively measured in C2 (M4 ~30-60% range across different runs), not
something Part D introduced — Part D's own additions (digipin decode,
predicate construction, GIST usage, bilingual parity) are independently
confirmed exact.

**D-V7 — Spatial did not become a route. PASS.**
Exactly 3 registered routes (`sql`, `docs`, `hybrid`) in the routing table
registry — confirmed via `graph.py`'s `register()` calls, unchanged from
Phase B. No node named for geocoding/spatial exists; `normalize` (language)
and the 8 total `add_node()` calls are unrelated to spatial routing.
`DigipinResolver` is a plain function call inside `sql_agent_node`, not
registered anywhere.

## Bugs found and fixed (during this phase, not pre-existing scope)

1. **Pre-existing Phase B fixture gaps, only surfaced once the real digipin
   library was pinned** — see `docs/spec-gaps.md` GAP-4 for full detail:
   stub-encoded seed digipins (Appendix 2.1 required the pinned library from
   the start), and seed location density too sparse to ever satisfy
   Appendix 2.3's clustering requirement. Both fixed; regeneration confirmed
   via diff to touch only what was necessary (locations.digipin/lat/lon plus
   one added hub row, zero change to any other table or FK assignment).
2. **`digipin_reference.json` froze pre-encode input coordinates instead of
   `decode()`'s own output** (D-V5, detailed above). A generator bug, not a
   library or data problem.
3. **Entity-preservation column-selection bug** (sql_agent.py): the prompt
   hint told the model to route Devanagari values to `_local` columns but
   said nothing about the Latin-script case, so the model sometimes routed
   *those* to `_local` columns too (e.g. `WHERE s.display_name_local =
   'Seller Store 5'`, which matches nothing). Fixed by making both
   directions of the instruction explicit. Verified via direct query
   before/after.
4. **Normalizer self-mapping bug** (language_normalizer.py): the model's own
   JSON response mapped preserved entities to a translated/glossed value
   instead of a true self-map about 40% of the time. Fixed with a worked
   example demonstrating the exact failure mode. Verified via a full bench
   re-run (59.4% -> 84.4%), not a spot check.
5. **Docker registry connectivity stall** — infrastructure, not a code
   defect: `docker compose build`/`docker pull` hung on registry metadata
   resolution for an extended period on the host. Worked around by
   live-patching the running container (source isn't bind-mounted, only
   `config/` is) so verification could continue; once connectivity
   recovered on its own, did a clean rebuild and redeployed before the
   final regression run, so nothing shipped depends on the workaround.

## Regression check

`scripts/lint-no-literals.sh`: PASS (40 files, 0 violations).
`scripts/verify_b.sh`: **28/28** structural checks pass, on a freshly
rebuilt image with `memory.enabled=true` and `normalization.enabled=true`
(production defaults). B-V1's real per-query golden rate this run: **9/10**
(`b_008` failed: 10 rows returned vs 1 expected) — consistent with the
run-to-run variance already documented in the C1/C2 logs (this local
model's baseline has ranged 7/10-10/10 across runs previously), not a new
regression introduced by Phase D.

---

# M3 separation run (post-Phase-F, 2026-08-15)

**Appended, not edited.** Everything above is the Phase D record as measured
at the time and stands unchanged. This section records a controlled experiment
run after the `v1.0.0-phase-f` freeze to identify why M3 regressed between
Phase D and Phase F's F-V3 re-verification.

## The question

F-V3 measured M3 lower than Phase D on both halves, with the **English
reference line** — queries the normalization gate provably leaves byte-identical
(D-V3: 20/20 unchanged in every run) — falling 80% → 45%. An English-only drop
cannot originate in the multilingual path, so the cause had to be upstream of
or beneath normalization. Two candidates were carried in `docs/open_items.md`:

- **Candidate 1** — the `memory.enabled` default flip `true`→`false`
  (gap-packet Task 3). Phase D measured M3 with memory ON.
- **Candidate 2** — draft-first interrogation targeting (gap-packet Task 4),
  which added an LLM call and changed which tables get sampled on the miss path.

## Method

`scripts/bench_multilingual.py` run twice, unmodified, back to back on the same
machine, changing exactly one thing: `memory.enabled`. Everything else — fixture,
seed data, model (`qwen2.5:7b`), image, `normalization.enabled` toggling (the
script does that itself) — held constant. Flags were explicitly reset before
each run rather than inherited, which is the bug that contaminated the original
F-V3 measurement (see `docs/phase_f_log.md`).

## Result

| | control | treatment | delta | English ref | entity pres. | English no-SQL | English wrong-SQL |
|---|---|---|---|---|---|---|---|
| **Phase D (on record)** | 6/40 (15.0%) | 25/40 (62.5%) | **+47.5pp** | **16/20 (80%)** | **27/32 (84.4%)** | — | — |
| memory=OFF (shipped default) | 13/40 (32.5%) | 23/40 (57.5%) | +25.0pp | 9/20 (45%) | 18/32 (56.2%) | 4 | 7 |
| memory=ON (pre-gap default) | 5/40 (12.5%) | 17/40 (42.5%) | +30.0pp | 5/20 (25%) | 14/32 (43.8%) | 4 | 11 |

## Conclusions

**Candidate 1 is REFUTED.** Restoring `memory.enabled=true` does not recover the
numbers — it makes every absolute measure *worse*: English reference 45%→25%,
treatment accuracy 57.5%→42.5%, entity preservation 56.2%→43.8%. The memory
default flip is not the cause of the M3 regression, and the shipped default
(`false`) is the better of the two configurations on this model. This
independently re-confirms BD6's own finding that hit-path SQL is less reliable
than miss-path SQL here.

**Read the delta with care.** The `+30.0pp` figure for memory=ON is *higher*
than memory=OFF's `+25.0pp`, which looks like an improvement and is not: the
control arm degraded faster (32.5%→12.5%) than the treatment arm
(57.5%→42.5%). A control-vs-treatment delta is the right measure for D-V1's own
pass condition, but it is the wrong measure for diagnosing a regression —
absolute treatment accuracy and the English reference line are what matter here.

**A third cause was found that neither candidate covers, and it is confirmed
deterministic.** In *both* arms, the same four English queries produced no
`executed_sql` at all — meaning they never took the `sql` route:

- `d_ml_005_en` / `d_ml_006_en` — "What category is Seller Store *N* in?"
- `d_ml_009_en` / `d_ml_010_en` — "What is Seller Store *N*'s contracted SLA in hours?"

Verified directly against the live orchestrator: both classes return
`route="docs"`, `executed_sql=None`. Both are plainly relational — `category`
and `contracted_sla_hours` are **columns on the `sellers` table** (A5), and
neither value exists in any document. This is the **same defect already tracked
as `c2g_10`**: E1's Supervisor prompt rewrite removed Phase B's "default to sql"
bias, and document-flavoured vocabulary ("SLA", "category") now pulls plainly
relational questions to the document route.

4 of 20 English queries = **20 percentage points of the 35-point English drop**,
attributable to routing alone, in both arms, identically. The remaining ~15
points are SQL-generation quality on queries that *did* route correctly (7
wrong-SQL with memory off).

**Candidate 2 remains unresolved and is now the only live hypothesis for the
residual.** Separating it requires a third condition (miss path *without*
draft-first targeting), which needs a code-level toggle that does not exist.
Not guessed at here.

## Decision: keep `memory.enabled = false`

Per `docs/rebaseline_runbook.md` §4, the shipped default is re-decided when
evidence arrives. Evidence arrived: memory ON is worse on this model across
every absolute measure. **The default stays `false`.** Recorded as a decision
rather than left implicit — and it is still only a *local-model* decision; the
runbook re-decides it again on the graded model, where BD6 may resolve
differently.

## Artifacts

- `docs/bench_multilingual_report.json` — the memory=OFF run (shipped config;
  canonical).
- `docs/m3_separation_memory_on_report.json` — the memory=ON run, preserved
  for comparison.

## Caveat recorded honestly

The qdrant trajectory store has accumulated trajectories from many hours of
runs, including failed ones. The memory=ON arm retrieves from that store, so it
is not a clean replication of Phase D's memory=ON conditions — it is a
measurement of *what flipping the default would do today*, which is exactly the
question §4 asks. A cleaner replication would reset the collection first; that
was not done, because the decision it informs (don't flip the default) is
unchanged either way.

---

## M3 re-measurement after the Supervisor routing fix (2026-08-15, F-V3 run)

Run as part of the full F-V3 sequence (flags reset to shipped defaults before
the step, so no leakage from `check_c2v8.py` — the bug that invalidated an
earlier run).

| | Phase D (record) | Phase F (pre-fix) | **Post-fix** |
|---|---|---|---|
| D-V1 control | 6/40 (15.0%) | 13/40 (32.5%) | **16/40 (40.0%)** |
| D-V1 treatment | 25/40 (62.5%) | 23/40 (57.5%) | **26/40 (65.0%)** |
| D-V1 lift | +47.5pp | +25.0pp | **+25.0pp** |
| D-V2 entity preservation | 27/32 (84.4%) | 18/32 (56.2%) | **26/32 (81.25%)** |
| English reference line | 16/20 (80.0%) | 9/20 (45.0%) | **12/20 (60.0%)** |
| D-V3 English untouched | 20/20 | 20/20 | **20/20** |

### The prediction, and how it did

Recorded in `docs/open_items.md` **before** this run, so it could be wrong:
the `c2g_10` routing defect was costing ~20 of the 35 lost English points, so
the English line should go 9/20 → roughly 13/20.

**Actual: 12/20.** Directionally right, slightly optimistic. Routing was worth
**~15pp**, not ~20pp; the unattributed residual is therefore ~20pp rather than
the ~15pp previously estimated. The estimate is corrected here rather than
quietly restated.

### D-V2's regression is closed

56.2% → **81.25%**, within run-to-run noise of the 84.4% on record. This was
the more alarming half of the regression (entity preservation is what D's
whole normalization design exists to protect) and it is effectively recovered.

### Why the lift stayed at +25pp — and why that is not a failure

The obvious reading is "the fix didn't restore the lift". The data says
something more specific:

    control    13 → 16   (+3 queries)
    treatment  23 → 26   (+3 queries)

**Exactly +3 in each arm.** Routing happens *upstream of* normalization —
`normalize_node` runs before `supervisor_node`, and the routing decision is
independent of which arm is active — so a routing fix necessarily helps both
arms equally and leaves the *difference* between them untouched. The lift
metric was never what the routing bug damaged; the absolute accuracies were.

Following that through changes the interpretation of the "M3 regression"
itself. Comparing post-fix against the Phase D record:

- **treatment 65.0% > 62.5%** — the arm that actually ships is at its highest
  measured value, above Phase D;
- **control 40.0% >> 15.0%** — the no-normalization baseline improved by 25
  points since Phase D;
- the lift is smaller **because the control arm got better, not because the
  treatment arm got worse.**

A shrinking lift over a rising baseline is not the same defect as a falling
treatment arm, and reporting it as "M3 regressed" without that distinction
would be misleading. What legitimately remains below the Phase D record is the
**English reference line (60% vs 80%)**, and that is the residual worth
chasing — it is unaffected by normalization (D-V3: 20/20 unchanged), so it
sits in SQL generation, not in the multilingual path.

Candidate 2 (draft-first interrogation) remains the only live hypothesis for
that residual, and isolating it still needs a miss-path-without-draft-first
condition that has no toggle today. Not guessed at.

---

## Schema column semantics: a hypothesis, a measurement, and a revert (2026-08-16)

**The hypothesis, and why it looked strong.** The English reference line's 8
misses were read individually rather than treated as noise. Four were one
cluster: the model filtering `locations.city_local` (local-script) for an
English city name, which cannot match. Two more measured delivery delay from
`orders.placed_at` instead of against the SLA deadline `fulfillments.promised_at`.

The cause looked structural and cheap to fix. `mcp/tools/schema_tools.py::
describe_table` has selected `col_description(...)` and returned it as each
column's `comment` since Phase B, and the SchemaRetriever passes it to the SQL
Agent — but **every comment in the database was NULL**. A semantic channel had
been wired end to end and never populated. Migration 005 filled it for 22
genuinely ambiguous columns.

Verified D4-safe before running anything: comments are not fingerprint inputs,
and `cf4caa5c…` was identical before, after, and after the revert.

**Spot-checks passed.** `city = 'Bengaluru'` replaced `city_local = 'Bengaluru'`;
delay became `delivered_at - promised_at`. Two of three probe queries fixed.

**Then it was measured, and it was worse.**

| | before 005 | after 005 |
|---|---|---|
| D-V1 control | 40.0% | 50.0% |
| **D-V1 treatment** | **65.0%** | **55.0%** |
| D-V1 lift | +25.0pp | +5.0pp |
| **D-V2 entity preservation** | **81.25%** | **62.5%** |
| English reference | 60.0% | 55.0% |
| M4 cold (independent fixture) | 28.0% | 28.0% |
| M4 warm | 32.0% | 24.0% |

**The mechanism is visible in the D-V2 detail, not inferred.** `दिल्ली`,
`बेंगलुरु` and `चेन्नई` all flipped to `preserved=False`. The comment *"an
English city name will never match city_local"* reads to the model as *"prefer
the English column"* — so for a Hindi query naming `दिल्ली` it transliterates to
`'Delhi'` and filters `city`. That **destroys the preserved entity**, which is
exactly what D-V2 measures and exactly what Phase D's normalization design
exists to protect.

The change bought a small English gain by damaging the multilingual path. On
the arm that actually ships it is a 10-point regression, and on entity
preservation nearly 19.

**Reverted (migration 006), not re-tuned.** Rewriting the comments to be
script-neutral is a plausible fix — but it is a hypothesis, and this exact
hypothesis has already been wrong once at a cost of two 30-minute
measurements. Shipping an unvalidated revision of a change measured as harmful
would repeat the error with more confidence and no more evidence.

**What this run actually bought**, since the code change is gone:
1. A **mechanism**, not a guess, for part of the English residual: the model
   cannot tell `city` from `city_local` and nothing tells it.
2. A **live demonstration** that schema comments reach the model and change
   its SQL — the channel works; the wording was wrong.
3. A **warning about single-axis optimisation**: the English line and entity
   preservation pull in opposite directions on the same column pair. Any fix
   here must be measured on both halves or it will trade one for the other
   silently. The spot-checks looked fine precisely because they only tested
   the English half.
4. Confirmation that **M4's fixture is a weak holdout for this class** — only
   ~5 of 25 C2 queries touch city/delay/category, so it cannot resolve the
   question either way.

Recorded as an open item with the hypothesis stated, so the next attempt
starts from this evidence rather than from the same intuition.
