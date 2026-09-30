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

## 2. Hypotheses (stated before measuring)

- H1. On aggregation and join errors, the data checks have higher precision than an LLM
  judge at equal or lower cost. On other error types (wrong filter value, wrong column
  meaning), they catch little; the judge does better there.
- H2. Combining checks with one judge beats either alone on risk–coverage.
- H3. Figure grounding has a low false-alarm rate (< 5% of correct answers flagged) after
  the list-marker fix; its catches are mostly prose errors, not SQL errors.

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
