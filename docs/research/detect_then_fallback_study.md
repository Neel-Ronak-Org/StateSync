# StateSync system paper: document answers and SQL fallback, measured (Paper B protocol, version 2)

Status: **version 2, 2026-10-05.** Paper B, re-scoped by the owner after an independent audit. Written
before any verifier was trained, before any judge or decision model scored the pool, and before any
standalone candidate was executed. Version 1 (commit `4ed5805`; changes 1–2 in `724f9d7` and `88ca99c`)
is summarised in §12, and its full text is in git history.

**Correction to version 1.** Its status line said it was written "before any candidate below was
generated or scored". That held for the 9 standalone candidates per question. It did not hold for the 12
pipeline candidates (GPT-5 mini and gpt-oss): Paper A's exploratory fallback simulation had already
executed and scored them. Version 2 is written knowing these results about the SQL candidates:
- the single-answer accuracy of both pipeline models (Paper A: GPT-5 mini 77.1%; gpt-oss 77.9% at
  temperature 0), and Paper A's detection results for GPT-5 mini's answers;
- a majority vote over GPT-5 mini's 6 candidates, triggered when Paper A's judge doubts the answer:
  +1.5 points; some candidate of the 6 is right for 84.2% of questions;
- from the audit: switching from GPT-5 mini's answer to gpt-oss's temperature-0 answer when Paper A's
  judge doubts it gives 77.9%; the other way round, 79.9%; one of the two answers is right for 83.7%.

**Type and venue.** An applied system paper for MoSICom 2026 (IEEE; deadline 15 October 2026). It
describes StateSync and measures two of its parts. It claims no new method: every technique here has
prior work (§10).

## 1. What the paper reports

1. **The system** (descriptive): one assistant over a relational database and uploaded documents; the
   credential-holding MCP service and the three-layer SQL gate; routing; the SQL agent; document search
   with re-ranking; the answer writer; evidence tiers and checks.
2. **Part D, document answers** (FinanceBench): retrieval with and without the re-ranker (done,
   `docs/eval/rag_v3_report.md`) and, new here, the answers those searches lead to.
3. **Part S, SQL answers** (BIRD): what falling back recovers when the first answer may be wrong, how to
   trigger it and choose the replacement, and what it costs.
4. **Cost and latency** of each part.

## 2. Questions

- **QD1.** Does the re-ranker that raised page hit@5 from 43.3% to 59.3% also raise answer accuracy, and
  at what latency?
- **QD2** (descriptive). How accurate are answers when a gold page was, and was not, among the passages
  the answer writer was given?
- **QS1.** How much accuracy do fallback policies recover on the 393 held-out BIRD questions, out of how
  much is available, and at what extra cost?
- **QS2.** Can a small model trained at setup on labelled questions from the same databases (a plain
  classifier, ModernBERT; or a decision model, Laya) replace the LLM judge as the trigger and as the
  selector? How do decision models do with no training (Laya, Jev)?
- **QS3.** Given the same labelled questions, does more accuracy come from showing them to the SQL writer
  as approved examples (an existing StateSync feature) or from training a verifier on them?
- **QS4.** What share of first answers can carry a "verified" label at a 5% error target, measured out of
  sample?

## 3. Data

**Part D: the FinanceBench open sample** (150 questions, 84 filings; `docs/eval/external_datasets.md`).
- The RAG v3 ingest: paragraph chunker, all 84 filings ready (44,918 chunks). `[library] max_pages` is
  600, raised from 300 by the owner on 2026-10-02 so that the six longest filings could be read.
- Questions verbatim. Question types: metrics-generated, domain-relevant, novel-generated (50 each).
- Gold: FinanceBench's answer and justification for each question.

**Part S: the BIRD Mini-Dev held-out set** (393 questions; `docs/eval/bird_heldout_sample.json`), as in
Paper A:
- asked in the corrected file's wording;
- labelled with the corrected gold (Jin et al. 2026) run on SQLite against predictions run on
  PostgreSQL, under Paper A's relaxed rule (numbers to 5 significant digits);
- a candidate that fails to run is wrong;
- the 100 pilot questions stay excluded.

## 4. Part D method (documents)

**Runs.** The 150 questions go through the full pipeline twice, in one workspace linked only to the
FinanceBench folder:
- **D-dense:** `[pipeline] rerank = "off"`;
- **D-bge:** `rerank = "bge"` (shipped).

