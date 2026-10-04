# Do Data Checks Tell You When to Trust a Text-to-SQL Answer? A Pre-registered Study on Corrected BIRD Labels

*Draft v0.1, 2026-10-04. Short-paper format (about 4 pages plus references). The run-to-run repeat is
marked PENDING. Author block, venue and template to follow.*

## Abstract

A text-to-SQL system that answers wrongly should say so. Most work estimates correctness from the
model: self-consistency, log-probabilities, or an LLM judge. A cheaper alternative checks the answer
against the data, with no model. It flags joins that count rows twice, results cut off at a row limit,
aggregates over empty or all-NULL data, and figures in the written answer that do not appear in the
result.

We test whether such model-free data checks are a useful warning in a pre-registered study on 393
held-out BIRD Mini-Dev questions. The study scores them against corrected gold annotations and
compares every signal with an LLM judge at the same flag rate. A production pipeline answers 77.1% of
questions correctly (82.2% once scorer false negatives are discounted).

The data checks fire on only 4% of answers. When they do, they catch no more wrong answers than the
judge does with the same number of warnings (9 vs 10.8; 95% CI of the difference −6.3 to 2.2). Ordering
by the checks before the judge does not improve the judge's risk–coverage curve.

The strongest cheap warnings are ordinary process signals: the query failed, returned nothing, or
needed a retry (79% precision). Self-consistency gives the best ranking (AUROC 0.74 vs 0.70 for the
judge).

A blind annotation of every wrong answer explains why. Most errors are semantic: wrong filter values,
wrong columns, wrong aggregation intent. The double-counting errors that a structural probe targets
mostly arise through legitimate joins whose intent the probe cannot see. A fifth of the
execution-match "errors" are formatting differences the scorer misses. On a frozen set of 60 questions
built to invite double counting, the model never double counted, so the pre-registered fan-out test
could not be run: the textbook trap such probes target no longer catches a current model.

## 1 Introduction

Natural-language interfaces to databases are now good enough to deploy and still wrong often enough to
mislead. A business user who asks "how many patients had an abnormal test" and receives a confident
number cannot tell whether the query counted patients or lab tests. A system that can attach an
honest "this might be wrong" to such answers is more useful than one that is slightly more accurate.

Two families of signals can provide that warning:
- **Model-based signals** ask a language model again: sample several queries and measure agreement
  (self-consistency), read token probabilities, or ask a separate LLM to judge the answer. Recent work
  finds black-box model signals around 0.61–0.68 AUROC and LLM judges at 0.72–0.78 (Richardson, 2026).
- **Data checks** inspect the answer against the database itself, with no model call. They are free,
  instant and explainable ("this join repeats each patient 46 times"), which makes them attractive in a
  product.

We built such checks into StateSync, a self-hosted text-to-SQL and document question-answering
system, and asked whether they earn their place. This paper reports a pre-registered evaluation.

**Contributions:**
1. **A pre-registered comparison** of model-free data checks, model-free process signals, an LLM judge
   and self-consistency as correctness warnings, on 393 held-out BIRD Mini-Dev questions scored
   against corrected annotations (Jin et al., 2026). The primary measure compares signals at a matched
   flag rate, which is fair to rarely-firing yes/no checks.
2. **A blind error-type annotation** of every wrong answer, with a catch matrix showing which signal
   sees which kind of error.
3. **A negative result with its reason.** Data checks rarely apply and do not beat a judge or
   self-consistency, because most real errors are semantic. Structural double-counting checks miss
   double counting that happens through legitimate joins.
4. **A frozen, owner-reviewed fan-out question set** (60 risky and 33 control questions on the BIRD
   databases), each with a verified gold query, an independent check query and the naive
   double-counting query. The model answered 59 of the 60 risky questions correctly and never double
   counted, so the fan-out hypothesis (H1) was untestable by its pre-registered precondition.
5. **A measurement note.** It confirms known benchmark and metric problems (Wretblad et al., 2024;
   Pourreza and Rafiei, 2023; FLEX, 2025; Jin et al., 2026) on corrected labels, and quantifies them:
   - correction moves accuracy by 10.7 points;
   - even then, a fifth of execution-match errors are formatting differences the scorer misses.

## 2 Related work

