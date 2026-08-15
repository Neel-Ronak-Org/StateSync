# Anthropic-gap work packet — completion log (Tasks 1–5)

Date: 2026-08-15. Builds on Phase E1 (`0f0ebb9`).
Packet: `docs/AGENT_TASKS_phase_e_gap.md`.

## Scope actually executed

**Tasks 1–5 only.** Tasks 6–9 are Phase E2 deliverables (A7 dual validation,
CSP + `vega-interpreter`, the deterministic M7 scorer, the
`malicious_specs.json` rejection suite, and `docs/phase_e2_log.md`), and the
standing instruction for this session is not to begin E2 building. They are
untouched and remain open. Task 10 is deferred by its own definition.

---

## Task 1 — BD6 recorded. Done.

Appended verbatim to `docs/phase_b_amendment.md` as the entry following BD5,
in the same A0.3 Decided / Alternative / Why / Hook shape as BD1–BD5. Only the
"APPEND-READY, paste this verbatim" instruction line was dropped — that is
meta-instruction to the agent, not part of the entry. No other BD touched.

## Task 2 — Strategy §7.0 recorded. Done, with one action the packet implied but did not state.

`StateSync_Strategy_Document.md` was **not checked into the repository** — the
task could not be performed against a file that did not exist. The Strategy
Document was therefore checked in at `docs/StateSync_Strategy_Document.md`
(precedent: `statesync_phase_B.md` is already tracked at repo root), and §7.0
inserted at the head of §7 verbatim. §7's existing lead line and M1–M10 text
are unchanged below it, confirmed by diff.

Note for later: `StateSync_Build_Spec.md` is still not in-repo. That is why
BD1–BD6 live in the standalone `docs/phase_b_amendment.md` rather than in
Appendix 5, as BD1's own header records. Checking the Build Spec in would
create an obligation to migrate all six BD entries into its Appendix 5, which
is beyond this packet — flagged, not done.

## Task 3 — `memory.enabled` default flipped to `false`. Done, with a bug caught in the packet's own instruction.

`config/flags.toml` now defaults `memory.enabled = false`, carrying the
comment text the packet specified.

**The comment could not go where the packet said to put it.**
`scripts/bench_memory.py`'s `set_memory_enabled()` matches
`r"(\[memory\]\s*\n\s*enabled\s*=\s*)(true|false)"` and raises rather than
guess if it does not match exactly once. `\s` does not match `#`, so comment
lines placed between `[memory]` and `enabled` break the regex — which would
have broken `bench_memory.py` *and* `check_consistency.py` (it imports
`set_memory_enabled` from the same module), i.e. both halves of the C1-V1 and
C1-V3 A/B. Caught before running anything: the comment block sits above the
`[memory]` header instead, with an inline note on the `enabled` line. Verified
by re-running the exact regex against the edited file for both `true` and
`false`: `matches=1` in both directions, and the file still parses as TOML with
`memory.enabled = False`.

Acceptance otherwise as specified: `config/` is a **read-only bind mount** into
the containers, so a host-side edit is what the running services read after a
restart — which is exactly the mechanism the bench scripts already rely on.
`flags_snapshot` still captures the flag at query entry (A2), unchanged.

Regression effect: recorded under "Regression check" below.
Recorded as `NOTE-1` in `docs/spec-gaps.md` — a reversible flag *default*, not
a BD, per `flags.toml`'s own header rule.

## Task 4 — Interrogation targets now derive from the query. Done.

**Before:** `_pick_interrogation_targets` walked the schema in alphabetical
order and took the first `sql.interrogation_max_samples` tables having any
nullable column — the same four tables (`catalog_items`, `delivery_events`,
`disputes`, `document_chunks`) for *every* question, on both the control and
treatment arms. `docs/phase_c2_log.md` records the resulting constant
"exactly 4" sample count.

**After:** on the miss path the agent drafts its query first, parses that
draft's own FROM/JOIN targets with `pglast` (`RangeVar` nodes, so subquery and
CTE-body references are caught too), intersects them against the real table
list from `schema.list_tables`, and samples only those, still capped at
`sql.interrogation_max_samples`. Strategy §4.2 describes interrogation as
verifying "its hypotheses about the data's actual shape" — that presumes a
hypothesis exists before sampling, and the draft is that hypothesis.

