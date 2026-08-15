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
- [ ] E2-V1 (M7): **only runnable once Task 10 lands** (the Synthesizer must
      populate `chart` first). If still deferred, record it as still deferred.

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

| Check | Metric | Local baseline on record | Graded result |
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
| E2 (Task 10) | M7 chart quality | pending — Synthesizer does not yet populate `chart` on any route | ___ |

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
