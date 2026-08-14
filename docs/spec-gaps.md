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

---
GAP-2 | Part C2 | A3 / Deliverable 5 (malformed_queries.json) | build agent | 2026-08-14
"join_cardinality" is a valid ErrorCategory (state.py) and Deliverable 5's
fixture requires an mq_10 seed with expected_category="join_cardinality" (a
fan-out join that double-counts SUM(oi.line_value) across a 1:many join to
fulfillments). But this query does not error: it executes successfully
(ok:true, non-empty rows) with silently wrong aggregate values. A3's
classifier is explicitly "deterministic... a mapping from engine error codes
and regex patterns" operating on sqlstate/message/empty_result -- there is no
engine-observable signal that distinguishes a correct join from a fan-out
join. Nothing in A3, A4, or C2 describes a mechanism (row-count sanity check,
cardinality heuristic, or otherwise) for detecting this deterministically.
Resolution: see BD3 (docs/phase_b_amendment.md). mq_10 is retained in the
fixture exactly as specified (the deliverable requires copying its structure
verbatim), but it structurally cannot enter the classifier at all -- the
query succeeds, so no failure/empty-result ever reaches A3. bench_correction.py
and C2-V2 report this seed honestly as "never reached the classifier
(non-erroring, semantically wrong result)", distinct from a 0%-repair-rate
category where the classifier fires but no fix is found.
---

---
GAP-3 | Part C2 | Appendix 2.4, mq_08 | build agent | 2026-08-14
The literal mq_08 SQL given in the spec (`ST_DWithin(geom, 'POINT(72.87
19.07)', 5000)`) does not reproduce the intended failure against this
environment's Postgres/PostGIS: the untyped string literal casts cleanly and
the query returns ok:true with a real row, not the described "untyped
literal, missing SRID and geography cast" error. Verified empirically (see
scratchpad of this session) before freezing the fixture.
Resolution: substituted a verified-working spatial_invalid trigger with the
same fault family (a geometry/geography SRID mismatch, still under
A4.5/A5's SRID 4326 contract): `ST_DWithin(geom::geometry,
ST_SetSRID(ST_MakePoint(72.87, 19.07), 3857), 5000)`, which reliably
produces sqlstate XX000 with "Operation on mixed SRID geometries" in the
message (matches the taxonomy's `(?i)geometry|srid|postgis` regex). This is
the same category of mistake (an SRID that doesn't match the column's 4326)
as the original text described, just a concrete query that actually
reproduces it here. Not a BD -- this is a fixture-content correction
(Appendix 2 fixtures are "populated... then frozen" per instructions, not a
Strategy/Build-Level decision), recorded for traceability only.
---
