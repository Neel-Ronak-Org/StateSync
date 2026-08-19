# The cycle protocol — how a session on StateSync is run

**Binding.** This file governs how work is handed between sessions. It is not a
style preference; each rule below exists because its absence cost this project
a measurable thing, cited inline.

The unit of work is a **cycle**: one session, numbered, that audits the state it
inherited, plans against what it found, builds, and hands off. The owner drives
the model switch manually (`/model`) — one session spans the whole cycle.

---

## 0. Why the cycle is shaped this way

The recurring failure in this project is not bad code. It is **a claim that
nothing checked**: a metric named for something it did not measure, a
verification that passed before its subject existed, a default committed wrong,
code committed but never shipped. Every one was found by someone arriving
without the belief that produced it.

So the cycle is built to manufacture that condition on purpose, and to make the
useful part of institutional memory survive anyway.

---

## 1. The five moves

| # | Move | Context available | Output |
|---|---|---|---|
| 1 | **Audit** | orientation only (`AGENT_HANDOFF.md`, the repo, previous *audits*) | `docs/audits/NNN.md` |
| 2 | **Unseal** | + all previous `docs/cycles/*` | — |
| 3 | **Plan** | full | plan + **frozen** acceptance criteria in `docs/cycles/NNN.md` |
| 4 | **Build** | full (owner switches model) | code, commits, deviations appended |
| 5 | **Brief** | full | outcome + confidence section in `docs/cycles/NNN.md` |

Move 2 is gated: `python3 scripts/cycle.py unseal NNN` refuses until the audit
file exists and is substantive. This is a guard rather than a convention, for
the same reason `scripts/shipped_flags.py` is — a convention that is only
remembered is a convention that is eventually forgotten.

---

## 2. The seal — what is withheld, and the exact line

**Orientation carries claims. The briefing carries confidence.**

The auditor gets every number, every gate result, every decision, every open
question. Those are things to *check*, and hiding them means the auditor does
not know what to check.

The auditor does **not** get the outgoing agent's self-assessment: what it
thinks is solid, what it suspects is shaky, why it made each call. That is
sealed in `docs/cycles/*` until the audit is written and committed.

Reason for the line falling there. A self-assessment is a map of where to look,
and an auditor handed one looks there and nowhere else. Every defect this
project has paid for was in a place the previous agent believed was fine —
`a4293e1` was reasoned well and spot-checked well before measurement refuted
it; `f46e0bf` published a non-BIRD number under a BIRD label with the
diagnostic working exactly as its author intended.

**Previous audits are NOT sealed.** Reading them shows what was already found
and what was left open, which prevents rediscovery. An audit is a defect list,
not a self-assessment.

---

## 3. Rules on the audit (move 1)

**3.1 — Every finding carries a transcript.** The command run and what came
back. A finding without a reproduction is labelled `HYPOTHESIS`, and a
hypothesis may not become this-cycle work without first being reproduced.

**3.2 — Every finding states what it does NOT establish.** One line, mandatory.

> Precedent: a `qwen2.5-coder:14b` result was measured correctly on **one**
> role split (SQL-generator only) and stated as a conclusion about the model.
> The owner caught it — "why didn't we use 14b for supervisor and all too?" —
> and the all-roles run gave a different number. The transcript was real. The
> scope claim was not. This line is the check that was missing.

**3.3 — Rank by publishable impact.** A finding is this-cycle work only if it
invalidates a number or a claim that would appear in the report, the paper, or
the demo. Everything else goes to `docs/open_items.md`. State the rank; do not
leave it implied.

**3.4 — Standing checklist.** Section 6 lists the failure classes this repo has
actually produced, each with a real commit. Walk it. Finding nothing in a class
is a result worth writing down.

**3.5 — "No findings" is a permitted outcome**, but only with the checklist
walked and the transcripts shown. An empty audit with no evidence of looking is
a failed audit, not a clean one.

---

## 4. Rules on acting (moves 2–3)

**4.1 — Classify before fixing.** Every finding lands in exactly one bucket:

| bucket | meaning | action |
|---|---|---|
| **DEFECT** | the previous agent was wrong | fix it |
| **TRADEOFF** | previous agent knew and chose | leave it, cite where recorded |
| **OWNER** | the owner decided this | **ask before touching** |

**4.2 — The evidence rule for TRADEOFF.** The justification must exist in a
commit message or document written **before** the audit found it, and the audit
must cite it. A rationale that can only be constructed after the fact is a
DEFECT wearing a tradeoff's clothes. Without this rule "understand why the
previous agent did it" becomes a machine for explaining away every finding, and
nothing is ever fixed.

**4.3 — OWNER decisions currently on the list** (never reverse silently):
the migrations 005/006 revert; the Devanagari-vs-Hinglish direction in
`AGENT_HANDOFF.md` §5; `memory.enabled` default `false`; write capability
absent; no new features.

**4.4 — Acceptance criteria are frozen before any code is written.**
`python3 scripts/cycle.py freeze NNN` records a SHA-256 of the criteria block.
`verify` recomputes it. Moving a criterion after the build has started is the
same class of act as editing a scorer to make a number look better, and it is
prohibited on the same grounds. If the build proves a criterion wrong, append a
**DEVIATION** entry saying so — do not edit the criterion.

---