**Correctness prediction and abstention for text-to-SQL:**
- Richardson (2026) compares black-box, white-box and verification-based signals on BIRD and Spider.
  They find self-consistency and executability near 0.61–0.68 AUROC, LLM judges at 0.72–0.78, and a
  two-provider judge ensemble at 0.82.
- Somov and Tutubalina (2025) study entropy-based selective classifiers for error detection.
- Chen et al. (2025) add abstention and human feedback through conformal prediction on the model's
  hidden states.

All of these signals come from a model. We add model-free data checks and compare them with model
signals at the same flag rate.

**Error detection with database feedback.** SQLens (Gong et al., 2025) is the closest work. It
combines database signals (for example an abnormal result, an empty predicate, a suboptimal join tree
or value ambiguity) with LLM signals in a learned classifier. On BIRD and Spider it reports gains in
error-detection F1 and in accuracy after correction.

We differ in four ways:
- we evaluate the checks as warnings in their own right, at a matched flag rate, against a judge and
  self-consistency;
- we use corrected gold labels;
- we pre-register the hypotheses;
- we annotate every error to explain what the checks can and cannot see.

**Benchmark and metric quality.**
- Wretblad et al. (2024) document noise in BIRD's questions and gold SQL, and show that correcting the
  gold changes which methods look best.
- Jin et al. (2026) report annotation errors in 52.8% of BIRD Mini-Dev examples and release corrected
  annotations, including corrected hints ("external knowledge"). We score against them.
- On the metric side, execution accuracy has both false negatives and false positives:
  - FLEX (2025) re-judges with an LLM and finds that underestimation on BIRD and Spider stems mainly
    from annotation quality;
  - Pourreza and Rafiei (2023) find that most failed predictions of a strong model on Spider are
    judged correct by humans, citing underspecified questions and non-deterministic outputs such as
    ties.

Our measurement note confirms these on corrected labels, and adds three points:
- the share of execution-match errors that are formatting only;
- dialect artefacts when a SQLite-born benchmark is run on PostgreSQL;
- documentation gaps in the released files.

**Self-consistency** (Wang et al., 2023) is our strongest model-based baseline. It is applied here as
agreement between the execution results of resampled queries.

## 3 System and signals

**Pipeline.** StateSync routes each question; for database questions an LLM writes SQL. The SQL is
validated by a read-only gate and executed with a row cap. The model retries with a classified error
message when a query fails or returns nothing. A second LLM call writes the answer text from the first
rows of the result.

In this study the SQL and answer model is GPT-5 mini (default sampling: the model accepts no
temperature). A small model (Qwen3.8-27B) normalises the question.

**Data checks (no model call):**
- **Fan-out probe.** For aggregates over joins, it compares COUNT(key) with COUNT(DISTINCT key) for
  each joined table, to detect joins that repeat the rows being aggregated.
- **Truncation.** The result reached the row cap.
- **NULL-heavy.** The aggregate result is entirely NULL.
- **Figure grounding.** Every number in the answer text must trace to a result cell, an aggregate of a
  column, the question, or (version 2) the number of rows the answer writer was shown.
- **Any data check:** the union of the four.

**Model-free process baselines:**
- the query failed;
- the result was empty;
- the agent needed a retry;
- the answer text refuses (a fixed pattern on its first sentence).

**Model-based baselines:**
- **LLM judge.** Gemini 3.8 Flash, the primary comparator. It is from a different family than the SQL
  model and sees the question, schema, SQL, first rows and answer.
- **Self-consistency.** Five extra samples of the SQL call with the same prompt, each executed. Risk is
  the share whose result differs from the answer's under the relaxed rule below.

## 4 Study design

**Pre-registration and the development pilot.**
- **Protocol.** We wrote the protocol before measuring.
- **Pilot.** We ran a 100-question pilot.
- **Revision.** We revised the protocol (v2) after the pilot exposed harness faults:
  - a prompt instruction that replaced LIMIT 1 with LIMIT 1000;
  - grounding false alarms caused by the pipeline's own wording;
  - noisy original labels.
- **Dev data.** The pilot questions are development data and excluded from all results.
- **Logging.** Every amendment, and whether the pilot informed it, is logged.

**Data:**
- **Held-out set:** the 393 BIRD Mini-Dev questions not in the pilot. Two ids appear twice in the
  release, so duplicates are removed. Seven questions are excluded because their SQLite and PostgreSQL
  copies disagree on the original gold.