Fallback is preserved, so this cannot regress: an unparseable draft, or one
naming nothing real, reverts to the original schema-wide rule. Verified
directly against the parser — `SELECT FROM orders WHERE;`, an empty string, and
non-SQL text all yield `[]`. A CTE alias (e.g. `WITH late AS (…) … JOIN late`)
surfaces as a `RangeVar` but is dropped by the intersection, confirmed.

**Evidence (live, `docker logs` + returned state trace):**

| Query | Tables sampled | Executed SQL's own tables |
|---|---|---|
| c2g_11 "total delivery events per event type" | `delivery_events` | `delivery_events` |
| c2g_06 "disputes unresolved, no resolution date" | `disputes` | `disputes` |
| c2g_02 "total order value per logistics provider" | `logistics_providers`, `orders`, `fulfillments` | `orders`, `fulfillments`, `logistics_providers` |

Three distinct questions, three distinct target sets, each matching the
question's real tables, each ≤ the cap of 4. C2-V7 still holds: the MCP call
log shows `schema.list_tables → data.sample_rows(×n) → data.run_select` for
every one — sampling strictly precedes execution, and
`interrogation_samples` is non-empty on every sql-route query.

No table names are hardcoded anywhere in the change (A0.2); the targets come
from the parse tree and the live schema. `pglast>=6.2` added to
`orchestrator/pyproject.toml`, version-matched to `mcp/pyproject.toml`. Here it
is read-only and **not** security-bearing (unlike the Layer-1 gate), so an
absent parser degrades to the schema-wide fallback rather than failing closed.