Everything else is as shipped: `retrieval_mode = "dense"`, `query_rewrite = "off"`, `doc_abstain = "off"`,
paragraph chunker. Only the hosts differ:
- the answer writer is gpt-oss-120b (the shipped model) through Cheaper Inference, reasoning effort low;
- the light roles are qwen3.8-27b through Cheaper Inference, thinking off (Paper A, change 16);
- reason: Groq's free tier cannot take 300 answers in the time available;
- the served model is recorded per call.

The route is the product's own decision; a question sent down another route is scored as answered.

**Grading.** Gemini 3.8 Flash (Zed) grades each answer against FinanceBench's answer and justification.
- Verdicts: correct, partly correct, incorrect, refused. The primary label is "correct".
- A number counts when it agrees with the gold within 1%, or within the rounding the gold shows.
- The grader sees the question, the gold answer, the justification and the system's answer, but not the
  configuration. The 300 answers are graded in one shuffled order (seed 20261005).
- The grader prompt is fixed, and committed, before any answer is graded.

**Human check.** The owner grades 30 answers drawn at random from the 300, blind to the grader and the
configuration. Agreement with the grader is reported. Below 80%, the paper says so next to every Part D
number.

**Measures.**
- answer accuracy per configuration, overall and by question type, with the paired difference (exact
  McNemar, two-sided) and a bootstrap interval (2,000 resamples, seed 20261005);
