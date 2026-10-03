# Study protocol: do data-level checks tell you when to trust a text-to-SQL answer?

Status: protocol, written 2026-09-30 **before any result was measured**. Changes after
data collection starts are logged at the bottom with the reason.

Target: a short paper (workshop, regional conference or a short/demo track). One clear
finding with honest limits beats a broad claim.

## 1. Question

A text-to-SQL system answers some questions wrongly and cannot tell which. Recent work
predicts correctness from the SQL text or the model: self-consistency, log-probabilities,
LLM judges (Richardson 2026, arXiv 2607.06799: black-box signals 0.61–0.68 AUROC, a
Claude judge 0.78, a two-provider judge ensemble 0.82).

StateSync instead checks the **answer against the data**, with no model:

| Signal | What it checks | Module |
|---|---|---|
| Evidence tier | verified (approved SQL as is) / compiled (template, values filled in code) / checked / unchecked | `orchestrator/evidence.py` |
| Fan-out probe | a join that repeats rows under SUM/AVG (COUNT(key) vs COUNT(DISTINCT key)) | `orchestrator/answer_checks.py` |
| Truncation / NULL-heavy | a result cut at the row limit, aggregates over mostly-NULL columns | `answer_checks.py` |
| Figure grounding | every number in the prose traced to a cell, a total, the question or a passage | `orchestrator/number_grounding.py` |
| Metric reconciliation (to be built) | a free-form answer disagreeing with the owner's approved metric on the same filters | planned |

**RQ1.** As a signal for "this answer may be wrong", how do the data-level checks compare
with model-based confidence, on accuracy of the warning and on cost?
**RQ2.** Which check catches which errors (per-check ablation)? Which errors does none catch?
**RQ3.** Do the checks raise false alarms on correct answers, and how often?
**RQ4 (added 2026-10-03, before any BIRD run).** When a check flags an answer, does ONE
repair attempt that is told exactly what failed turn wrong answers into right ones, how
often does it break a right one, and what does it cost?

Failure types used throughout (owner's framing, 2026-10-03): **grounded** (is every figure in
the answer actually in the result?), **correct** (did the query answer the question: right
table, filter, time window, no double counting?), **intended** (is it what the user meant:
net or gross, fiscal or calendar?). Data checks are expected to see the first two only.

## 2. Hypotheses (stated before measuring)

- H1. On aggregation and join errors, the data checks have higher precision than an LLM
  judge at equal or lower cost. On other error types (wrong filter value, wrong column
  meaning), they catch little; the judge does better there.
- H2. Combining checks with one judge beats either alone on risk–coverage.
- H3. Figure grounding has a low false-alarm rate (< 5% of correct answers flagged) after
  the list-marker fix; its catches are mostly prose errors, not SQL errors.

- H4. A repair attempt triggered by a data check, with the failure stated in the prompt,
  fixes more wrong answers than it breaks right ones, and raises end-to-end accuracy by at
  least 2 points on BIRD at under 0.5 extra model calls per question on average. A repair
  triggered only by "are you sure?" (no stated failure) fixes fewer (self-verification
  without new information is weak).

Any outcome is reportable. "The checks catch few real errors on BIRD" is a result.

## 3. Data

