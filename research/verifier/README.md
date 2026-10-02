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