## 5. Rules on handing off (moves 4–5)

**5.1 — Write in three passes, never one.** Findings before the plan; criteria
before the build; outcome during and after it. A handoff written only at the
end is written by a context-compacted agent that has lost the details it most
needed to record. This has already happened.

**5.2 — Append deviations as they occur**, not from memory afterwards.

**5.3 — The confidence section is where honesty is cheap.** It is sealed from
the next auditor, so nothing is lost by saying "I did not verify this", "this
number is provisional on the 7B", "I believe X but only tested one path". Say
it there. It reaches the owner and it reaches the next agent *after* it has
formed its own view.

**5.4 — Report your own regressions** in the outcome section, by name.

---

## 6. Standing audit checklist — failure classes with real instances

Every entry happened here. Walk all ten.

| # | Class | Instance |
|---|---|---|
| 1 | **Vacuous pass** — check passes because its subject does not exist yet | F-V1's chart check passed before charts existed; M8 could pass while asserting nothing (`dc2e31f`) |
| 2 | **Metric measures something other than its name** | M4 measured column width; "value recall" is unaligned and de-duplicated, so 30 rows for a 1-row answer scores 1.00 |
| 3 | **Repo ≠ running image** | `d5a5d35` D11's rendering half committed, never shipped; `5301b89` stale evaluator image |
| 4 | **Two copies of one fact drift** | BD4/BD5/BD7 key lists; the Dockerfile's second dependency list; two implementations of "standard EX"; two copies of the shipped-defaults table |
| 5 | **API verified, user path never** | `9d73b7c` the frontend could never reach its API; every check tested the API directly |
| 6 | **Overclaim from probe without measurement** | `d8a8a86` "recovered 7 of 8" → measured 1 of 8; the `coder:14b` one-role-split conclusion |
| 7 | **Ground truth itself wrong** | 12 of 25 `phase_c2` references were defective; correcting them moved standard EX +16.0pp |
| 8 | **Shipped default committed wrong** | `d7c0a6b` |
| 9 | **Measurement contaminated by concurrent work** | three instances, the third being two Claude sessions in one repo — which no in-repo guard can see |
| 10 | **A claim about source code that nothing checks** | "fingerprint.py hardcodes public" was load-bearing and unasserted until `tests/test_schema_scoping.py` |

---

## 7. What this protocol does NOT catch

Stated so a followed protocol never implies more than it means.

- **A wrong plan.** The auditor checks the build against the criteria; nothing
  checks that the criteria were the right ones. The owner has caught this twice
  (the RAG engine missing from V2_STRATEGY; the `coder:14b` scope). It remains
  a human job.
- **A shared blind spot.** If a defect is invisible to the failure classes in
  §6, every cycle misses it identically.
- **Anything the audit had no budget to reach.** Audits are ranked and
  truncated by design (§3.3); "not found" is not "not there".
- **Concurrency between sessions.** §6 class 9. No in-repo guard sees another
  Claude session. Check `ps aux | grep -E "bench_|check_|diagnose_"` and ask.

---

## 8. The opening prompt

Paste this as the first message of a new session, substituting `NNN`. It is
kept here so it is not retyped — and retyped slightly differently — each cycle.

> You are opening cycle **NNN** on StateSync. Read `docs/CYCLE_PROTOCOL.md`
> first; it governs this session. Then `docs/AGENT_HANDOFF.md`.
>
> **You are the auditor until you say otherwise.** Do not read anything in
> `docs/cycles/` — those are previous agents' briefings and they are sealed
> until your audit is written. `python3 scripts/cycle.py status` shows the
> state.
>
> **Move 1.** Fill in `docs/audits/NNN.md`. Walk all ten failure classes in
> protocol §6 — finding nothing in a class is a result, skipping it is not.
> Every finding needs a command transcript and the mandatory "Does not
> establish" line. Rank each finding (§3.3) and bucket it (§4.1); a TRADEOFF
> must cite evidence that predates your audit (§4.2). Commit the audit.
>
> **Move 2.** `python3 scripts/cycle.py unseal NNN`. If it refuses, it will say
> why. Then read `docs/cycles/*` and tell me what the previous agent believed
> that your audit contradicts — that gap is the most valuable output of this
> move.
>
> **Move 3.** Propose the plan. Put acceptance criteria between the
> `CRITERIA:BEGIN`/`CRITERIA:END` markers in `docs/cycles/NNN.md`. **Stop and
> show me before freezing** — §7 says a wrong plan is what this protocol cannot
> catch, so that check is mine. Then `python3 scripts/cycle.py freeze NNN` and
> commit.
>
> **Move 4.** I will switch the model. You build against the frozen criteria,
> appending DEVIATION entries as they occur rather than from memory afterwards.
>
> **Move 5.** Write the outcome and confidence sections, including your own
> regressions by name.
>
> Binding throughout: do not break the product · never alter a verification,
> fixture or scorer to make a number look better · measure before and after
> anything that could move a metric · report regressions including your own ·
> commit per item with the reasoning in the message · ask before reversing a
> decision I made (§4.3 lists them). Be blunt; do not soften findings.
>
> Check for other running work before any measurement — `ps aux | grep -E
> "bench_|check_|diagnose_"` — and ask me. Two sessions in one repo has
> contaminated a run before and no in-repo guard can see it.