1. **BIRD Mini-Dev, PostgreSQL version** (500 questions, 11 databases; CC BY-SA 4.0;
   https://github.com/bird-bench/mini_dev), scored against the **corrected annotations** of
   Jin et al. 2026 (arXiv 2601.08778; https://github.com/uiuc-kang-lab/text_to_sql_benchmarks,
   "Arcwise-Plat-SQL" corrects SQL only, keeping questions as posed). The originals have
   annotation errors in about half the questions, so the original gold is used only as a
   sensitivity check.
2. **A fan-out set** built from the same databases: questions whose natural SQL joins a
   one-to-many table under SUM/AVG/COUNT(*), with gold SQL written and executed by hand.
   Target 60–100 questions. Needed because BIRD rarely exercises fan-out.
3. **The StateSync set** (30 questions: olist, the synthetic corpus, follow-ups), as a
   case study only; too small for statistics.

## 4. Conditions

The system under test is the StateSync pipeline at shipped flags, with `answer_checks=on`
and number grounding on. Model: Groq gpt-oss-120b for SQL (as the baseline run).

Signals compared, each producing a "warn" decision and, where possible, a score:

| Family | Signal |
|---|---|
| Data checks (ours) | tier; each check alone; any check; checks + tier |
| Model-free baselines | executes without error; empty result; row count out of range |
| Model-based baselines | verbalised confidence; self-consistency over k=5 samples (execution-result agreement); LLM judge (one provider); judge ensemble (two providers) |
| Combined | checks OR judge; logistic combination (cross-validated) |

**Repair loop (RQ4).** Fixed now, not tuned on BIRD:
1. Answer, then run the checks. No flag → done.
2. Flag → one repair call (same SQL model) whose prompt states the failing check and its
   evidence (e.g. "the answer says 97.0%, which is not in the result"; "this join repeats rows
   of orders: 1,204 joined rows for 1,000 orders"). Then the checks run again.
3. Passes → the repaired answer is shown, marked as corrected. Still flagged → the original
   answer is shown with a "might be wrong" label. Never more than one repair.
4. Variants: (a) no loop; (b) loop triggered by data checks with evidence (ours);
   (c) loop triggered by data checks but with only "check your answer" (no evidence);
   (d) loop triggered by the LLM judge. The classifier verifiers (Laya and variants) are not
   triggers: on real errors they reached only 0.52–0.73 AUROC (`research/verifier`).

## 5. Measures

- Correctness label: execution match with the corrected gold (order-insensitive unless the
  question asks for an order), then **manual review of every disagreement between the
  label and the checks**, recording the error type (join/fan-out, filter value, column
  meaning, aggregation, time window, prose-only).
- Warning quality: precision, recall, F1 of "warn" for wrong answers; AUROC where a score
  exists; risk–coverage curve and selective accuracy at 80% and 90% coverage.
- False alarms: share of correct answers warned, per check.
- Cost: extra model calls and tokens per question for each signal (checks: zero).
- Latency added per signal.
- Repair loop (RQ4): accuracy before and after; fixed (wrong → right), broken (right → wrong),
  unchanged; extra calls and latency per question; share of answers ending "might be wrong".

## 6. Analysis plan

- Main table: each signal's precision/recall/AUROC/cost on (1) and (2).
- Per-error-type catch matrix (RQ2).
- Bootstrap 95% confidence intervals (1,000 resamples by question).
- Report the original-gold numbers alongside as a sensitivity check.

## 7. Budget and schedule

Model calls, approximate: pipeline ~5 calls/question → ~3,000 for 600 questions;
self-consistency k=5 → ~3,000 SQL calls; judges → ~600–1,200 calls on two providers.
About 15–25M tokens in total; the free Groq tier (200K tokens/day per model) is far too
small, so this needs paid credits (owner offered). Estimate cost from the providers'
current price pages before starting; do not assume.

Weeks: (1) data load + gold execution + fan-out set; (2) pipeline runs + checks;
(3) baselines; (4) manual review of disagreements + analysis; (5–6) writing.

## 8. Threats to validity

- The checks were designed with the StateSync data in view; BIRD is the out-of-sample test.
- The fan-out set is built by us; report it separately and publish it.
- One SQL model; a second model for SQL is optional (a robustness row), not the main result.
- Execution-match labels have their own errors; manual review of disagreements limits this.

## 9. Change log

- 2026-09-30: protocol written. Known before measuring: a live answer's numbered-list
  markers ("1.", "2.") were read as figures; fixed in `number_grounding.py` (commit 590c59a).
- 2026-10-03 (owner): RQ4, H4, the repair-loop conditions and measures, and the
  grounded / correct / intended failure types added. BIRD Mini-Dev downloaded the same day;
  no BIRD question has been run. The fan-out check now also covers COUNT (commit adf27ba),
  which is the version every BIRD run uses. Providers: measurement runs use
  `flags.llm.openai_compat.fallback = "off"`, and every call records the model the server
  says answered (`llm/adapters/openai_compat_adapter.py`).
