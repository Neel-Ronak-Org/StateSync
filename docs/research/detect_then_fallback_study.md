# Detect, then fall back: candidate selection and verified labels for text-to-SQL (pre-registration)

Status: **pre-registered 2026-10-05, before any candidate below was generated or scored.** Changes are
logged at the end with a date and whether any result informed them. Paper B. It builds on the
evidence-tiers study (`evidence_tiers_study.md`, "Paper A") and the setup-time verifier pilot
(`research/verifier/README.md`).

## Why

Paper A's exploratory fallback simulation found two things:
- A right SQL candidate often exists when the answer is wrong. For GPT-5 mini's 393 held-out answers,
  a right candidate exists for 28 of the 90 wrong ones (a ceiling of 84.2%, against 77.1%).
- Majority voting captures little of that headroom (+1.5 points). For a model that agrees with itself
  (gpt-oss-120b), voting changes nothing.

The verifier pilot found that a classifier trained on synthetic SQL mutations does not transfer to real
LLM errors, while training on real candidates helps.

**The question.** Can a cheap trained verifier:
- **select** the right candidate from a pool drawn from several models,
- **trigger** a fallback only when the first answer is doubtful, and
- give a **"verified" label** with a stated error rate,

better than voting or an LLM judge, and at what cost?

## Data

- **Questions:** the 393 held-out BIRD Mini-Dev questions of Paper A (`docs/eval/bird_heldout_sample.json`),
  asked in the corrected file's wording.
- **Labels:** corrected gold (Jin et al. 2026), run on SQLite. A candidate runs on the PostgreSQL copy and
  is labelled correct under the relaxed rule (`eval_bird.rows_match_relaxed`, `norm_v2`), exactly as in
  Paper A. A candidate that fails to run is wrong.
- The 100 pilot questions stay development data and are excluded.

**Candidate pool, 21 per question:**

| source | per question | how |
|---|---|---|
| GPT-5 mini (Zed) | 6 | the pipeline answer + its 5 self-consistency samples (existing, Paper A) |
| gpt-oss-120b (Cheaper Inference) | 6 | the same, from Paper A's replication (existing) |
| deepseek-v4.1-flash, qwen3-coder-30b-a3b-instruct, glm-5.3-flash (Cheaper Inference) | 3 each | a standalone prompt (schema of the question's database, hint, question; PostgreSQL SQL only), default sampling (`scripts/paperb_candidates.py`) |

The three new models are from families other than the judge's (Gemini). Generation settings are fixed
before any candidate is scored.

## Signals and selectors

Each candidate is scored by:
1. **Majority vote:** the size of its result cluster (candidates whose results agree under the relaxed rule, both ways).
2. **LLM judge:** Gemini 3.8 Flash (Zed), the same prompt as Paper A, P(correct). It is scored once per
   distinct result cluster per question, on the cluster's first candidate.
3. **Trained verifiers**, with input `question + SQL + result preview` (row count, columns, first 3 rows):
   - **ModernBERT-base**, fine-tuned as a binary classifier;
   - **Qwen3-0.6B** with a LoRA classification head;
   - **Laya**, fine-tuned (the pilot's recipe, cross-entropy);
   - **Laya hosted, zero-shot** (Vercel AI Gateway);
   - **Jev, zero-shot** (Vercel AI Gateway), if paid credits are available. Otherwise it is reported as not run.
4. **Training regimes for the fine-tuned verifiers:**
   - **real:** candidates of training questions, labelled by execution;
   - **mutation:** rule-based mutations of the training questions' gold SQL (the pilot's generator), labelled by execution.

**Out-of-fold scoring (same schema).** The 393 questions are split into 5 folds, stratified by database,
seed 20261005. Each candidate is scored by verifiers trained on the other 4 folds. This simulates
setup-time training on other reference questions from the same databases.

**Unseen schema (secondary).** Leave one database out, for ModernBERT only (compute).

## Measures

- **Selection accuracy:** per question, the share whose selected candidate is correct (ties broken by
  the first candidate in pool order). Selectors:
  - the first answer (GPT-5 mini's pipeline answer);
  - majority vote;
  - judge-max;
  - each verifier's max;
  - verifier + vote (verifier score summed per cluster);
  - **oracle** (any correct).
- **Detection:** AUROC and AURC of each signal for the first answer.
- **Cascade:** keep the first answer unless its verifier risk exceeds a threshold, else select. The
  threshold is chosen on the training folds to maximise training accuracy. Reported: accuracy, the share
  of questions falling back, and extra calls and cost.
- **Verified label:** split-conformal on out-of-fold scores. Label an answer "verified" when
  P(correct) ≥ t, with t chosen on the training folds so that the verified answers' error is ≤ 5%
  (Clopper-Pearson upper bound). Reported: verified coverage and observed error on the held-out fold.
- **Cost and latency per check:** local verifiers measured on this Mac; API judges at list price.

## Confirmatory hypotheses (Holm across H1–H3, α = 0.05)

- **H1 (selection).** The best fine-tuned verifier (chosen by mean out-of-fold training-fold accuracy, not
  test accuracy) selects a correct candidate more often than majority vote. McNemar, one-sided.
- **H2 (training signal).** For ModernBERT, the real-candidate regime gives higher selection accuracy than
  the mutation regime. McNemar, one-sided.
- **H3 (cascade).** The verifier-triggered cascade is more accurate than the first answer alone. McNemar,
  one-sided.

**Reported but not tested:**
- judge-max vs vote;
- hosted zero-shot models;
- unseen-schema results;
- the verified label's coverage at a 5% error target;
- per-model breakdowns.

## Threats

- **Candidate pool:** pipeline candidates (GPT-5 mini, gpt-oss) and standalone candidates differ in
  prompt. The pool is a pool; selection does not depend on the source.
- **Contamination:** models may have seen BIRD. This affects all selectors alike.
- **Overlap with Paper A:** the same 393 questions and two of its runs are reused. Paper B's contribution
  is selection, fallback and verification, not detection, and it cites Paper A.
- **Hosts:** Cheaper Inference does not disclose its upstream provider (Paper A, change 16). The served
  model name is recorded per call.

## Change log

1. **2026-10-05, during candidate generation, before any candidate was scored.** The generation budget
   is raised from 3,000 to 8,000 tokens for all three standalone models. deepseek-v4.1-flash reasons
   before answering, and on 11 of its first 45 calls it used the whole budget and returned no text.
   Calls that hit the old cap with no SQL are regenerated with the new budget. Calls whose model
   answered without SQL keep that answer (it counts as a candidate that fails). No result informed
   this.