- **Labels: corrected annotations** (Jin et al., 2026), in their SQLite dialect:
  - gold queries run on BIRD's SQLite databases, predictions on the PostgreSQL copy;
  - results are compared to 5 significant digits;
  - the comparison was validated on the original golds of both copies (491 of 498 agree).
  - The corrected file also rewords 81 questions; we ask the corrected wording.
- **Fan-out set.**
  - 60 risky questions whose natural SQL aggregates a parent-level quantity across a one-to-many join;
  - 33 control questions on the same tables where the join is harmless.
  - Each question has a gold query, an independently written check query that must agree with it, and
    the naive join. The naive join must differ from gold for risky questions and equal it for
    controls, verified on the data.
  - Drafted with an AI assistant (Claude) from construction rules fixed in advance, and verified on the
    data. The author reviewed a random 20% (19 questions, all accepted) before freezing.
  - On this set the fan-out check runs warn-only (no automatic retry), so detection is measured
    without repair.

**Labels and measures:**
- **SQL label:** execution match, primary rule relaxed (column order and extra predicted columns
  ignored); BIRD's strict rule is also reported.
- **Answer label:** whether the answer text states every cell of a small gold result. It was
  validated against a blind manual reading of 60 random answers (96.7% agreement).
- **Primary measure: wrong answers caught at a matched flag rate.** When a signal flags k answers, we
  compare how many are wrong with the expected number among the judge's k most suspect answers (ties
  broken at random, computed exactly). For a yes/no flag, AUROC equals 0.5 + (recall − false-alarm
  rate)/2, which penalises a rarely-firing flag regardless of its precision.
- **Also reported:** precision, recall, AUROC and the area under the risk–coverage curve (AURC).
- **Statistics:** paired bootstrap stratified by database (2,000 resamples). Four confirmatory tests,
  Holm-corrected:
  - **H1:** on the fan-out set, the fan-out check catches more wrong answers than the judge at its flag
    rate.
  - **H2:** ordering by "any data check", then by judge risk, lowers the judge's AURC.
  - **H3:** grounding flags fewer than 5% of correct answers.
  - **H4:** one evidence-guided repair fixes more answers than it breaks and adds at least 2 points.

  H1 and H4 carry preconditions (at least 10 double-counting errors; at least 10 repairs) and are
  reported as untestable otherwise.
- **Error types:** every wrong answer was annotated blind to the signals into ten categories. A second annotator (GPT-6, run independently on a blank, reshuffled copy) labelled all 90. Agreement is κ = 0.74 over ten
  categories, and κ = 0.87 for system error vs benchmark/scorer artefact.

## 5 Results (held-out, n = 393)

**Accuracy:**
- **Primary:** 77.1% under the relaxed rule (95% CI 72.5–81.2); strict 70.2%.
- **Original gold:** 66.4%.
- **Answer label:** 81.2% (298 answers with small gold results).
- **Format-only discounted:** counting the 20 errors whose only fault is output format as correct
  gives 82.2% (exploratory).

**Warnings.** 90 answers are wrong (22.9%).

| signal | flagged | precision | recall | matched-k vs judge (95% CI) |
|---|---|---|---|---|
| any data check | 17 | 0.53 | 0.10 | −1.8 (−6.3, 2.2) |
| · fan-out | 1 | 1.00 | 0.01 | n/a |
| · truncation | 9 | 0.22 | 0.02 | −3.7 (−7.4, −0.7) |
| · NULL-heavy | 2 | 1.00 | 0.02 | n/a |
| · figure grounding | 5 | 0.80 | 0.04 | +0.8 (−1.2, 2.8) |
| query failed | 4 | 1.00 | 0.04 | n/a |
| empty result | 6 | 1.00 | 0.07 | +2.2 (0.7, 4.1) |
| needed a retry | 24 | 0.79 | 0.21 | +3.7 (−1.7, 8.6) |
| refusal | 7 | 0.57 | 0.04 | −0.5 (−2.7, 1.7) |

**Rankings:**

| ranking | AUROC | AURC |
|---|---|---|
| self-consistency | 0.739 | 0.135 |
| LLM judge | 0.697 | 0.153 |
| checks, then judge | 0.697 | 0.152 |

