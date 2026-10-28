# Evidence-tiers study, protocol v2: results report (DRAFT, methods only)

Status: **draft, 2026-10-04.** The methods were written before any held-out result was looked at.
All runs are complete: held-out, fan-out set, run-to-run repeat, and the robustness run (the fan-out
set with gpt-oss-120b, protocol v2 §2 H1 and §4).

Protocol: `docs/research/evidence_tiers_study.md` (v2, with changes 1–15 in its §10). Earlier
development run: `docs/eval/bird_pilot_report.md`.

## What was run

| run | questions | label | configuration |
|---|---|---|---|
| held-out | 393 BIRD Mini-Dev questions (`bird_heldout_sample.json`), the corrected file's wording | `heldout_gpt5mini` | measurement flags below, `fanout_retry_max = 1` |
| repeat | 50 of the held-out questions, asked a second time (`bird_heldout_repeat_ids.json`) | `heldout_gpt5mini_repeat` | as held-out, but `self_consistency_k = 0` (run-to-run variation only) |
| fan-out set | 93 questions, 60 risky + 33 control (`bird_fanout_set.json`, frozen after owner review) | `fanout_gpt5mini` | as held-out, `fanout_retry_max = 0` (the check warns only; change 15) |

**Measurement flags:**
- provider `openai_compat`: SQL and answer writing by GPT-5 mini, Zed-hosted, through a local
  OmniRoute;
  - fallback off;
  - the served model recorded per call;
  - default sampling, since the model accepts no temperature;
- light roles (the normaliser) by Groq `qwen/qwen3.8-27b`;
- `answer_checks = on`, `number_grounding = on`, `repair_loop = evidence`;
- `sql_row_cap = executor` (fix F1), `grounding_version = v2` (fix F3), `self_consistency_k = 5`;
- workspaces with no drafted knowledge.

**Judges** see the question as asked, the hint, the tables, the SQL, its first rows and the answer
as written before any repair:
- Gemini 3.8 Flash (Zed): the primary comparator;
- gpt-oss-120b (Groq free tier).

**Labels:**
- SQL label: the corrected gold run on BIRD's SQLite databases against the prediction run on
  PostgreSQL. Relaxed rule (primary) and strict, numbers to 5 significant digits.
- Answer label for answers with small gold results: the automatic rule, validated against a blind
  manual reading of 60 random answers.
- Error types: blind annotation of every wrong answer (Claude), plus a second blind annotation of all 90
  by GPT-6 (Codex), run by the owner. This deviates from the planned owner annotation of a random 20%.
  The owner later reviewed the labels of the 18 sampled answers, not blind (they had seen GPT-6's
  labels), and agreed with them. That is a review, not an independent annotation, so no human kappa is
  reported.

## Operational record (what happened during the runs)

- **Held-out run chunks.** The run went in chunks of up to 2 hours, because of background task
  limits. It is resumable: each answer is saved as it arrives; an answer in flight when a chunk
  stopped was asked again in the next chunk.
- **The Groq daily token cap (found 2026-10-03, about 18:50 UTC).** `qwen/qwen3.8-27b` on Groq's
  free tier allows 200,000 tokens per rolling day. The normaliser uses about 700 tokens per
  question, so after about 286 questions every call waited on retries.
  - Throughput fell to about 6–12 questions an hour.
  - The configuration was **not** changed mid-run. Skipping the normaliser (`language_gate =
    script`) would also drop the proper-noun hints it passes to the SQL writer, a real change to
    the system under test. The run continued at the slower pace instead.
  - Latency figures for the questions asked during that period include the waits, so latency is
    reported separately for them.