- refusals; latency per answer (p50, p90); model tokens;
- QD2: accuracy split by whether any passage given to the answer writer is on a gold page (from the
  run's record of passages; if a run records only citations, citations are used and the paper says so).

Expected, not hypothesised: a smaller gain in answers than in retrieval.

## 5. Part S method (SQL)

**Candidate pool: 21 per question, in this order.**

| source | per question | sampling |
|---|---|---|
| GPT-5 mini (Zed): the pipeline answer, then its 5 self-consistency samples (Paper A) | 6 | provider default (the model accepts no temperature) |
| gpt-oss-120b (Cheaper Inference): the pipeline answer, then its 5 samples, from Paper A's rerun (`heldout_gptoss`, change 18) | 6 | provider default. The temperature-0 run (`heldout_gptoss_t0`) is not used. |
| deepseek-v4.1-flash, qwen3-coder-30b-a3b-instruct, glm-5.3-flash (Cheaper Inference), standalone prompt (`paperb_candidates.json`) | 3 each | provider default, no temperature sent |

Candidates whose results agree under the relaxed rule, in either direction, form a cluster.

**Folds.** The 393 questions are split into 5 folds stratified by database, seed 20261005, by
`paperb_verifiers.folds` as committed in `88ca99c`. Round r:
- tests fold r;
- calibrates on fold (r + 1) mod 5;
- trains on the other three.

Every question is tested once, by a model that saw neither it nor its round's calibration fold. Every
threshold comes from the calibration fold of the same round.

**Signals.**
1. **ModernBERT verifier** (primary): ModernBERT-base, full fine-tune, with version 1's hyperparameters
   (learning rate 3e-5, 3 epochs, batch 16, input capped at 512 tokens; about 0.4% of inputs are longer).
   Input: the question, the SQL and the result preview (row count, columns, first 3 rows). Two regimes:
   - **real** (primary): the training folds' pool candidates, labelled by execution;
   - **mutation** (secondary): the training folds' reference SQL and its rule-based mutants
     (`paperb_mutations.json`).
2. **Laya, fine-tuned at setup** (secondary): the verifier pilot's code and recipe
   (`research/verifier/laya_verifier.py train --loss rlcd`: Laya's RLCD objective plus cross-entropy,
   effective batch 64, correct and wrong weighted equally, temperature fitted on 10% of the training
   questions), from the pinned checkpoint (`convaiinnovations/laya`, `typed-decisions`, revision
   `55cf4c4e`), on the real regime, in the same rounds.
   - **One epoch**, not the pilot's four: a round has roughly 3–4 times the pilot's 1,118 training rows,
     so one epoch gives about as many updates. With one epoch, the RLCD noise scale stays at its starting
     value (0.4) instead of annealing to 0.1.
   - Same input content as the ModernBERT verifier, in Laya's QUESTION / SQL / RESULT format.
3. **LLM judge:** Gemini 3.8 Flash (Zed).
   - One call per distinct result cluster, on the cluster's first candidate.
   - It is shown the question with its hint, the tables, the SQL and the first 10 rows.
   - System prompt, fixed here: *"You check SQL written for a question about a PostgreSQL database. You
     are given the question (with an optional hint), the database's tables and columns, the SQL, and the
     first rows of its result. Rate how likely it is that the result correctly answers the question, from
     0 (certainly wrong) to 100 (certainly right). Reply with JSON only: {"score": <integer 0-100>,
     "reason": "<one sentence>"}."* Paper A's judge, asked for a probability, gave 1.0 to 328 of 393
     answers, so most selections would have been ties.
   - A candidate that failed to run scores 0 without a call.
4. **Decision models with no training:**
   - **Laya, zero-shot:** the same pinned checkpoint, run locally, with the pilot's question
     ("Does the SQL correctly answer the QUESTION?", options correct and wrong);
   - **Jev, zero-shot:** through Vercel AI Gateway (the owner is adding credits), one yes/no question per
     distinct candidate over the verifier's input; the model version returned is recorded.
5. **Vote:** the size of the candidate's cluster.

**Policies.** Each policy gives one answer per question.

| | policy | extra work |
|---|---|---|
| P0 | first answer: GPT-5 mini's pipeline answer | none |
| P1 | judge-triggered re-vote: if the judge scores P0 below 50, the majority over GPT-5 mini's 6 | 5 samples + judge, on trigger |
| P2 | judge-triggered escalation: if the judge scores P0 below 50, gpt-oss's pipeline answer | one more pipeline run + judge, on trigger |
| P3 | pool vote | all 21 |
| P4 | pool judge-max | all 21 + judge |
| P5 | pool verifier-max: ModernBERT, real regime | all 21 + verifier |
| P5m | pool verifier-max: ModernBERT, mutation regime | all 21 + verifier |
| P5v | ModernBERT + vote: scores summed per cluster | all 21 + verifier |
| P5L | pool verifier-max: Laya fine-tuned | all 21 + Laya |
| P6 | ModernBERT-triggered cascade: P0 unless its verifier risk exceeds τ, else P5. τ maximises accuracy on the calibration fold. | verifier always; all 21 on trigger |
| P7 | judge-triggered cascade: P0 unless the judge scores it below 50, else P4 | judge always; all 21 on trigger |
| – | oracle: right if any candidate is right | the ceiling |

The threshold 50 is fixed here, not tuned. Ties between top-scoring clusters are scored as the expected
accuracy under uniform random tie-breaking (exact), for every selector, votes included.

**Approved examples against a verifier (QS3).** gpt-oss-120b (Cheaper Inference, provider default
sampling, reasoning effort low) with the standalone candidates' prompt, in three arms, **three samples per
question per arm**:
- **E0:** schema, hint, question;
- **E1:** the same, plus up to 3 approved examples chosen as the product chooses them (similarity at least
  0.3). This gives 54 of the 393 questions at least one example;
- **E2:** the same, plus the 3 most similar approved examples with no minimum similarity (change 4).

The examples:
- are ranked as StateSync ranks them (`mcp/tools/workspace_tools.py`: Jaccard similarity of content
  words; E1 keeps those at least 0.3, at most 3; E2 keeps the top 3), comparing question texts without
  their hints, an exact match left out;
- come from the same database's questions in the round's three training folds;
- are shown as the product shows them ("Approved examples of similar questions and their SQL"), each a
  question without its hint and its SQL;
- use a question only if its PostgreSQL reference SQL is correct under the corrected labels (the
  `reference` rows of `paperb_mutations.json`).

A question's accuracy in an arm is the share of its three samples that are right. Reported: E1 − E0 (the
product as configured) and E2 − E0 (examples whenever the database has any), with paired bootstrap
intervals, next to the verifiers' gains (P5 − P3 and P6 − P0). All of them come from the same labelled
training folds.

**Verified label (QS4).**
- Per round, a threshold on the first answer's score is set on the calibration fold by fixed-sequence
  testing. Start at the highest score and keep lowering the threshold while the verified set's one-sided
  95% Clopper–Pearson upper error bound stays at or below the target; stop at the first failure.
- The threshold is applied to the test fold.
- Targets: 5% (primary) and 10%.
- Scores: ModernBERT (real), Laya fine-tuned, and the judge.
- Reported: coverage and observed error over all test folds.
- With about 79 calibration questions per round the bound is strict, so coverage may be small. It is
  reported as it is.

**Measures.**
- the accuracy of every policy, with paired bootstrap intervals against P0 (2,000 resamples stratified
  by database, seed 20261005);