**Hypotheses:**
- **H2 is not supported:** AURC gain 0.001 (95% CI −0.005 to 0.009).
- **H3 is supported:** 2 of 242 correct answers flagged; upper bound 2.6%. But grounding fired on only
  5 answers, and 3 of its 4 hits are answers whose SQL failed and whose text quotes an error code.
- **H4 is untestable:** 2 repairs were triggered.
- **H1 is untestable:** on the fan-out set the model made no double-counting errors (precondition: 10).
- **Holm** over the testable confirmatory tests (H2, H3): H3 is rejected (p = 0.0004), H2 is not
  (p = 0.42).

**What the errors are.** The 90 wrong answers, by type:

| error type | n | any data check | empty result | needed a retry | judge (same k) |
|---|---|---|---|---|---|
| filter value | 22 | 3 | 3 | 5 | 6 |
| output format only | 20 | 1 | 0 | 3 | 1 |
| column meaning | 15 | 1 | 2 | 5 | 2 |
| aggregation | 8 | 1 | 0 | 1 | 0 |
| hint contradicts data or gold | 8 | 0 | 0 | 0 | 0 |
| ordering/top-k | 6 | 0 | 0 | 0 | 1 |
| other (incl. 4 failed SQL) | 6 | 3 | 1 | 5 | 1 |
| double counting | 4 | 0 | 0 | 0 | 1 |
| gold error | 1 | 0 | 0 | 0 | 0 |

The fan-out probe caught none of the four double-counting errors:
- Three count the wrong entity through a join that is itself legitimate:
  - lab rows instead of patients;
  - result rows instead of drivers;
  - rulings across card printings.
- The fourth repeats rows inside a subquery the probe does not parse.

**Label quality.** A blind check of 50 random answers found none labelled correct that was wrong.

**Run-to-run variation:** [PENDING: label flip rate on 50 repeated questions.]

## 5b Results (fan-out set, n = 93)

| kind | n | correct | double-counting errors | fan-out check fired |
|---|---|---|---|---|
| risky | 60 | 59 (98.3%) | 0 | 0 |
| control | 33 | 29 (87.9%) | 0 | 0 |

The model avoided every trap:
- 39 of the 60 risky queries use `COUNT(DISTINCT …)`, and 15 a semi-join (`EXISTS` or `IN`).
- The one wrong risky answer is a semi-join of the right shape that failed on a malformed column name.

With no double counting to catch, H1's precondition fails, and the check fired on none of the 93
answers.

The five wrong answers (an unblinded, exploratory reading):
- three filter values: an invented code, and a county name queried with " County" appended;
- the failed query;
- one formatting-only difference.

The NULL-heavy check flagged two of them. The judge gave all four genuine errors a probability of 0.

## 6 Discussion

**Why data checks rarely help.** The checks see the query and its own result; most errors are about
meaning:
- a filter value stored differently ('north Bohemia');
- a column that is plausible but wrong (fastest-lap rank vs finishing position);
- an aggregation that answers a neighbouring question.

A result can be internally consistent and still answer the wrong question. Even double counting,
the error a structural probe targets, mostly happens through joins that are correct for some
reading of the question. "Count lab rows" and "count patients" share a join. Only the question
says which was meant.

**What does work cheaply.** Signals from the pipeline's own process are surprisingly strong:
- a query that failed, returned nothing, or needed a retry is wrong 79–100% of the time;
- these cost nothing and catch about a fifth of the errors;
- self-consistency gives the best overall ranking, at the price of five extra model calls (two thirds
  of our generation cost).

In practice we would show process signals and self-consistency, and keep data checks as explanations
attached to a flagged answer rather than as detectors.

**What the benchmark measures.** These problems are known (Section 2); our numbers show they persist
after correction:
- **Correction.** Correcting the gold moves accuracy by 10.7 points.
- **Formatting.** A fifth of the remaining "errors" are formatting differences: a fraction for a
  percentage, a timestamp for a date, a name in one column instead of two.
- **Hints.** Eight more involve a hint that contradicts the data or the gold. The corrections fix SQL
  and some hints, but not all.
- **Dialect.** Running a SQLite-born benchmark on its PostgreSQL copy adds artefacts:
  - 7 of 498 golds disagree across the two engines;
  - PostgreSQL sorts NULLs first in descending order, which turned 2 answers wrong.
