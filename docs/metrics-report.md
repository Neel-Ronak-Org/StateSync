# Metrics report — M1 through M10

Phase F Deliverable 7. Every value as measured, with procedure and date.
Per Strategy §7.0: **model-independent** metrics stand as measured on any
model and are reported as final here. **Model-sensitive** metrics are
measured on the local model (`qwen2.5:7b`) but are **local, provisional**
until the graded (Anthropic) re-baseline runs (`docs/rebaseline_runbook.md`)
— never overwritten, only appended to, per the runbook's own §5 rule.

Model-sensitive metrics below carry two columns: **Local (provisional)** —
this build's own measured number — and **Graded (pending)** — filled in only
after `docs/rebaseline_runbook.md` executes. An empty Graded column is not a
missing measurement; it is the honest state of a metric that has not yet
been re-baselined.

Date: 2026-08-15. Local model: `qwen2.5:7b` (generation), `mxbai-embed-large`
(embeddings), via the BD1 provider seam (`flags.llm.provider = "local"`).

---

## Model-independent metrics (final as measured)

| Metric | Value | Procedure | Verified |
|---|---|---|---|
| **M1 direction** — memory reduces tokens/trajectory | −39.2% tokens, −73.1% trajectory steps (control vs. treatment A/B) | `scripts/bench_memory.py` | C1 (2026-08-14); Phase-F re-run below is not directly comparable — see note |
| **M8** — adversarial security suite | **PASS IN FULL — 36/36, 0 failed** (binary metric; see F-V1 section) | `scripts/verify_security.sh` | Part F (this run) |
| **M9** — routing determinism ≥ 90% | **PASS — 60/60 (100%)**; over-exposure (9→40 surface) shows no degradation, honestly recorded as a non-reproduction | `scripts/experiment_tool_ceiling.py` | Part F (this run) |
| **M10** — reproducibility | Stack comes up from a clean bring-up; golden set executes | B-V1 (`scripts/verify_b.sh`), re-run at F-V3 | Part F (this run) |
| Structural verification (network isolation, credential absence, 3 SELECT-only layers, role separation, tool ceiling, seam resolution, output-contract enforcement) | All pass | B-V3..B-V9, E1-V6/V7/V8, E2-V3..V6, F-V4/F-V5/F-V6 | Part F (this run) |

**M1 comparability note.** The Phase-F gap-close session added one extra
LLM call to the SQL Agent's miss path (draft-first interrogation targeting,
`docs/phase_e_gap_log.md` Task 4) — the miss path *is* M1's control arm, so a
fresh `bench_memory.py` run measures a heavier control than the C1-era
−39.2%/−73.1% figures did. Both numbers are retained on record; they are not
directly diffable against each other. See F-V3 below for the fresh run.

---

## Model-sensitive metrics (local provisional, pending graded re-baseline)

| Metric | Local (provisional) | Graded (pending) | Procedure | Local date |
|---|---|---|---|---|
| **M2** — consistency (C1-V3) | FAIL — treatment mean **1.8** distinct result sets vs. control 1.0 (fresh, worse than the 1.5 on record — see `docs/phase_f_log.md` for the run-to-run variance discussion) | — | `scripts/check_consistency.py` | 2026-08-15 (Phase F re-run) |
| **M3** — multilingual lift (D-V1) | **PASS but REGRESSED: +25.0pp (13/40 32.5% → 23/40 57.5%)**, vs. +47.5pp on record. Lift direction and significance hold; magnitude roughly halved. See regression note below | — | `scripts/bench_multilingual.py` | 2026-08-15 (Phase F, isolated clean re-run) |
| **M3** — entity preservation (D-V2) | **REGRESSED: 18/32 (56.2%)**, vs. 27/32 (84.4%) on record | — | same run as D-V1 | 2026-08-15 (Phase F, isolated clean re-run) |
| **M4** — query correctness (C2-V8) | Cold **28.0% (7/25)**, warm **36.0% (9/25)** (fresh) — golden recovery 0 still failing | — | `scripts/check_c2v8.py` | 2026-08-15 (Phase F re-run) |
| **M5** — self-correction repair rate (C2-V2) | **10/12 classified correctly** (fresh; down from 12/12) — 2 of the 2 misses are client-side HTTP timeouts under sustained session load, not classification failures; see `docs/phase_f_log.md` | — | `scripts/bench_correction.py` | 2026-08-15 (Phase F re-run) |
| **M6** — grounding (E1-V3) | Precision **0.622** (unchanged), recall **0.80** (unchanged), faithfulness **0.831** (up from 0.769), **4** ungrounded-but-true (down from 5) | — | `scripts/bench_grounding.py` | 2026-08-15 (Phase F re-run) |
| **M7** — chart quality (E2-V1) | **MEASURED for the first time: 0.4592** mean spec score, `chart:null` 8/30. Over the 22 charts actually produced: 0.626. Task 10 built the emission this metric was waiting on | — | `run_e2_v1.py` then `score_specs.py --score` | 2026-08-15 (first run, pre-JSON-fix) |
| **M7** — A7 validity (E2-V1, binary) | **PASS — 30/30 responses A7-valid**, 0 invalid, 0 transport errors. Not model-sensitive in the same way: this must hold on any model | — | `scripts/run_e2_v1.py` | 2026-08-15 |
| C2-V1 (2-hop join redirect, BD6) | FAIL, conditions 3–4 | — | part of `bench_correction.py`'s C2-V1 worked example | 2026-08-14 |
| D-V6 (spatial exact count, BD6) | FAIL — 41 vs. hand-computed 30 | — | `scripts/check_dv6_spatial.py` | 2026-08-15 |