- the share of questions that trigger a fallback;
- extra model calls, tokens, list-price cost and added latency per question, including generating the
  candidates a policy uses;
- detection of P0's errors: AUROC and AURC of ModernBERT, Laya fine-tuned, Laya zero-shot, Jev and the
  judge;
- the accuracy of each pool source.

**Pre-specified comparisons.** Version 1's H1–H3 are kept, tested with one-sided McNemar and Holm across
the three:
- **H1:** P5 > P3;
- **H2:** P5 > P5m;
- **H3:** P6 > P0.

H2 and H3 are expected to hold (H2 from the verifier pilot; H3 because P6 sees 20 more candidates than
P0). The paper does not rest on them. Every Laya and Jev result is descriptive.

## 6. Cost and latency

- model calls: list price per million tokens, as published on the run date, with tokens recorded per
  call;
- local: training minutes and milliseconds per check on this Mac's GPU for ModernBERT and Laya; bge
  milliseconds per search (Part D);
- reported per 1,000 questions.

## 7. Overlap with Paper A

Paper A (to ARR, 12 October) is cited. Part S reuses its held-out questions, its labels and its two
pipeline runs, and reports none of its detection or data-check results. If the owner agrees, Paper A's
exploratory fallback simulation moves to this paper, so that the result is not published twice.

## 8. Deferred (not in this paper)

- **A verifier trained on another company's database** (the FIBEN-trained Laya checkpoints scored on
  BIRD). The owner's view: a deployment would train on its own data.
- Qwen3-0.6B (LoRA) and Laya with cross-entropy only; leave-one-database-out.
- A fallback for documents that do not answer the question (abstention or a wider search).

## 9. Threats

- one benchmark per part (BIRD, FinanceBench), English only; the models may have seen both;
- an LLM grades Part D (with a human check on 30 answers);
- small calibration folds; hyperparameters are not tuned;
- Laya is larger than ModernBERT-base (421M against 149M parameters) and is trained by its own recipe,
  so the two are not a controlled comparison of architectures; its one epoch and untuned noise schedule
  may undersell it;
