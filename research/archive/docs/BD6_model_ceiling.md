## BD6 — Local-model ceiling is the recorded disposition for C1-V3, C2-V1, and D-V6 (provisional, re-baseline-gated)

**APPEND-READY.** Paste this verbatim as the next entry in `docs/phase_b_amendment.md`
(and migrate into Build Spec Appendix 5 when that file is checked in under `docs/`),
matching the A0.3 format used by BD1–BD5.

**Decided**: Three Verification steps with written pass-conditions that were not
met on the local model are recorded as **model-ceiling limitations** — caused by
`qwen2.5:7b` SQL-generation quality, a single shared root cause — rather than as
defects in StateSync's own logic, **conditional on re-verification under the
graded (Anthropic) configuration per the re-baseline runbook**:

- **C1-V3 (M2 consistency)**: treatment mean distinct-result-sets 1.5 > control
  1.0; five base queries regressed. Root cause: hit-path (compact-insight) SQL is
  less reliable than miss-path (full-schema) SQL on this model — the same finding
  as the golden-rate 10/10→7/10 drop with `memory.enabled=true`.
- **C2-V1 conditions 3–4**: the correction loop never redirects the 2-hop join
  through `orders`; verified unchanged after prompt reordering + more directive
  FK instructions.
- **D-V6 exact count**: generated SQL omitted the `fulfillments` join +
  `status='delivered'` filter (returned 41 vs hand-computed 30); confirmed by
  reproducing 41 from the ground-truth query with that filter removed.

The **structural / mechanism** halves of all three are independently confirmed
passing (memory hit/miss wiring and tenant+fingerprint scoping; error-history
accumulation and terminal-category handling; digipin decode, predicate
construction, GIST index usage, and English/Hinglish parity).

**Alternative considered**: treat each as a hard A0.4 block ("do not open the
next Part with a prior Part's Verification failing") and halt until repaired on
the local model.

**Why not**: all three reproduce as the same 2-hop-join / filter-omission class
already measured across C2 (M4 ≈ 30%) and D-V6, and ≥1 documented, verified
prompt-improvement attempt per case did not move them. Halting would gate the
whole build on local-model capability the design never claimed to require: the
Strategy Document's evaluation philosophy (D13) is a live A/B on StateSync's own
system with one flag toggled, and every A/B *mechanism* works. The absolute
generation quality is explicitly a model property (BD1 seam; config-selected per
A0.2), not a StateSync-logic property.

**Hook**: this BD is **PROVISIONAL and self-voiding**. It is void if the
re-baseline on the graded model (see `docs/rebaseline_runbook.md`) does not
materially improve these three checks. On void, they revert to open A0.4 blocks
and the phases that depend on them are re-opened. Record the graded numbers
against this entry when the key lands; do not report M2/M4 as final until then.
