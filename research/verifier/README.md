# Setup-time verifier: feasibility study (pre-registered 2026-10-02)

## Question

Can a small decision model, fine-tuned during workspace setup on labels the customer's own
database produces, tell correct SQL from wrong SQL on that schema? Is it reliable enough to
put a "verified / could be wrong, please check" label on every answer, at a fraction of an LLM
judge's cost?

## Why this is worth testing

- **Where trained verifiers stand.** Richardson (2026, arXiv:2607.06799) found trained SQL
  verifiers reach AUROC 0.77–0.80 on the schemas they were trained on, but only 0.66–0.67 on
  unseen ones. The authors call fine-tuning useful "for fixed deployments that own their
  schemas and can label examples from them". StateSync is such a deployment.
- **The open question.** Can the labels come from the database itself, at setup, without
  people labelling?

The central question is whether a verifier trained on synthetic mistakes (mutated correct
SQL, labelled by running it) catches the mistakes a real LLM makes. The study is worth
reporting whichever way it comes out.

## Data (FIBEN, `docs/eval/external_datasets.md`; local copy from `demo/fiben_db.sh`)

- **Items.** A question is kept if its gold SQL, with identifiers lower-cased for the Postgres
  port, runs within 20 s and returns at least one row. An empty gold result would let any
  query that returns nothing count as "correct".
- **Split, fixed before any training.** Questions are grouped by normalised gold SQL, so a
  paraphrase never sits on the other side of the split from its original. 70% of groups go to
  train and 30% to test (seed 20261002).
- **Correctness label.** A candidate SQL is correct when its result multiset equals the gold
  result multiset. Cells are normalised: numbers to 4 significant figures, text trimmed and
  lower-cased. Row and column order are ignored. Candidates that fail to run are excluded,
  because the product already catches those by running them.

## Candidates

| set | source | used for |
|---|---|---|
| `train` | gold SQL of the train questions, plus rule-based mutations of it (comparison operator, numeric constant, aggregate, a dropped AND condition, sort direction, DISTINCT, join type), each labelled by running it | training (10% held out for temperature calibration) |
| `test_mut` | the same mutations on the test questions | in-distribution check |
| `test_llm` | SQL a real LLM (Groq) writes for the test questions, given the schema of the tables the gold SQL uses plus 3 distractor tables, each labelled by running it | **the primary test: real mistakes** |

## Verifier input (identical for every model)

`QUESTION`, `SQL`, and `RESULT`: row count, column names and the first 3 rows, truncated to
fit Laya's 1,024-token window. The question is one 2-way choice, correct or wrong. The
confidence used is P(correct).

## Signals compared on `test_llm` (and `test_mut`)

1. Laya typed-decisions, zero-shot. Checkpoint revision `55cf4c4e`.
2. Laya fine-tuned at "setup" on `train`: Convai's recipe (RLCD + cross-entropy, temperature
   refit), as ported in jev-demos.
3. The same, with cross-entropy only and no RLCD term. This is the ablation: is it the
   decision-model training, or just a fine-tuned encoder?
4. LLM judge: gpt-oss-120b on Groq, asked the same question, P(correct) from a 0–100 rating.
5. Cheap heuristics: result is empty, and row count.

**Metrics.** AUROC, as in Richardson; ECE (10 bins); and "verified precision". The verified
precision is computed by fixing a threshold on the calibration split for a target error rate
on the answers labelled verified (5%), then reporting what share of `test_llm` gets the label
and how many of those are actually wrong. Also latency per check.

## Pre-registered expectations (written before any run)

- **Zero-shot Laya:** near chance (AUROC ≈ 0.5), as its own model card says for unseen tasks.
- **Fine-tuned Laya on `test_mut`:** high, because it is in distribution.
- **On `test_llm`, the real test:** lower than on `test_mut`. If it stays at or above the LLM
  judge's AUROC, the setup-time verifier is viable. If it falls toward 0.6, synthetic
  mutations do not teach real mistakes, and the next step is adding the generator's own
  candidates to training.
- **RLCD:** it may not beat cross-entropy-only on AUROC, but should give better calibration.

## Limits (known now)

- One schema in this feasibility run. BIRD Mini-Dev (11 databases) would allow a
  same-schema against unseen-schema comparison, but needs a download approved separately.
- A few hundred questions. FIBEN's synthetic people and holdings data leaves many gold
  results empty, and those are dropped.