- the pool mixes pipeline candidates (StateSync's prompt and retries) with standalone candidates;
- hosts:
  - GPT-5 mini and Gemini are reached through a local router to an editor subscription (Zed);
  - gpt-oss and the standalone models are reached through Cheaper Inference, which does not name its
    upstream;
  - Jev is reached through Vercel AI Gateway;
  - the served model is recorded per call;
- the gpt-oss pipeline answers will have been scored for Paper A before the pool is built.

## 10. Prior work the paper positions against

- verifier re-ranking with execution results: LEVER (Ni et al., 2023);
- execution-labelled outcome reward models for text-to-SQL that beat majority vote: GradeSQL (Tritto et
  al.);
- trained candidate selection: CHASE-SQL (Pourreza et al., 2025);
- parser-independent error detection for re-ranking and triggering (Chen et al., 2023);
- trained verifiers on and off their training schemas (Richardson, 2026);
- LLM cascades: FrugalGPT; Mixture-of-Thought cascades (Yue et al., 2024); AutoMix;
- retrieval evaluators that trigger corrective actions: CRAG (Yan et al., 2024); adaptive retrieval:
  Adaptive-RAG (Jeong et al., 2024);
- abstention for text-to-SQL: TrustSQL; RTS (Chen et al., 2025);
- example selection: DAIL-SQL;
- FinanceBench (Islam et al., 2023), and page-level retrieval on its open sample (arXiv 2602.17981);
- decision models: Laya (Convai Innovations) and Jev (TypeSafe), with no published evaluation on SQL
  answers that we found.

## 11. Schedule and budget (estimates)

| step | when | time | cost |
|---|---|---|---|
| this protocol committed and pushed | 5 Oct | – | – |
| gpt-oss rerun (Paper A's session) | 5 Oct | hours | Paper A's budget |
| build and label the pool | 5 Oct | 1–2 h | $0 |
| approved-examples arms E0, E1 and E2 (3,537 calls) | 5 Oct | about 1–2 h | about $1–2 |
| ModernBERT: real and mutation, 5 rounds each | 5 Oct | about 4–5 h on the Mac | $0 |
| Laya fine-tuned, 5 rounds (overnight); Laya zero-shot | night of 5–6 Oct | about 6–7 h on the Mac | $0 |
| judge, one call per cluster (about 1,600–2,500) | 6 Oct | 1–2 h | about $1–2 (Zed) |
| Jev, once credits are added | 6 Oct | about 1 h | about $0.15 |
| Part D: two runs of 150 answers, then 300 gradings | 6–7 Oct | about 3 h | about $1–2 |
| analysis | 8 Oct | 1 day | – |
| writing (IEEE) and the owner's review | 9–14 Oct | – | – |
| submission | 15 Oct | – | – |

Part D and the judge use the orchestrator container, which Paper A's session also uses: they run after
its rerun has finished. ModernBERT and Laya each need the Mac's GPU to themselves, so they run one after
the other.

## 12. Version 1 and the change log

**Version 1** (2026-10-05, `4ed5805`): candidate selection, verifier-triggered fallback and a conformal
"verified" label, on BIRD only.
- Pool: 21 candidates per question, including gpt-oss's existing temperature-0 samples.
- Verifiers: ModernBERT and Qwen3-0.6B (LoRA), fine-tuned Laya, hosted Laya, Jev.
- Scoring: 5-fold out-of-fold; leave one database out for ModernBERT.
- Tests: H1–H3 with Holm.

**Changes.**
1. **2026-10-05, during candidate generation, before any candidate was scored** (`724f9d7`). The
   generation budget was raised from 3,000 to 8,000 tokens for all three standalone models.
   deepseek-v4.1-flash reasons before answering, and on 11 of its first 45 calls it used the whole budget
   and returned no text. Calls that hit the old cap with no SQL were regenerated with the new budget.
   Calls whose model answered without SQL keep that answer (it counts as a candidate that fails). No
   result informed this.
2. **2026-10-05, before any verifier was trained or any candidate scored** (`88ca99c`). H1's verifier was
   named in advance instead of "chosen by training-fold accuracy": **ModernBERT, real-candidate regime,
   5-fold.** The folds save scores only for each model's held-out questions, so a training-fold choice
   would have needed extra in-sample scoring that the design did not include.
   - Qwen3-0.6B, fine-tuned Laya and the hosted models became secondary comparisons.
   - In H3's cascade, the fallback selects with the same verifier's highest score.
   - Its threshold was chosen per fold on the other folds' out-of-fold scores (replaced in change 3).

   No result informed this.
3. **2026-10-05, version 2.** Made before any verifier, judge or decision model scored the pool, and
   before any standalone candidate was executed. The owner re-scoped Paper B after an independent audit,
   and decided its scope.
   - **An applied system paper with a document part (Part D)**: the owner's choice. No novelty claims,
     because each component has prior work (§10).
   - **gpt-oss candidates come from the default-sampling rerun.** Version 1's six were near-copies from
     temperature-0 decoding (all five samples identical on 180 of 389 questions), and would have cast six
     votes for one answer.
   - **Train, calibrate and test folds rotate.** Version 1 set its thresholds on out-of-fold scores from
     models that had trained on the test fold.
   - **The verified label uses fixed-sequence testing.** Version 1's search did not stop at its first
     failure, so its 5% bound did not hold.
   - **The judge gives a 0–100 score, and ties are scored as exact expected accuracy.** Version 1's
     judge-max would have been decided by ties.
   - **New policies P1, P2 and P7, and the approved-examples comparison (QS3)**, with three samples per
     arm because single answers flip between runs (Paper A: 10%). These are the cheaper fallbacks a
     product would try first, and a direct alternative use of the same labelled questions.
   - **Laya (owner's decision):** zero-shot from the pinned checkpoint, run locally instead of hosted;
     fine-tuned at setup with its own recipe in every round, for one epoch, overnight. A Laya trained on
     another company's database is deferred (§8).
   - **Jev:** in, once the owner adds credits.
   - **Cost includes generating the candidates.**
   - **Dropped, for time:** Qwen3-0.6B, Laya with cross-entropy only, leave-one-database-out.
   - H1–H3 are kept as pre-specified comparisons.
4. **2026-10-05, after version 2 was pushed (`d496c32`), before any approved-examples call was made.**
   An arm **E2** is added: the 3 most similar approved examples with no minimum similarity. Building
   E1's examples showed that the product's threshold (0.3) gives at least one example to only 54 of the
   393 questions (43 one, 9 two, 2 three), with about 16 usable reference questions per database in a
   round's training folds. The other 339 questions would have the same prompt in E0 and E1, so E1 − E0
   would mostly measure the threshold, not the use of examples. E1 is kept as the product's behaviour. No
   result informed this; the selection statistics came from the question texts alone.