**F-V3 fresh-run values** (this session, model unchanged from the dates
above — `qwen2.5:7b`) are recorded in `docs/phase_f_log.md`'s F-V3 section
and in each script's own freshly-overwritten `docs/*_report.json`. Where a
fresh number differs from the value on record above, both are shown in the
phase log; this table is not silently overwritten, per
`docs/rebaseline_runbook.md` §5's rule extended to this report.

### M3 regression, reported as F-V3 requires

F-V3: "A metric that regressed since its original Part is reported as a
regression, not silently overwritten." M3 regressed on both halves:

| | Phase D (on record) | Phase F (fresh, isolated) |
|---|---|---|
| D-V1 control | 6/40 (15.0%) | 13/40 (32.5%) |
| D-V1 treatment | 25/40 (62.5%) | 23/40 (57.5%) |
| D-V1 lift | **+47.5pp** | **+25.0pp** |
| D-V2 entity preservation | **27/32 (84.4%)** | **18/32 (56.2%)** |
| English reference line | 16/20 (80.0%) | 9/20 (45.0%) |

**The English reference line is the diagnostic.** English queries pass
through the normalization gate untouched (D-V3 confirms this in both runs:
20/20 `normalized_query_unchanged=True`), so an English-only drop of
80%→45% cannot be caused by anything in the multilingual path. Something
upstream of, or beneath, normalization changed. Two deliberate Phase-E-gap
changes are the candidates, and they are not yet separated:

1. **`memory.enabled` default flipped `true`→`false`** (gap-packet Task 3,
   `docs/spec-gaps.md` NOTE-1). Phase D measured M3 with memory ON; this run
   measured it with memory OFF. Repeated/similar queries within the 40-query
   set that previously hit cached trajectories now each take the full
   miss path.
2. **Draft-first interrogation targeting** (gap-packet Task 4,
   `docs/phase_e_gap_log.md`) changed miss-path SQL generation — and with
   memory off, *every* query is now a miss-path query.

**Separation run completed 2026-08-15 — see `docs/phase_d_log.md` §"M3
separation run" for the full experiment.** Outcome:

- **Candidate 1 (memory flip) is REFUTED.** Re-running with
  `memory.enabled=true` made every absolute measure *worse* (English
  45%→25%, treatment 57.5%→42.5%, entity preservation 56.2%→43.8%).
  **Decision: the shipped default stays `false`** (runbook §4).
- **~20 of the 35 lost English points are a routing defect**, the same one
  tracked as `c2g_10`: four English queries (`sellers.category` and
  `sellers.contracted_sla_hours` lookups) deterministically route to `docs`
  and never execute SQL — identically in both arms, verified live.
- **Residual ~15 points remain unattributed.** Candidate 2 (draft-first
  interrogation) is the only live hypothesis; isolating it needs a
  miss-path-without-draft-first condition that has no toggle today. Not
  guessed at.

*(A first F-V3 run produced worse numbers still — +20.0pp / 43.8% / 25%
English — from a genuine harness bug in `scripts/verify_fv3.sh`: the
previous step, `check_c2v8.py`, deliberately leaves `memory.enabled=true`
on exit for its own golden-recovery assertion, and D-V1 inherited it. That
run is discarded, the bug is fixed (flags reset to shipped defaults before
every step), and the numbers above are from a clean isolated re-run. Both
runs are described in `docs/phase_f_log.md`.)*

---

## F-V1 — M8 adversarial security suite

See `docs/phase_f_log.md` for the full class-by-class breakdown
(`scripts/verify_security.sh`). Binary per the Strategy Document: "This
metric is binary. A partial pass is a fail."

## F-V2 — M9 routing determinism + over-exposure experiment

**Baseline (M9, final): 60/60 (100%)**, all three routes 1.000 — well above
the 90% threshold, through the live, unmodified production Supervisor.

**Over-exposure experiment: no degradation reproduced.** Accuracy held at
1.000 across all four surface sizes (9/15/25/40 total capability
descriptions, i.e. 6/12/22/37 decoys), 5 runs each, 12-query stratified
subsample. `accuracy(40) < accuracy(9)`: **False** — recorded as an honest
non-reproduction per F-V2's own instruction ("do not tune the experiment
until it reproduces"), not adjusted to force a curve.

Full data: `docs/f_v2_report.json`. See `docs/phase_f_log.md` for discussion
of what this non-reproduction plausibly does and doesn't say about the
Supervisor's routing prompt.

---

*Report generated as part of Phase F (`docs/phase_f_log.md`). Do not hand-edit
the Local (provisional) column after a graded run — append a Graded value
instead, per `docs/rebaseline_runbook.md` §5.*
