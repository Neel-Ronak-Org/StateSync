# StateSync — Graded (Anthropic) Re-baseline Runbook

Run this the day the Anthropic key is available. It resolves BD6 and converts
every model-sensitive metric from "local, provisional" to "graded, final."
Companion: `BD6_model_ceiling.md`, `strategy_addendum_model_config.md`.

---

## Re-baseline checklist

Tick these off in order. Everything above the line marked PRE-VERIFIED was
confirmed during Phase F's re-baseline preparation (2026-08-15) and needs no
investigation — just confirm it still holds.

**Setup (PRE-VERIFIED — confirm, don't investigate)**
- [ ] Place the real key at `secrets/anthropic_api_key` (host path). The wiring
      is already correct end to end: compose secret `anthropic_api_key` →
      mounted at `/run/secrets/anthropic_api_key` → orchestrator env
      `ANTHROPIC_API_KEY_FILE` → read once at adapter construction
      (`orchestrator/llm/adapters/anthropic_adapter.py`), with an
      `ANTHROPIC_API_KEY` env fallback. Never a source literal.
- [ ] Set `llm.provider = "anthropic"` in `config/flags.toml` (currently
      `"local"`). This is the only line that must change.
- [ ] `[llm.models.anthropic]` already defines **all seven** roles the seam
      resolves — `supervisor`, `sql`, `synthesizer`, `memory_summarizer`,
      `normalizer`, `decomposer`, `judge`. Verified against
      `provider.py::_VALID_ROLES`. No additions needed.
- [ ] Decide `memory.enabled` before running (see step 4 and Item A of
      `docs/open_items.md` — the M3 separation run bears directly on this).
- [ ] Rebuild and `docker compose up -d` — **not** `restart`, which runs stale
      images. If BuildKit stalls, use
      `DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0 docker compose build`.

**Run (each writes its own `docs/*_report.json`, overwriting the local one —
copy the local report aside first if you want both on disk)**
- [ ] `scripts/verify_b.sh` — B-V1 (M10)
- [ ] `scripts/bench_memory.py` — C1-V1 (M1)
- [ ] `scripts/check_consistency.py` — C1-V3 (M2)
- [ ] `scripts/bench_correction.py` — C2-V2 (M5) + C2-V1
- [ ] `scripts/check_c2v8.py` — C2-V8 (M4)
- [ ] `scripts/bench_multilingual.py` — D-V1/D-V2 (M3)
- [ ] `scripts/check_dv6_spatial.py` — D-V6
- [ ] `scripts/bench_grounding.py` — E1-V3 (M6)
- [ ] `scripts/experiment_tool_ceiling.py` — F-V2 (M9; model-independent, re-run
      for completeness only — do not gate on it)
- [ ] E2-V1 (M7): **Task 10 has landed — this is now runnable and must be run.**
      `python3 scripts/run_e2_v1.py --out docs/e2_v1_generated_<label>.json`
      then `python3 scripts/score_specs.py --score <same path>`. Two numbers to
      record, not one: the A7 validity count (binary, must be 30/30) and the
      mean spec score. Record the `chart:null` count separately — a null scores
      0.0 against a reference that has a chart, so the null count and the spec
      quality move the mean for different reasons and must not be conflated.
      `scripts/verify_fv3.sh` runs both halves automatically.

Use `scripts/verify_fv3.sh` to run the C/D/E sequence in order — it resets
flags to shipped defaults between steps, which a hand-run sequence does not
(this bit us in Phase F; see `docs/phase_f_log.md`).

**Resolve**
- [ ] Fill the "Graded (pending)" column in `docs/metrics-report.md`. **Append —
      never overwrite the Local column** (§5).
- [ ] Resolve BD6: confirmed, or void (§3).
- [ ] Re-decide the `memory.enabled` shipped default (§4).
- [ ] Append a "graded re-baseline" section to each phase log; do not edit
      existing numbers.
- [ ] Update `docs/open_items.md` status column for anything now closed.

---

## 0. Preconditions
- Anthropic API key available.
- Agent Task Packet tasks 1–9 already applied (docs recorded, default flipped,
  model-independent fixes in).

## 1. Switch the provider seam (BD1)
1. Place the key where the compose stack reads it (per the walkthrough:
   `secrets/anthropic_api_key`), never as a source literal.
2. In `config/flags.toml`, set `llm.provider = "anthropic"`.
3. Confirm `[llm.models.anthropic]` defines a model id for **every** role the
   orchestrator resolves. That is **seven** roles, not six: `supervisor`,
   `sql`, `synthesizer`, `memory_summarizer`, `normalizer`, `decomposer`,
   `judge` — the authoritative list is `_VALID_ROLES` in
   `orchestrator/llm/provider.py`. (`memory_summarizer` was omitted from an
   earlier draft of this line; C1's trajectory-summarization call resolves it,
   so a missing entry would fail the moment memory is enabled.) A missing role
   must be a startup failure, not a silent fallback (A8: unresolvable name =
   startup fail). **Verified present for all seven as of 2026-08-15** — see the
   checklist at the top.
4. Rebuild + `docker compose up -d` (NOT `restart` — see C1 log bug #7:
   `restart` runs stale images).

## 2. Re-run the model-sensitive checks (diff against recorded local numbers)
Run in this order and record each result next to its local baseline:

> **⚠ READ BEFORE DIFFING ANYTHING AGAINST THIS TABLE — added 2026-08-16 by the
> fresh-context audit.** The "Local baseline on record" column below is a
> snapshot from *before* Phase F's re-runs, and **every model-sensitive row in
> it has since been superseded**. It is the column a graded run would be
> diffed against, so using it as-is would produce a wrong delta for essentially
> every metric.
>
> Not overwritten, per §5's own append rule. The current values are added as a
> third column instead, and the stale column is kept exactly as written:
>
> | Check | This table says | Current on record | Source |
> |---|---|---|---|
> | C1-V3 (M2) | treat **1.5** vs ctrl 1.0 | treat **1.8** vs ctrl 1.0 (F-V3 reproduced 1.8 exactly) | `metrics-report.md` |
> | C2-V8 (M4) | **29.2%** / 32.0% | cold **36.0%** (2026-08-16 clean re-run), warm **not re-measured** since `da6e49f` | `m4_diagnosis_v2_corrected.json` |
> | D-V1 (M3) | **+47.5pp** (15.0%→62.5%) | **+25.0pp** (40.0%→65.0%) — the lift fell because the CONTROL rose | `metrics-report.md` |
> | D-V2 (M3) | **27/32 (84.4%)** | **26/32 (81.25%)** | `bench_multilingual_report.json` |
> | E1-V3 (M6) | 0.622 / 0.80 / **0.769** | 0.622 / 0.80 / **0.8125** | `metrics-report.md` |
> | E2-V1 (M7) | **0.4592**, 8/30 null (pre-JSON-fix) | **0.4850**, 7/30 null (post-fix) | `metrics-report.md` |
>
> The M4 row is the worst of them: **29.2% appears nowhere else in the repo**
> (`metrics-report.md` says 28.0% for the same check), so this table and the
> metrics report have disagreed on M4's baseline for as long as both have
> existed. 29.2% is 7/24, one query short of the 25-query fixture.
>
> **Two further conditions a graded run must satisfy, neither of which existed
> when this runbook was written:**
> 1. Every harness now asserts the shipped flag state at startup
>    (`scripts/shipped_flags.py`) and refuses otherwise. A graded run that
>    deliberately varies a flag must pass `--allow-nonshipped-flags`, and the
>    deviation is recorded in the report's `flag_provenance` field.
> 2. Run `python3 scripts/verify_deployed.py` first. The provider switch in §1
>    is a code change, and this repo has shipped a committed-but-not-deployed
>    change twice (`d5a5d35`, `5301b89`). A graded run against a stale image
>    would produce a graded number for the wrong build.

| Check | Metric | Local baseline on record (STALE — see the box above) | Graded result |
|---|---|---|---|
| C1-V1 | M1 direction | −39.2% tokens, −73.1% steps (pre-Phase-F; re-run at F-V3 adds one miss-path draft call to the control arm — see `docs/phase_e_gap_log.md` Task 4 — so the fresh number is not directly comparable to this one; diff against the F-V3 re-run's own value, not this row) | ___ |
| C1-V3 | M2 consistency | FAIL (treat 1.5 vs ctrl 1.0) | ___ |
| C2-V2 | M5 repair rate | see `docs/phase_f_log.md` F-V3 section for the Phase-F re-run figure (added here; not previously tracked in this table — GAP-8-adjacent completeness gap, closed in Part F) | ___ |
| C2-V8 | M4 cold / warm | 29.2% / 32.0% | ___ |
| C2-V1 | 2-hop join redirect | cond 3–4 FAIL | ___ |
| D-V1 | M3 lift | +47.5pp (15%→62.5%) | ___ |
| D-V2 | M3 entity preservation | 27/32 (84.4%) — same `bench_multilingual.py` run as D-V1, not a separate script | ___ |
| D-V6 | spatial exact count | FAIL (41 vs 30) | ___ |
| E1-V3 | M6 prec/recall/faith | 0.622 / 0.80 / 0.769 | ___ |
| E2-V1 | M7 chart quality | 0.4592 mean spec score, 8/30 `chart:null`, A7 30/30 valid (local, first run, pre-JSON-fix) | ___ |
| E2-V1 | M7 A7 validity (binary) | 30/30 valid — must stay 30/30 on any model | ___ |

**M9 (F-V2 routing determinism) is intentionally NOT a row above.** Strategy
§7.0 classifies M9 as model-independent ("a classification property... stand
as measured on any model") — unlike M2/M4/M6/M7, an M9 number measured on the
local model is not provisional pending a graded re-run; it is reported as
final in `docs/metrics-report.md` and `docs/phase_f_log.md` directly. Re-run
it under the graded model anyway for completeness once the key lands (the
routing PROMPT could still perform differently under a different model, even
though the design property being tested does not require re-verification),
but do not gate anything on that re-run the way BD6 gates M2/M4's checks.

Use the existing scripts unchanged: `bench_memory.py`, `check_consistency.py`,
`bench_correction.py` / `check_c2v8.py`, `bench_multilingual.py`,
`bench_grounding.py`. They toggle flags themselves; do not hand-edit fixtures to
match output (A0.4). The `judge` role for `bench_grounding.py` now also runs on
Anthropic — confirm it resolves via `get_agent_llm("judge")` (E1 log bug #7).

## 3. Resolve BD6
- **If C1-V3, C2-V1, and D-V6 materially improve** (C1-V3: treatment mean ≤
  control and no query worse; C2-V1: the join redirects; D-V6: count == 30):
  mark BD6 **CONFIRMED**, record the graded numbers against it, keep the phases
  closed.
- **If they do NOT improve**: BD6 **VOIDS** itself per its own hook. The three
  checks revert to open A0.4 blocks; the dependent phases re-open and the failures
  become genuine defects to fix, not model-ceiling. Escalate to the team.

## 4. Re-decide the memory default (Task 3)
If graded hit-path SQL is now as reliable as miss-path (C1-V3 recovers), flip
`memory.enabled` back to `true` as the shipped default and note it. Otherwise
leave `false`.

## 5. Report
Only after steps 2–4: report M2, M4, M6, M7 as **final graded figures**, each
paired with its local baseline and the control-vs-treatment A/B (D13). Update
`phase_c1_log.md`, `phase_c2_log.md`, `phase_d_log.md`, `phase_e1_log.md`,
`phase_e2_log.md` with a "graded re-baseline" section each; do not overwrite the
local numbers — append.