- The LLM candidates are written with a schema that already contains the gold tables (oracle
  schema linking), so they test SQL logic errors, not table-selection errors.

## Deviations (recorded as they happen)

1. **2026-10-02, label rule, before any model was scored.** The first three LLM candidates
   showed that FIBEN's gold SQL often projects extra identifying columns (ticker, legal name)
   the question did not ask for. An exact multiset match would mark a right answer with fewer
   columns wrong.
   - The primary label is now `data.relaxed_correct`: same row count; every candidate column
     equals a distinct gold column; every gold aggregate column is matched.
   - `correct_strict`, the exact match, is kept and reported beside it.
   - Seeing those three candidates did not involve any verifier.

2. **2026-10-02, generator context, before any verifier was scored.** With the schema alone,
   the LLMs got almost every test question wrong: 2 of 45 runnable for gpt-oss-120b and 2 of
   42 for qwen3.8-27b, under the relaxed label. That leaves too few positives to measure a
   verifier, since always answering "wrong" would score 95%.
   - The primary `test_llm` set now gives the generator what a StateSync workspace gives it:
     FIBEN's declared foreign keys among the shown tables, 2 sample values per text column, and
     3 approved examples. The examples are the train questions sharing the most tables, mirroring
     a workspace's approved examples.
   - The bare-prompt sets (`test_llm_*.jsonl`) are kept as a secondary, near-all-wrong condition.

3. **2026-10-02, a second training variant, declared before any verifier was scored.**
   - Variant A (pre-registered) trains on gold SQL plus mutations.
   - Variant B adds qwen3.8-27b's own rich-context SQL for the train questions, each labelled by
     running it (`train_llm_qwen3.8-27b_rich.jsonl`). A question's own group is never among its
     prompt examples.
   - Both are trained and scored with the same recipe. The A-against-B comparison on
     `test_llm` is the central result: does training on synthetic mistakes teach real ones?

## Results: first run (2026-10-02, FIBEN, one schema)

AUROC, with the bootstrap 95% interval in brackets. Full metrics are in
`results/metrics_2026-10-02.jsonl`. `test_llm` uses the rich context (deviation 2): 46
candidates per generator, 37% and 35% correct.

| signal | test_mut (481) | real gpt-oss (46) | real Qwen (46) |
|---|---|---|---|
| non-empty result | 0.62 | 0.59 | 0.63 |
| Laya zero-shot | 0.63 | 0.62 [0.43, 0.79] | 0.73 [0.57, 0.88] |
| A, RLCD (mutants) | **0.86** [0.83, 0.90] | 0.52 [0.34, 0.69] | 0.60 [0.42, 0.76] |
| A, CE only | **0.86** [0.82, 0.89] | 0.53 [0.37, 0.70] | 0.73 [0.57, 0.87] |
| B, RLCD (mutants + 83 real Qwen) | 0.82 [0.79, 0.86] | 0.60 [0.43, 0.77] | **0.79** [0.64, 0.90] |
| LLM judge (gpt-oss-120b) | not run | 0.66 [0.50, 0.81] | 8/46 judged; Groq daily limit |

What the run shows:

- **Mutants teach mutant detection.** Trained on mutants, the verifier is strong on mutants
  (0.86). On real LLM errors it is at chance on gpt-oss and below zero-shot on Qwen.
  Pre-registered expectation confirmed.
- **The gain is not purely shortcut learning.** Within a single mutation type, AUROC stays
  0.74–0.88, so the model learns something about whether a small edit changes the result. Real
  errors are a different kind: wrong tables, invented values, misread columns.
- **Real candidates help on the generator they come from.** Adding 83 real Qwen candidates
  (7% of training) lifts the Qwen test set from 0.60 to 0.79, the best of any signal, and the
  gpt-oss test set only from 0.52 to 0.60. That matches the generator-specific outcome
  anticipated before the run.
- **RLCD is no better than plain cross-entropy here.** It does worse on real Qwen errors
  (0.60 against 0.73).
- **Calibration is poor.** Temperatures of 5.6, 3.6 and 10.0 (the clamp limit) were fitted on a
  mutant calibration slice, and ECE is 0.16–0.31. No threshold yet supports a "verified" label.
- **Small samples.** Every `test_llm` interval spans about 0.3, so no difference between the
  signals on real errors is statistically clear yet.