- **Served models.** Every GPT-5 mini call reported `gpt-5-mini`. Gemini judge calls on the
  held-out run reported `gemini-3.8-flash` (a versioned name, unlike the pilot's bare "gemini").

## Results: held-out (393 questions)

Files:
- `bird_scores_heldout_gpt5mini_v2_corrected.json` (labels)
- `bird_analysis_v2_heldout_gpt5mini_v2_corrected.json` (analysis)
- `bird_worksheet_*_heldout_gpt5mini.json` (blind annotations)

The fan-out set (H1) is reported in its own section below. Holm is applied over all confirmatory tests
(the tests table).

### Accuracy

| label | accuracy |
|---|---|
| SQL, relaxed, corrected gold (primary) | **77.1%** (95% CI 72.5–81.2) |
| SQL, strict, corrected gold | 70.2% |
| SQL, relaxed, original gold (sensitivity) | 66.4% |
| answer text, automatic rule (298 answers with small gold results) | 81.2% |
| SQL, relaxed, with "output format only" counted correct (exploratory) | 82.2% |

- **The labels matter.** The corrected gold moves accuracy by 10.7 points. That's partly because 81
  questions were reworded to match it; the original gold answers the original wording.
- **Pilot comparison.** The pilot's 56% (original gold, dev questions, before fixes F1 and F3) is not
  comparable with these numbers.

### Which warnings point at wrong answers (90 wrong; base rate 22.9%)

Signals are measured on the answer before any repair.

| signal | flagged | wrong among flagged (precision) | recall | false alarms on right answers |
|---|---|---|---|---|
| fan-out | 1 | 1 (1.00) | 0.01 | 0 |
| truncation | 9 | 2 (0.22) | 0.02 | 7 |
| NULL-heavy | 2 | 2 (1.00) | 0.02 | 0 |
| figure grounding v2 | 5 | 4 (0.80) | 0.04 | 1 |
| **any data check** | **17** | **9 (0.53)** | **0.10** | 8 |
| SQL failed | 4 | 4 (1.00) | 0.04 | 0 |
| empty result | 6 | 6 (1.00) | 0.07 | 0 |
| needed a retry | 24 | 19 (0.79) | 0.21 | 5 |
| refusal | 7 | 4 (0.57) | 0.04 | 3 |

**Same flag rate against the Gemini judge (primary).** When a signal flags k answers, how many are
wrong, against the judge's k most suspect (ties at random); paired, stratified bootstrap.

| signal | k | signal catches | judge catches (expected) | difference (95% CI) |
|---|---|---|---|---|
| any data check | 17 | 9 | 10.8 | −1.8 (−6.3 to 2.2) |
| figure grounding v2 | 5 | 4 | 3.2 | +0.8 (−1.2 to 2.8) |
| empty result | 6 | 6 | 3.8 | +2.2 (0.7 to 4.1) |
| needed a retry | 24 | 19 | 15.3 | +3.7 (−1.7 to 8.6) |

**Risk–coverage (all 393 answers ranked):**

| ranking | AUROC | AURC (lower is better) | accuracy on the 80% kept |
|---|---|---|---|
| self-consistency (5 samples) | 0.739 | 0.135 | 85.8% |
| Gemini 3.8 Flash judge | 0.697 | 0.153 | 85.4% |
| data checks first, then judge | 0.697 | 0.152 | 85.6% |
| judge ensemble (mean of the two judges) | 0.701 | 0.147 | 83.3% |
| gpt-oss-120b judge | 0.606 | 0.186 | 80.8% |

**Self-consistency vs the judge is not a significant difference** (corrected 2026-10-05, protocol change
18):
- **The gap:** +0.042 AUROC (95% CI −0.028 to 0.107; paired, stratified bootstrap).
- **Format-only errors:** counting the 20 format-only errors as correct, self-consistency gets 0.761
  and the judge 0.757.
- **Why:** resampled queries vary in format, so self-consistency flags answers the scorer wrongly
  marks wrong. Mean risk on format-only errors is 0.22 for self-consistency and 0.05 for the judge.
- **Ties:** the judge gives p = 1 to 328 of 393 answers, so its ranking is dominated by ties.
- **Failed and empty queries are wrong by construction** on this benchmark: no corrected gold result is
  empty (§3 of the protocol). Their precision of 1.00 says nothing about deployment.

**The secondary judge, gpt-oss-120b,** was re-run on all 393 answers through Cheaper Inference
(protocol change 16), after Groq's free tier stopped it at 260.
- **Agreement with the 260 Groq-direct verdicts:**
  - 241 (93%) are on the same side of 0.5;
  - 230 (88%) are within 0.1;
  - Spearman 0.77;
  - AUROC on the overlap 0.62 (Groq) vs 0.63 (Cheaper Inference).
- **The route check** (`bird_cheaperinference_route_check.json`): every call reported gpt-oss-120b, and
  the reasoning-effort setting is honoured.
- **It is the weakest ranking here.** The ensemble barely improves on Gemini alone and stays behind
  self-consistency.

### Pre-registered tests

| test | result | outcome |
|---|---|---|
| **H1** fan-out check beats judge at the same k (fan-out set) | GPT-5 mini: 0 double-counting errors on 60 risky questions. Rerun with gpt-oss-120b (as the protocol requires): 5 | **untestable** for both models (precondition: 10) |
| **H2** checks then judge beats judge (AURC) | gain 0.001 (95% CI −0.005 to 0.009), p = 0.42 | **not supported** |
| **H3** grounding false alarms < 5% of correct texts | 2 of 242 (0.8%; upper 95% bound 2.6%), p = 0.0004 | **supported**, but grounding fired on only 5 answers (recall 4%) |
| **H4** repair fixes > breaks, +2 points | 2 repairs triggered | **untestable** (precondition: 10) |

**Holm over the confirmatory tests** (`bird_analysis_v2_fanout_gpt5mini_v2_fanout.json`, `holm_confirmatory`):
- H1 and H4 are untestable by their pre-registered preconditions and carry no p-value.
- That leaves H2 (p = 0.42) and H3 (p = 0.0004). H3 is rejected at the Holm threshold (0.025); H2 is
  not.

### What the wrong answers are (RQ2)

Blind annotation of all 90 wrong answers by Claude. A second, independent blind annotation of all 90
was made by GPT-6 (Codex), run by the owner, on a blank, reshuffled copy:
- **Agreement:** 70 of 90 (Cohen's kappa 0.74). On the split that matters here, system error vs
  benchmark or scorer artefact, 85 of 90 (kappa 0.87).
- **Formatting only:** of the 20 Claude marks formatting-only, GPT-6 agrees on 18. On the other two it
  says column meaning (judging the text) and gold error.
- **Deviation from protocol v2 §5:** the second annotator was another model, on all 90 items, not the
  owner on a 20% sample.

| error type | n | caught by any data check | empty result | needed a retry | Gemini, same k |
|---|---|---|---|---|---|
| filter value (wrong literal, missing or invented filter) | 22 | 3 | 3 | 5 | 6 |
| output format only (scorer false negative) | 20 | 1 | 0 | 3 | 1 |
| column meaning | 15 | 1 | 2 | 5 | 2 |
| aggregation | 8 | 1 | 0 | 1 | 0 |
| hint contradicts data or gold | 8 | 0 | 0 | 0 | 0 |
| ordering/top-k (ties, NULLs sorted first) | 6 | 0 | 0 | 0 | 1 |
| other (4 failed SQL) | 6 | 3 | 1 | 5 | 1 |
| join/fan-out (double counting) | 4 | 0 | 0 | 0 | 1 |
| residual gold error | 1 | 0 | 0 | 0 | 0 |

**The fan-out check caught none of the 4 double-counting errors:**
- Three counted the wrong entity through a legitimate join:
  - lab rows instead of patients;
  - result rows instead of drivers;
  - rulings across printings.

  A structural probe cannot tell these from a correct count of the child rows. That is a limit of the
  idea.
- One (q197) is a join that repeats rows, but the aggregate sits inside a subquery the probe's parser
  does not reach. That is a coverage gap that could be fixed.

The check's only flag fell on an answer whose fault was output format.

**Figure grounding:** 3 of its 4 "catches" are answers whose SQL failed (the text quotes error codes).
It caught one real figure problem.

## Exploratory extensions (protocol change 17; decided after all confirmatory results)

### Second-model replication: gpt-oss-120b at provider default sampling (protocol change 18)

Files:
- `bird_answers_heldout_gptoss.json`
- `bird_scores_heldout_gptoss_v2_corrected.json`
- `bird_analysis_v2_heldout_gptoss_v2_corrected.json`
- `bird_judge_heldout_gptoss_local_router_zed-hosted_gemini-3.8-flash.json` (393 of 393 verdicts, all
  served by gemini-3.8-flash; judged 2026-10-05 18:25–19:14 IST)
- `bird_combination_heldout_gptoss_v2_corrected.json`

**Setup:**
- the orchestrator code the study measured (`c49d5e7`);
- held-out settings: `self_consistency_k = 5`, `fanout_retry_max = 1`;
- gpt-oss-120b at the provider's default sampling (no temperature sent), as for GPT-5 mini;
- gpt-oss-120b and the normaliser through Cheaper Inference; every call reported the requested model;
- Gemini judge on Zed.

Answers were generated 11:38–13:09 IST, before the network outage of about 14:20 that the Paper B
session reported; no answer or verdict records a connection error.

| | GPT-5 mini | gpt-oss-120b | gpt-oss-120b, temperature 0 (superseded) |
|---|---|---|---|
| accuracy (relaxed, corrected gold) | 77.1% | 75.8% | 77.9% |
| samples identical to the answer SQL | 14% | 26% | 78% |
| any data check: flagged / precision | 17 / 0.53 | 22 / 0.41 | 20 / 0.40 |
| data checks vs judge at the same k | −1.8 (−6.3, 2.2) | −3.3 (−9.0, 1.7) | −5.6 (−11.6, −0.5) |
| empty result vs judge at the same k | +2.2 (0.7, 4.1) | +3.5 (1.4, 6.1) | +3.2 (1.4, 5.6) |
| needed a retry vs judge at the same k | +3.7 (−1.7, 8.6) | +3.4 (−1.5, 8.3) | +1.0 (−4.5, 5.7) |
| needed a retry: flagged / precision | 24 / 0.79 | 26 / 0.69 | |
| checks then judge, AURC gain (H2's ordering) | 0.001 (−0.005, 0.009) | 0.002 (−0.007, 0.014) | 0.001 (−0.008, 0.011) |
| grounding false alarms on correct texts | 2/242 | 3/207 | 3/211 |
| repairs triggered | 2 | 7 | 6 |
| Gemini judge AUROC | 0.697 | 0.683 | 0.705 |
| self-consistency AUROC | 0.739 | 0.715 | 0.605 |
| self-consistency minus judge, AUROC | +0.04 (−0.03, 0.11) | +0.03 (−0.05, 0.11) | |
| wrong answers whose 5 samples all agreed | 36/90 | 38/95 | 63/87 |
| combination: all signals / without data checks | 0.785 / 0.801 | 0.774 / 0.792 | 0.672 / 0.691 |
| empty answer texts from the host (correct SQL) | 0 | 39 (35) | 25 (22) |

Intervals are 95% paired stratified bootstrap intervals (seed 20261003). The self-consistency minus
judge intervals come from `scripts/sc_vs_judge_bird.py` (`analyze_bird.stratified_bootstrap` on the AUROC difference); the
GPT-5 mini figure reproduces the audit's. Fallback, voting and headroom figures belong to Paper B and
are not computed here.

**Empty answer texts.** The host returned an empty answer text on 39 answers, 35 of them with correct
SQL. The judge is shown the answer text and rated these lower (mean p 0.51 against 0.89 for the rest).
Without them the judge's AUROC is 0.713, self-consistency's 0.725 and the retry flag's 0.582. The SQL
labels, and so every check and self-consistency figure, are unaffected. (The post-study synthesizer
fix, `cd37011`, asks once more on an empty answer; the measured code did not.)

**What replicates (all exploratory):**
- the data checks fire on about 5% of answers and catch no more errors than the judge at the same flag
  rate;
- ordering by the checks first does not improve the judge's curve, and a combined model is better
  without them (5-fold AURC change −0.020, CI −0.053 to 0.006; leave one database out, AUROC 0.743 vs
  0.773);
- the agent retry is the best cheap signal, and empty results beat the judge at their flag rate (by
  construction);
- self-consistency and the judge rank answers about equally;
- about 40% of wrong answers have five agreeing samples for both models;
- grounding's false-alarm rate stays low (H3), and H4 is untestable again (7 repairs, precondition 10).

### Self-consistency at matched cost (`scripts/sc_cost_bird.py`, `bird_sc_cost_heldout.json`; exploratory)

Self-consistency's risk recomputed from m of the 5 recorded samples (AUROC averaged over all C(5, m)
choices of sample positions; answers whose SQL failed keep risk 1). Differences are from the Gemini
judge, with paired stratified bootstrap intervals.

| samples (extra calls) | GPT-5 mini AUROC | minus judge (95% CI) | gpt-oss AUROC | minus judge (95% CI) |
|---|---|---|---|---|
| 1 | 0.645 | −0.052 (−0.111, 0.004) | 0.614 | **−0.069 (−0.132, −0.009)** |
| 2 | 0.687 | −0.010 (−0.074, 0.052) | 0.659 | −0.024 (−0.095, 0.043) |
| 3 | 0.710 | +0.013 (−0.053, 0.076) | 0.684 | +0.001 (−0.073, 0.074) |
| 4 | 0.726 | +0.029 (−0.040, 0.093) | 0.701 | +0.018 (−0.060, 0.095) |
| 5 | 0.739 | +0.042 (−0.028, 0.107) | 0.715 | +0.032 (−0.048, 0.112) |

- **At one call each, the judge is ahead;** self-consistency needs about three samples to draw level.
- **Tokens:** a self-consistency sample averages about 2,020 in and 380 out; a judge call about 790 in
  and 63 out. (Different models, so token counts are not prices.)
- **Format-only errors counted as correct (gpt-oss):** judge 0.731, self-consistency 0.709.

### What gpt-oss's wrong answers are (`bird_worksheet_errors_heldout_gptoss.json`; exploratory)

Blind annotation of all 95 wrong answers by Claude, from the worksheet only (no signals, verdicts or
self-consistency). One annotator: for 9 questions that GPT-5 mini also got wrong, its earlier labels
were consulted to keep the codebook consistent. A blank, reshuffled copy for an optional second
annotator is `bird_worksheet_errors_heldout_gptoss_second.json`.

| error type | GPT-5 mini (of 90) | gpt-oss (of 95) | gpt-oss: any data check | empty result | retry | Gemini, same k |
|---|---|---|---|---|---|---|
| filter value | 22 | 21 | 2 | 3 | 5 | 4 |
| column meaning | 15 | 21 | 4 | 5 | 7 | 4 |
| output format only | 20 | 19 | 0 | 0 | 1 | 0 |
| hint contradicts data | 8 | 10 | 2 | 0 | 2 | 1 |
| ordering/top-k | 6 | 9 | 0 | 0 | 0 | 1 |
| other | 6 | 7 | 0 | 0 | 2 | 0 |
| aggregation | 8 | 5 | 1 | 0 | 1 | 1 |
| join/fan-out | 4 | 2 | 0 | 0 | 0 | 0 |
| residual gold error | 1 | 1 | 0 | 0 | 0 | 0 |

- **The same mix:** semantic errors (filter value, column meaning, aggregation) are 45 of 90 and 47 of
  95; benchmark or scorer artefacts (format only, hint, gold) 29 of 90 and 30 of 95.
- **gpt-oss-specific:** 4 errors come from PostgreSQL sorting NULLs first in descending order (GPT-5
  mini: 2); 4 compute age as exact age rather than the year difference the hint defines; one is float4
  rounding on the PostgreSQL copy.
- **Double counting:** 2 (a COUNT(*) over a one-to-many join; lab rows counted instead of patients).
  The fan-out check caught neither.

### Superseded: gpt-oss-120b at temperature 0

> **Superseded (protocol change 18).** This run sent `temperature = 0.0` to gpt-oss, while GPT-5 mini
> ran at its provider's default. Its self-consistency samples were near-copies (78% identical to the
> answer SQL), so its self-consistency figures reflect the sampling setting, not the model. Its
> figures are kept in the third column of the table above as the record of that run.

Files: `bird_answers_heldout_gptoss_t0.json` (merged from three parallel shards),
`bird_scores_heldout_gptoss_t0_v2_corrected.json`, `bird_analysis_v2_heldout_gptoss_t0_v2_corrected.json`,
`bird_judge_heldout_gptoss_t0_local_router_zed-hosted_gemini-3.8-flash.json`,
`bird_combination_heldout_gptoss_t0_v2_corrected.json`, `bird_fallback_sim_heldout_gptoss_t0.json`.

**Withdrawn conclusion:** this run's "gpt-oss repeats its errors, so self-consistency and fallbacks
built on it help only for models that are inconsistent when wrong" was an artefact of temperature 0.
At default sampling gpt-oss's wrong answers agree as often as GPT-5 mini's (40%), and its
self-consistency ranks as well. Excluding its 25 empty answer texts changed its AUROCs by at most 0.004.

### Do the signals add up? (`scripts/combine_signals_bird.py`, `bird_combination_heldout_gpt5mini_v2_corrected.json`)

Method: an L2 logistic regression on the per-answer signals, scored out-of-fold. Intervals come from
a paired stratified bootstrap over answers.

| ranking | AUROC (5-fold) | AURC (5-fold) | AUROC (leave one database out) |
|---|---|---|---|
| all signals | 0.785 | 0.122 | 0.769 |
| **all signals except the data checks** | **0.801** | **0.103** | **0.777** |
| judge + self-consistency | 0.790 | 0.114 | 0.776 |
| self-consistency alone | 0.739 | 0.135 | 0.739 |
| judge alone | 0.697 | 0.153 | 0.697 |
| free signals (process + data checks) | 0.578 | 0.208 | 0.519 |

- **The data checks add nothing to a combined model.** Removing them changes AURC by −0.019 (5-fold;
  95% CI −0.050 to 0.009).
- **Judge plus self-consistency carries almost all the value.** Its gain over self-consistency alone
  is within noise.
- **Free signals alone are far behind the judge:** AURC worse by 0.054 (CI 0.010 to 0.097).
- **Standardised coefficients of the full model:**
  - self-consistency 0.86 and judge 0.64;
  - empty result 0.50 and retry 0.30;
  - data checks −0.31 to 0.30, with truncation negative.

> **Reported in Paper B, not Paper A** (owner's decision, 2026-10-05): this fallback result belongs to
> the companion paper's question, so it is not published twice. It is kept here as an internal record.

### Does a fallback triggered by a warning raise accuracy? (`scripts/fallback_sim_bird.py`, `bird_fallback_sim_heldout_gpt5mini.json`)

Method: no model calls. The fallback re-runs the answer and its 5 self-consistency samples and keeps
the majority result (ties keep the original). It is scored against the corrected gold.

| policy | accuracy | answers re-voted | fixed | broken |
|---|---|---|---|---|
| none (as run) | 77.1% | 0 | | |
| always vote | 78.6% | 393 | 12 | 6 |
| vote when the judge doubts (p < 0.5) | 78.6% | 41 | 7 | 1 (sign test p = 0.035) |
| vote on a process signal | 77.1% | 24 | 1 | 1 |
| ceiling: right if any candidate is right | 84.2% | | | |

- **A judge-triggered vote gets the full gain of always voting,** on a tenth of the answers.
- **The candidates hold up to 7.1 more points:** 28 of the 90 wrong answers have a right candidate,
  which a better selector could reach.
- **62 have none.** No fallback over these candidates can fix them.

## Results: fan-out set (93 questions, H1)

Files:
- `bird_scores_fanout_gpt5mini_v2_fanout.json` (labels)
- `bird_analysis_v2_fanout_gpt5mini_v2_fanout.json` (analysis)
- `bird_judge_fanout_gpt5mini_local_router_zed-hosted_gemini-3.8-flash.json` (Gemini, 93 of 93)

The set's content hash matches the frozen one (d91c700b…). Gold is run on PostgreSQL, the dialect the
set was written for. The fan-out check warns but does not retry (change 15).

| kind | n | correct | double-counting errors (wrong, and equal to the naive join's result) | fan-out check fired |
|---|---|---|---|---|
| risky | 60 | 59 (98.3%) | **0** | 0 |
| control | 33 | 29 (87.9%) | 0 | 0 |

**H1 is untestable.** Its precondition (at least 10 double-counting errors on the risky questions) is
not met: GPT-5 mini never wrote the naive join.
- 39 of the 60 risky queries use `COUNT(DISTINCT …)`; 15 use `EXISTS` or `IN (SELECT …)`.
- The one wrong risky answer (F026) is a semi-join of the right shape. The model wrote a malformed
  four-part column name, and the safety gate rejected the query.
- The fan-out check had nothing to catch, and fired on none of the 93.

**The five wrong answers** (unblinded reading; exploratory):
- F011 (control): correct value, with a timestamp for a date. Formatting only; the answer label marks it
  correct.
- F023 (control): an invented filter literal (`k_symbol = 'TRVALY'`), so the result is NULL.
- F026 (risky): the failed query above.
- F090 and F091 (control): a county stored as "Los Angeles" / "Alameda", queried with " County"
  appended. F091 returns NULL; F090 returns 0.

Three of the five are filter-value errors, the commonest held-out type.

**Signals on this set** (5 wrong; too few for comparisons, reported for completeness):
- the NULL-heavy check flagged F023 and F091 (2 of 2 correct);
- grounding, failed SQL and retry each flagged F026.

The data checks flagged 3 answers, all wrong; at the same k the judge's 3 most suspect answers are
also all wrong. Gemini ranks the set at AUROC 0.90, self-consistency at 0.78.
Gemini gives probability 0 to all four genuine errors (F023, F026, F090, F091), including F090, which
no check flags; the one it rates correct, F011, is the formatting-only answer.

**What this adds.** Textbook "count the X that have at least one Y" questions no longer trap a current
model: it uses DISTINCT or a semi-join. The double counting we saw on held-out BIRD (4 of 90 errors)
came through joins that are correct for a different reading of the question, which neither a
structural probe nor this set targets.

## Results: robustness run (fan-out set, gpt-oss-120b)

H1's precondition failed for GPT-5 mini, so protocol v2 §2 reruns the set with the robustness model.

Files:
- `bird_answers_fanout_gptoss.json`
- `bird_scores_fanout_gptoss_v2_fanout.json`
- `bird_analysis_v2_fanout_gptoss_v2_fanout.json`
- `bird_judge_fanout_gptoss_local_router_zed-hosted_gemini-3.8-flash.json`

**Configuration** (protocol change 16):
- gpt-oss-120b for SQL and answers, through Cheaper Inference, with reasoning effort low; every call
  reported gpt-oss-120b;
- the normaliser is qwen3.8-27b through Cheaper Inference, thinking off. Against Groq direct on 20 dev
  questions (`bird_normaliser_route_check.json`; 3 Groq calls were rate-limited out): the same
  language decision on 17 of 17, and the same preserved names on 15 of 17 (Groq kept "Los Angeles"
  twice);
- measurement flags as for the held-out run, with `self_consistency_k = 0` and `fanout_retry_max = 0`
  (warn only);
- Gemini judge on Zed, as before.

| kind | n | correct | double-counting errors | fan-out check fired (all on wrong answers) |
|---|---|---|---|---|
| risky | 60 | 54 (90.0%) | **5** | 4 |
| control | 33 | 30 (90.9%) | 0 | 0 |

**H1 is untestable for this model too:** 5 double-counting errors, against a precondition of 10.

**Exploratory:**
- **The check, when the error happens:** the fan-out check fired 4 times, all on wrong answers. It caught
  4 of the 5 double-counting errors, with no false alarms.
  - The miss (F009) averaged `EXTRACT(YEAR FROM d.dob)`. The probe only read plain-column
    aggregates; this was fixed after the study (commit 8d7c3f5). The measured numbers predate the fix.
- **Against the judge at the same k (4):** the check caught 4, the judge 3.2 (+0.8, 95% CI 0 to 1.8,
  p = 0.12).
  - **Host artefact:** the answer writer returned an empty text on 6 of 93 answers (F013, F025, F037,
    F054, F062, F095). This is a host-side failure: 45–66 output tokens, no visible text, normal
    finish. Their SQL is correct, but the judge rated two of them wrong.
  - **Without those 6,** the judge's top 4 catch 4.0, a tie with the check. The check's edge comes
    from the artefact.
- **Ranking:** Gemini ranks this set at AUROC 0.93 (0.94 without the six).

**What it adds:**
- The shipped model does make the textbook double count, about one risky question in twelve.
- When it does, the structural check catches it precisely, but a strong judge catches it as well.
- Neither model gives H1 enough errors to test.

## Results: run-to-run variation (50 questions asked twice)

Files:
- `bird_scores_heldout_gpt5mini_repeat_v2_corrected.json`
- `bird_analysis_v2_heldout_gpt5mini_repeat_v2_corrected.json` (`run_to_run`)

The second run used the held-out configuration with self-consistency off (`self_consistency_k = 0`;
the samples never change the answer). Five questions hit Groq 503s on the normaliser and were asked
again until answered.

| label | flips | rate (95% CI) |
|---|---|---|
| SQL, relaxed | 5 of 50 | **10%** (3.3–21.8) |
| SQL, strict | 5 of 50 | 10% |
| answer text | 3 of 37 | 8.1% |

- **Direction:** 4 went wrong → right and 1 right → wrong. Accuracy on these 50 was 80% then 86%;
  the net change is not significant (sign test p = 0.38).
- **Signals flip rarely:** retry 2 of 50, every other signal at most 1.
- **Exploratory: self-consistency predicts the flips.** All 5 flipped questions had a first-run
  self-consistency risk of at least 0.4 (mean 0.76), against a mean of 0.09 for the 45 stable ones;
  5 of the 45 stable questions had risk ≥ 0.4.

**What it means:**
- GPT-5 mini at default sampling changes its answer's correctness on about one question in ten.
- A single-run accuracy has a matching per-question noise floor. The 95% interval on held-out
  accuracy (72.5–81.2) covers sampling of questions, not of runs.
- The instability is concentrated where the model's own samples disagree. That is the signal
  self-consistency measures, and is consistent with it giving the best ranking.

### Label quality

- **Answer label vs a blind manual reading of 60 random answers:** 58 of 60 agree (96.7%). The rule
  is kept (bar: 90%). The two disagreements are texts that contain the right value but do not
  answer.
- **SQL label, blind check of 50 random answers:**
  - 0 of 41 labelled correct were judged wrong;
  - 1 of 9 labelled wrong is formatting only.
- **Across all 90 labelled wrong:**
  - 20 are formatting only;
  - 8 involve a hint that contradicts the data or the gold;
  - 1 is a gold error.

### Cost and latency (held-out)

- **GPT-5 mini:** 5.55M input and 1.14M output tokens, about $3.66 at list price. Self-consistency
  alone was $2.47, two thirds of it.
- **Gemini judge:** 0.31M input and 0.03M output tokens.
- **Calls:** 9.0 model calls per question, of which 5 are self-consistency samples.
- **Latency:** median 30 s, p90 292 s. The p90 reflects the Groq waits, not the pipeline.

### What this says (held-out and fan-out set)

- **Answers are unstable on about 10% of questions run to run,** and those are the questions whose
  self-consistency samples disagree.
- **H1 could not be tested.** On 60 questions built to invite double counting, GPT-5 mini never
  double counted and gpt-oss-120b did so 5 times (precondition: 10). When it happened, the fan-out
  check caught 4 of 5 with no false alarms, but a strong judge caught as many.
- **Model-free data checks are not a better warning than an LLM judge.** At the same flag rate they
  catch about as many wrong answers or fewer (−1.8, CI includes 0), and they add nothing to the
  judge's ranking (H2).
- **They rarely apply:** 17 of 393 answers (4%), catching 10% of the wrong ones.
- **The strongest cheap signal is an agent retry** (79% precision, 21% recall). Failed and empty queries
  are always wrong here, but only by construction (no corrected gold result is empty).
- **Self-consistency and the judge rank answers about equally.** The difference is not significant, and
  it vanishes when format-only errors count as correct.
- **Most real errors are semantic:** filter values, column meaning, aggregation intent. A check that
  only sees the SQL and its own result cannot see them; the pilot suggested this, and the held-out
  data confirms it on corrected labels.
- **A fifth of the "wrong" answers are scorer false negatives,** and a further 9 of 90 involve
  benchmark problems (hints or gold). Execution-match accuracy on BIRD understates this system by
  about 5 points.