- **Documentation.** The released "SQL-only" correction file rewords 81 questions and 68 hints, and two
  Mini-Dev ids are duplicated.

Studies of warning signals on BIRD should report corrected labels, and ideally an answer-level label,
because the user reads the text. An LLM-based re-judge of execution matches, as in FLEX, would also
help.

## 7 Limitations

- **One SQL model** (GPT-5 mini) at default sampling; the model may have seen BIRD during training.
- **Model access** was through a local OpenAI-compatible proxy to a hosted subscription. The server
  reported the served model on every call (all `gpt-5-mini`; judge `gemini-3.8-flash`).
- **Rate limits.** The secondary judge (gpt-oss-120b) is partial (220 of 393) because of free-tier
  limits.
- **Annotation.** Both error-type annotators are AI models, blind to the signals: κ = 0.74; κ = 0.87 for system
  error vs artefact. Human annotation of a sample would strengthen this.
- **The fan-out set** was drafted with an AI assistant from rules fixed in advance and is published. Its
  risky questions follow the textbook pattern ("count the X that have at least one Y"). The null
  result for H1 shows that this pattern no longer traps a current model. It says nothing about the
  subtler double counting seen in held-out answers, through joins that are legitimate for another
  reading of the question.
- **Scope.** English questions, one benchmark, the SQL path only. The system's document path is not
  evaluated.

## Reproducibility and disclosure

Everything is in the project repository:
- the protocol (with its change log);
- the frozen samples and the fan-out set, with sha256 hashes;
- the run records, including the served model per call;
- the scorer and the analysis.

Corrected labels are from Jin et al. (2026, CC BY-SA 4.0); BIRD Mini-Dev is CC BY-SA 4.0. An AI
assistant (Claude) helped write code, annotate errors blind to the signals, and draft this text. The
authors reviewed the outputs and take responsibility for them.

## References

- Chen, K., Chen, Y., Yu, X., Koudas, N. (2025). Reliable Text-to-SQL with Adaptive Abstention.
  arXiv:2501.10858.
- Gong, Y., Lei, C., Qin, X., Vaidya, K., Narayanaswamy, B., Kraska, T. (2025). SQLens: An End-to-End
  Framework for Error Detection and Correction in Text-to-SQL. arXiv:2506.04494.
- Jin, T., Choi, Y., Zhu, Y., Kang, D. (2026). Pervasive Annotation Errors Break Text-to-SQL Benchmarks
  and Leaderboards. PVLDB 19. arXiv:2601.08778.
- Kim, H., Jeon, T., Choi, S., Choi, S., Cho, H. (2025). FLEX: Expert-level False-Less EXecution Metric
  for Text-to-SQL Benchmark. NAACL 2025. arXiv:2409.19014.
- Li, J., et al. (2023). Can LLM Already Serve as a Database Interface? A BIg Bench for Large-Scale
  Database Grounded Text-to-SQLs. NeurIPS 2023 Datasets and Benchmarks.
- Pourreza, M., Rafiei, D. (2023). Evaluating Cross-Domain Text-to-SQL Models and Benchmarks. EMNLP
  2023. arXiv:2310.18538.
- Richardson, R. (2026). What Predicts Correctness in Text-to-SQL? A Selective-Prediction Study.
  arXiv:2607.06799.
- Somov, O., Tutubalina, E. (2025). Confidence Estimation for Error Detection in Text-to-SQL Systems.
  AAAI 2025. arXiv:2501.09527.
- Wretblad, N., Riseby, F., Biswas, R., Ahmadi, A., Holmström, O. (2024). Understanding the Effects of
  Noise in Text-to-SQL: An Examination of the BIRD-Bench Benchmark. ACL 2024 (short papers).
  arXiv:2402.12243.
- Wang, X., Wei, J., Schuurmans, D., Le, Q., Chi, E., Narang, S., Chowdhery, A., Zhou, D. (2023).
  Self-Consistency Improves Chain of Thought Reasoning in Language Models. ICLR 2023.

*Verification:*
- Richardson, Jin et al., Gong et al. (SQLens), Somov and Tutubalina, and Chen et al. were checked
  against arXiv on 2026-10-04.
- Li et al. (BIRD) and Wang et al. (self-consistency) are standard references. Their bibliographic
  details are still to be confirmed against the official proceedings before submission.
