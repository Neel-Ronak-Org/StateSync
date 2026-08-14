# docs/spec-gaps.md
# StateSync — Specification Gaps Log
#
# Per A0.1: If the specification does not state what happens in a case
# encountered during build, that is a gap to flag — not a gap to fill.
# Record each gap here with the Part and subsection, then surface to the team.
#
# Format (append new entries):
# ---
# GAP-N | Part | Subsection | Discovered by | Date
# Description of the ambiguity.
# Resolution (pending/decided):
# ---

# No gaps recorded at Phase B issue date.
# Append new gaps as BD-N entries in Appendix 5 (statesync_phase_B.md) if they
# are decisions, or here if they remain open questions for the team.

---
GAP-1 | Part C1 | C1 Scope / C1 Notes | build agent | 2026-08-14
Does a successful query on the memory-HIT path also write a new trajectory
back via memory.put_trajectory, or only a successful MISS-path (full schema
read) query? "Successful queries write a trajectory back" (C1 Scope) and "The
trajectory write happens only on execution success" (C1 Notes) both read as
general rules, but neither says whether a hit counts. Nothing in A4.8, A6, or
the Verification section (C1-V1..V6) disambiguates this either.
Resolution (decided, not a BD -- implementation-level, reversible): hit-path
successes do NOT write back. Only hit_count on the reused trajectory is
bumped (inside memory.get_trajectory itself). Reasons: (1) a fresh
summarization LLM call on every hit would add tokens/llm_calls to the
treatment run that the control run never pays, partially offsetting the very
reduction C1-V1 measures; (2) repeatedly writing near-duplicate trajectories
for the same recurring query pattern has no stated benefit and works against
Qdrant collection hygiene. See orchestrator/nodes/sql_agent.py module
docstring. Revisit if C1-V1/C1-V2 results suggest hit-quality degrades
without reinforcement.
---