**Cost, stated plainly because it biases a metric.** This adds **one extra
generation call on the miss path** — none on the memory-hit path, none on
retries, and none when the test harness injects `debug_candidate_sql` (that
injected SQL is reused as the draft, so C2-V1..V4's call counts are unchanged).
The miss path *is* M1's control arm, so this makes the control more expensive
and will inflate C1-V1's measured token/trajectory reduction. **The existing
M1 numbers (−39.2% tokens, −73.1% steps) are not comparable to a post-change
run.** The re-baseline runbook re-runs C1-V1 anyway; that run is now measuring
a different control and must say so rather than diffing against the old
figure. Flagged here rather than discovered later.

## Task 5 — `spatial_ref_sys` excluded from `schema.list_tables`. Done, and the packet's premise about the fingerprint turned out to be wrong.

`schema.list_tables` now excludes **extension-owned** tables — `pg_depend`
`deptype = 'e'` — rather than denylisting the name `spatial_ref_sys`, per the
packet's own preference. Anything PostGIS or pgvector installs into `public` is
by construction not part of the A5 application schema, and a name denylist
would silently let the next one through.

**Result:** `schema.list_tables` returns exactly **12** tables — the 12 A5
application tables — with `spatial_ref_sys` absent. Every miss-path query
previously spent a `schema.describe_table` round-trip on it, on both arms.

Two corrections to the packet's stated expectations, both verified:

1. **The expected count of 17 is wrong for this schema.** `public` holds 14
   base tables: 12 A5 application tables + `schema_versions` (excluded since
   Phase B) + `spatial_ref_sys`. 12 is the correct post-fix count. The Phase B
   log's "17 tables" figure does not match the live database.
2. **The fingerprint does not equal `4e1de0e0…`, and that is not a STOP.**
   `4e1de0e0…` is the *Phase B* value; `schema_versions` shows it was
   superseded twice during Phase C1's own fingerprint work, and the current
   value `cf4caa5c…` has been current since 2026-08-14 10:49 UTC — recorded in
   `docs/phase_c1_log.md`, and predating every edit in this packet. mcp starts
   clean, verifies `cf4caa5c…` against `schema_versions`, and exposes 9 tools.
   The fingerprint is unchanged by this task, structurally: `fingerprint.py`
   runs its own separate query and was not touched.

**But the packet's underlying premise was false, and that is a real finding.**
It asserted "the fingerprint already excludes system tables by operating on the
application schema." It does not. `mcp/fingerprint.py` selects
`information_schema.tables WHERE table_schema='public' AND table_type='BASE
TABLE'`, excluding only `schema_versions` — verified under **both**
`statesync_migrator` and `statesync_reader` to return 14 rows including
`spatial_ref_sys`. So the A5 fingerprint is currently hashing PostGIS's table.

Deliberately **not** fixed, and not silently absorbed either: changing the
formula changes the fingerprint value, which under D4's validate-on-read
invalidates every stored trajectory and requires re-stamping `schema_versions`.
That is a decision about D4's blast radius, not a cleanup. Recorded as
**GAP-6** in `docs/spec-gaps.md` and escalated. Practical impact today is nil —
`spatial_ref_sys`'s shape is fixed by the PostGIS version, so the fingerprint
is stable, and it would only move on a PostGIS upgrade, where invalidating
trajectories is arguably the conservative behavior anyway.

---

## Observed in passing, out of this packet's scope

**A routing miss worth a look before Part F (M9).** `c2g_10` — "List sellers
whose contracted SLA hours has not been set" — routed to `docs`, not `sql`, so
no SQL ran at all. This is unrelated to anything in this packet (all three code
changes here sit downstream of routing) and traces to E1's Supervisor prompt
rewrite, which removed Phase B's "default to sql" bias to stop the opposite
failure. The word "SLA" now appears to pull an otherwise plainly relational
question toward the document route. M9 measures exactly this (routing
determinism ≥ 90% over `phase_f_routing.json`), so it belongs there rather than
being patched blind here.

## Environment note

Docker Desktop's BuildKit stalled indefinitely on `docker compose build`
(images unchanged after ~35 minutes, no output emitted). Not a network fault —
PyPI resolves from inside the containers in 0.3s, tested directly. Same class
of Docker Desktop flakiness recorded in the Phase D and E1 logs. Verification
therefore ran against **live-patched containers** (`docker compose cp` of the
two edited source files, `pip install pglast` in `orchestrator`, restart) — the
same workaround those phases used, and testing-only. The `config/` directory is
a read-only bind mount, so `flags.toml` needed no copy.

**Resolved.** `DOCKER_BUILDKIT=0 COMPOSE_DOCKER_CLI_BUILD=0 docker compose build`
(the classic builder, after `docker builder prune -f`) completes normally — so
the stall is BuildKit-specific, not a Dockerfile or network problem. All images
were rebuilt that way and the containers recreated from them
(`up -d --force-recreate`), discarding every live-patched layer. Every number in
this log and in `docs/phase_e2_log.md` was re-confirmed against those clean
images. Recommend the classic-builder fallback for the graded run as well,
until the BuildKit issue is understood.

## Regression check

`scripts/lint-no-literals.sh`: **PASS** (41 files, 0 violations).

`scripts/verify_b.sh`: **28/28 structural checks pass, 0 failed**, and B-V1's
golden query rate is **10/10** — every query matching its frozen row count.

**That 10/10 includes `b_008`.** `b_008` has failed in every recorded run since
Phase C2 and was carried forward through the C2, D, and E1 logs as a
"pre-existing failure, not a regression, don't spend time on it" — including in
this session's own Phase E1 closeout, where it failed with the same
`rows_got=10 min=1 fixed=1` signature. It passes now, on the first run after
the Task 3 default flip, with no change to the query, the fixture, or the SQL
path. The cause was `memory.enabled = true`: the hit path was returning 10 rows
where the miss path returns the correct 1. So it was never a standalone
pre-existing defect — it was the same hit-path-degradation finding that BD6
records for C1-V3, showing up in the golden set, and three phase logs
mislabelled it. Correcting that here rather than leaving the mislabel on record.

This also means Task 3's stated expectation ("expect ~10/10, modulo the
documented run-to-run variance and the pre-existing `b_008`/`b_010` cases") was
met more cleanly than it predicted: no exceptions were needed.

**Re-run after the clean rebuild: identical.** The BuildKit stall was resolved
by falling back to the legacy builder (`DOCKER_BUILDKIT=0
COMPOSE_DOCKER_CLI_BUILD=0`, after `docker builder prune -f`), which completed
all three images without incident. Containers were recreated from those images
(`up -d --force-recreate`, discarding the live-patched writable layers —
confirmed by `pglast v8.4` being present in the rebuilt orchestrator image
rather than pip-installed into a running container), and `verify_b.sh` re-run:
**28/28 structural, 0 failed, golden 10/10** again, `b_008` included. So the
result above is not an artifact of live patching.
