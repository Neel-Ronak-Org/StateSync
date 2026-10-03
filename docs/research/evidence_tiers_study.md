# Study protocol: do data-level checks tell you when to trust a text-to-SQL answer?

Status: **version 2, 2026-10-03, revised after the 100-question pilot.** Version 1 (written
2026-09-30 before any measurement, amended before the pilot) is commit `bfa9479`. §10 lists every
change from v1, why it was made, and whether the pilot's results informed it. The pilot is a
development run: its 100 questions are excluded from every confirmatory result.

Target: a short paper (workshop, regional conference or a short/demo track). One clear finding
with honest limits beats a broad claim. A negative result is reportable.

## 1. Question

A text-to-SQL system answers some questions wrongly and cannot tell which. Recent work predicts
correctness from the SQL text or the model: self-consistency, log-probabilities, LLM judges
(Richardson 2026, arXiv 2607.06799: black-box signals 0.61–0.68 AUROC, a Claude judge 0.78, a
two-provider judge ensemble 0.82).

StateSync instead checks the **answer against the data**, with no model:

| Signal | What it checks | Module | Evaluable on BIRD? |
|---|---|---|---|
| Fan-out probe | a join that repeats rows under SUM/AVG/COUNT | `orchestrator/answer_checks.py` | yes |
| Truncation / NULL-heavy | a result cut at the row cap; an aggregate over a mostly-NULL column | `answer_checks.py` | yes |
| Figure grounding | every number in the text traced to a cell, a total, the question or a passage | `orchestrator/number_grounding.py` | yes |
| Evidence tier | verified / compiled / checked / unchecked | `orchestrator/evidence.py` | no: "verified" and "compiled" need owner-approved examples, which BIRD has none of |
| Metric reconciliation (to be built) | a free-form answer disagreeing with an approved metric | planned | no, same reason |

**RQ1 (primary).** When a data check flags an answer, how often is the answer wrong, compared
with an LLM judge and with self-consistency flagging the same number of answers? Do the checks
add information to the judge?
**RQ2.** What kinds of errors does the system make, and which signal catches which? Which errors
does no signal catch?
**RQ3.** How often do the checks flag answers that are correct?
**RQ4.** When a check flags an answer, does ONE repair attempt that is told what failed turn wrong
answers into right ones, and how often does it break right ones?

Failure types (owner's framing): **grounded** (every figure in the text is in the result),
**correct** (the query answers the question: right table, filter, time window, no double
counting), **intended** (what the user meant: net or gross, fiscal or calendar). Here "correct" is
measured by the SQL label and "grounded" by the answer label (§5). "Intended" is not measurable on
BIRD, where the gold query fixes the intent; it is out of scope.

## 2. Hypotheses (v2)

- **H1 (fan-out set).** At its own flag rate k, the fan-out check catches more wrong answers than
  the judge's k most suspect answers. *Precondition:* the SQL model makes at least 10 fan-out
  errors on the set. If it makes fewer, H1 is untestable for that model; that is reported as "this
  model rarely makes the error the check targets", and the set is rerun with the robustness model
  (§4).
- **H2 (BIRD held-out).** Ordering answers by "any data check fired", then by judge risk, gives a
  lower area under the risk–coverage curve (AURC) than the judge alone.
- **H3 (BIRD held-out).** Figure grounding flags fewer than 5% of answers whose text is correct
  (answer label). It is always reported with grounding's flag count and recall: a check that never
  fires passes H3 trivially, and grounding v2 flagged 0 of 100 dev answers.
- **H4 (BIRD held-out).** Among answers the checks flag, the repair turns more wrong answers right
  than right answers wrong (answer label), and it raises answer-level accuracy over all questions
  by at least 2 points. *Precondition:* at least 10 repairs are triggered. On dev, grounding v2
  flags 0 of 100 answers, so the loop may trigger too rarely on BIRD. If it does, H4 is reported
  as untestable there, not as failed or passed.

Expected from the pilot, so reported but **not** counted as confirmed predictions: on BIRD the data
checks fire on fewer than 20% of answers and catch fewer than 20% of the wrong ones.

## 3. Data

1. **BIRD Mini-Dev** (CC BY-SA 4.0; https://github.com/bird-bench/mini_dev): 498 unique questions
   (ids 137 and 138 appear twice in the release; one copy of each is kept).
   - **Dev:** the 100 pilot questions (`docs/eval/bird_pilot_sample.json`). They are used to find
     and fix harness faults, never in a confirmatory result.
   - **Held-out:** the other 398, minus 5 whose SQLite and PostgreSQL copies disagree (below):
     **393 questions.**
   - **Labels:** the corrected SQL annotations "Arcwise-Plat-SQL" (Jin et al. 2026, arXiv
     2601.08778, https://github.com/uiuc-kang-lab/text_to_sql_benchmarks, CC BY-SA 4.0). They
     correct the SQL only and keep questions and hints as posed. They are written for **SQLite**,
     while StateSync runs on PostgreSQL. So the gold result is computed by running the corrected
     SQL on BIRD's own SQLite databases, the prediction runs on PostgreSQL, and the two results are
     compared (§5). No gold query is translated by hand.
   - **Cross-engine check (run 2026-10-03, before any corrected label was seen):** each copy's
     ORIGINAL gold, run on its own engine, gives the same result on 491 of 498 questions with
     numbers compared to 5 significant digits (475 under v1's 6-decimal comparison; the rest differ only in REAL-vs-double
     rounding). The 7 that differ in substance (532, 533, 563, 565, 683, 788, 1482; 532 and 565 are
     pilot questions) are excluded.
   - The original PostgreSQL gold is kept as a sensitivity analysis.
2. **Fan-out set**, built by us before any model sees it:
   - list every one-to-many relationship in the 11 databases' foreign keys;
   - for each, write questions that aggregate a parent-level quantity where the natural query
     joins the child table (risky), and control questions on the same tables whose join does not
     repeat rows (one-to-one, or aggregated before the join);
   - target 60 risky and 30 control questions; gold SQL written and executed by hand, each gold
     result confirmed by a second, independently written query;
   - written by Claude (disclosed), a random 20% reviewed by the owner, then frozen with a sha256
     before any model run. Reported separately and published.
3. **The StateSync set** (30 questions): a case study only, for the tier and reconciliation
   signals; too small for statistics.

## 4. System, signals and conditions

**System under test:** the StateSync pipeline with the dev fixes below, frozen (commit recorded)
before the held-out run.
- SQL and answer writing: GPT-5 mini (Zed-hosted, through a local OmniRoute; fallback off; served
  model recorded per call). It accepts no temperature, so it runs at the provider's default
  sampling. Light roles: Groq qwen3.8-27b.
- Robustness model (fan-out set only): Groq gpt-oss-120b, the shipped SQL model.

**Dev fixes** (found on the pilot; each behind a flag so the dev questions can be rerun with and
without it):
- **F1. Row cap.** The cap is applied when the query runs, instead of telling the model to "apply
  LIMIT 1000". The instruction replaced the LIMIT 1 of "which is the highest" questions: 97 of 100
  pilot queries ended in LIMIT 1000.
  - Dev check, F1 and F3 on: 12 pilot questions chosen because the faults hit them
    (`bird_answers_dev_f1f3_smoke.json`).
    - "The one" questions now end in LIMIT 1, and none in LIMIT 1000.
    - Grounding flags went from 7 to 0, refusals from 6 to 2, and 6 of 12 are right, against 2 in
      the pilot.
    - A functional check only: the subset is selected, and the model's sampling varies between runs.
- **F3. Figure grounding v2** (`[pipeline] grounding_version = "v2"`). It traces how many rows the
  answer writer was shown ("the first 50 rows") and remainders of it, and skips inline enumerators
  "(1) (2)". All 7 pilot grounding flags came from these, none from a wrong figure.
  - Dev check: re-grounding the 100 pilot answers (text before repair, rows re-executed) gives
    exactly the 7 recorded flags under v1, and 0 under v2.
- **No F2.** A domain-neutral SQL prompt was planned, on the belief that the pilot's prompt
  described a logistics network. It did not: `datasets.frame_prompt` already replaces that framing
  for connected databases, and every BIRD question ran in one. Only the timestamp rule is generic
  to all datasets; nothing in the pilot shows it caused an error, so it is left alone.

**Signals**, each giving a yes/no warning; the judge and self-consistency also give a score:

| Family | Signal | Definition |
|---|---|---|
| Data checks | fan-out | the probe found a join repeating rows (a `join_cardinality` retry) |
| | truncation | the result reached the row cap |
| | NULL-heavy | the NULL-heavy caveat was raised |
| | figure grounding | a figure in the text was not traced, before any repair |
| | **any data check** | any of the four above |
| Model-free baselines | SQL failed | no query executed |
| | empty result | zero rows |
| | needed a retry | the SQL agent retried at least once |
| | refusal | the text says it cannot answer (pattern fixed in `scripts/eval_bird.py` before the held-out run) |
| Model-based baselines | LLM judge | Gemini 3.8 Flash (primary comparator); gpt-oss-120b; ensemble = mean |
| | self-consistency | 5 extra samples of the SQL call with the same prompt; risk = share whose result differs from the answer's |
| Combined | checks then judge | answers ordered by "any data check", then by judge risk |

**Repair loop (RQ4):**
- The held-out run uses `repair_loop = evidence`. The record keeps the text before and after a
  repair, so arm (a), no loop, and arm (b), evidence loop, come from the same run.
- Every signal is computed on the answer before repair.
- Arm (c), the "check your answer" control, is run on the flagged questions only and is
  exploratory: its query is sampled again, so it differs from (b) by more than the prompt.

## 5. Labels and measures

**SQL label** (primary; RQ1, RQ2, H1, H2):
- execution match with the corrected gold;
- relaxed rule (primary: column order and extra predicted columns ignored) and strict rule
  (BIRD's set equality), both reported;
- numbers compared to 5 significant digits.

**Answer label** (H3, H4): does the text state the gold result?
- Automatic rule for gold results of at most 4 cells: every gold cell appears in the text.
  Numbers count when they agree to 3 significant digits, using `number_grounding`'s figure
  extraction; strings are matched ignoring case. A text whose first sentence says it cannot
  answer is labelled wrong, even if it lists the right value among candidates (pilot q1389;
  pilot-informed). Larger results get no answer label.
- Dev check (original gold, pilot): the answer and SQL labels agree on 67 of 74 labelled answers.
  Two of the 7 disagreements are scorer false negatives that the answer label gets right (a name
  in one column vs two). Others are answers that list many candidates including the gold one;
  that is why the blind manual validation decides whether the automatic rule is kept.
- Validated before use against a blind manual label of 60 random answers. If the two agree on
  fewer than 90%, every answer label is manual.

**Error types** (RQ2):
- Every wrong answer (SQL label) on the held-out set is annotated **blind to the signals**.
- The annotator sees the question, hint, gold and predicted SQL and results, and the answer text.
- Categories:
  - join/fan-out
  - filter value
  - column meaning
  - aggregation
  - time window
  - ordering/top-k
  - output format only (a scorer false negative)
  - residual gold error
  - hint contradicts the data
  - other
- Annotator: Claude (disclosed). The owner annotates a random 20% blind; Cohen's kappa is reported.

**No selective relabelling:**
- Labels are frozen before any signal is joined to them.
- Residual label error is estimated on a random 50 answers, reviewed blind.

**Warning quality:**
- Primary: wrong answers caught at a matched flag rate.
- Also reported:
  - precision, recall and false-alarm rate;
  - AUROC where a score exists;
  - the risk–coverage curve and AURC;
  - selective accuracy at 80% and 90% coverage, with ties broken at random (expected value).
- For a yes/no flag, AUROC = 0.5 + (recall − false-alarm rate)/2. A flag that rarely fires cannot
  score high even when it is always right, which is why AUROC is not the primary measure.

**Other measures:**
- **Cost:** extra model calls and tokens per question, and added latency.
- **Repair:** fixed, broken and unchanged answers (answer label); accuracy before and after;
  extra calls.
- **Run-to-run variation:** 50 random held-out questions asked twice; the label flip rate is
  reported.

## 6. Analysis plan

**Confirmatory tests:** H1–H4, Holm-corrected across the four at α = 0.05.
- **H1:** the paired difference in wrong answers caught at k (fan-out check vs the Gemini judge's
  top k, ties broken at random), by bootstrap.
- **H2:** the paired difference in AURC, by bootstrap.
- **H3:** the one-sided 95% upper bound of the false-alarm rate is below 5%.
- **H4:** fixed − broken > 0 (exact binomial on the changed answers), and the accuracy gain's point
  estimate is at least 2 points.

**Bootstrap:** 2,000 resamples, seed 20261003, stratified by database.

**Descriptive only** (labelled exploratory):
- the per-signal table;
- the per-error-type catch matrix;
- the original-gold and strict-label sensitivity analyses;
- the pilot, reported separately with v1 code and never pooled.

## 7. Power and budget

**Power:**
- Held-out: 393 questions, roughly 120–170 wrong. This detects large differences only (about 0.1
  in AUROC, or a dozen wrong answers caught at matched k). Smaller differences are reported as
  inconclusive, with their intervals.
- Fan-out set: power depends on how often the model double counts. With 60 risky questions and a
  25% error rate there are 15 errors, so only a large difference is detectable.

**Cost** (from the pilot's measured spend, about $0.0018 per GPT-5 mini call):

| Run | Estimate |
|---|---|
| held-out pipeline | ~$1.5 |
| self-consistency (5 SQL calls per question) | ~$3.5 |
| Gemini judge | ~$0.8 |
| fan-out set | ~$1 |
| repeat of 50 questions | ~$0.2 |
| **total** | **~$7 of the $10 Zed credit** |

gpt-oss-120b runs on Groq's free tier. Recompute from the pilot's token counts before starting.

## 8. Threats to validity

- **Design overlap:** the checks were designed with StateSync's data in view; the BIRD held-out set
  is the out-of-sample test.
- **Contamination:** BIRD dev has been public since 2023, so the models may have seen it. That
  inflates accuracy more than it shifts the comparison between signals.
- **Self-built set:** the fan-out set is ours. Its rules are fixed in advance, and it is reported
  separately and published.
- **One model, default sampling:** the main result uses one SQL model at default sampling;
  run-to-run variation is measured.
- **Labels:**
  - corrected gold reduces annotation errors but does not remove them;
  - hints are kept as posed, and some contradict the data (e.g. 'stealth' vs 'Stealth');
  - 7 questions are excluded because the two engines disagree.
- **Provenance:** models are reached through an unofficial local proxy of an editor subscription.
  The server's served-model name is recorded per call, but 94 of 100 pilot judge responses named
  only "gemini", so the judge's version is reported as unverified.
- **Annotator:** an LLM (Claude) annotates, with the owner checking a random 20%.

## 9. Schedule

1. **Freeze:**
   - fixes F1 and F3 behind flags;
   - the harness: record caveats, the cross-engine scorer, the analysis per §6;
   - tests;
   - verify on the dev questions only.
2. **Corrected labels:** download (needs the owner's permission) and validate.
3. **Fan-out set:** write, check, owner review, freeze.
4. **Runs:** held-out, self-consistency and judges; then the fan-out set.
5. **Analysis:**
   - freeze the labels;
   - blind annotation;
   - join the signals;
   - analyse.
6. **Writing.**

## 10. Changes from v1 and why

"Pilot-informed" means the change was prompted by the pilot's results; such changes are tested
only on held-out data.

1. **The pilot becomes dev data, excluded from confirmatory results.** Pilot-informed: the fixes
   below were found on it.
2. **Primary measure: wrong answers caught at a matched flag rate, not AUROC.** Methodological:
   - a rarely-firing yes/no flag has a capped AUROC;
   - precision depends on the base rate.

   Comparing at the same flag rate on the same answers removes both effects. The pilot made the
   problem visible, but it is general.
3. **Labels: corrected gold via cross-engine execution; 5-significant-digit comparison; 7
   exclusions; duplicates removed.** New facts found 2026-10-03:
   - the corrections are SQLite;
   - the PostgreSQL copy stores some columns as REAL;
   - two ids are duplicated.

   v1 already made corrected gold primary; the pilot deviated from that.
4. **No selective relabelling.** v1 said to review "every disagreement between the label and the
   checks". Relabelling only those cases biases agreement towards the signal under test
   (discrepant resolution). It is replaced by blind annotation of all wrong answers and a blind
   random sample for label error. Methodological.
5. **Answer label added.** v1 measured only the SQL, while grounding and the repair loop act on
   the text, so H3 and H4 could not be measured as designed. Pilot-informed:
   - the repair changed text but no label;
   - 7 answers said "I cannot determine" whatever their SQL label.
6. **H1 rewritten.** v1's "higher precision on aggregation and join errors" cannot be computed:
   precision is taken over flagged answers, and the correct ones among them have no error type.
   It is replaced by the matched-k comparison on the fan-out set, with a precondition on how many
   errors the model makes. Methodological, plus pilot-informed: GPT-5 mini wrote COUNT(DISTINCT)
   where BIRD's gold double counts.
7. **H2:** "checks OR judge" at an arbitrary 0.5 threshold is replaced by ordering, with AURC
   (threshold-free). Methodological.
8. **H3 on the answer label, with grounding v2.** Pilot-informed: all 7 flags were pipeline
   artifacts ("first 50 rows", a derived remainder, "(1)" list numbers).
9. **H4 on the answer label; arms (a) and (b) from one run; arm (c) exploratory; arm (d),
   judge-triggered, dropped** for cost.
10. **Signals:**
    - refusal added, pilot-informed, so tested on held-out only;
    - "any data check" no longer includes the tier or SQL retries (the pilot's composite did,
      deviating from v1);
    - truncation and NULL-heavy are now recorded (the pilot's runner dropped caveats);
    - tier and reconciliation become case-study only.
11. **Dropped:** verbalised confidence and the logistic combination (scope and sample size).
12. **System:** GPT-5 mini (already used in the pilot, for speed); gpt-oss-120b as the
    robustness model; dev fixes F1 and F3 (F2 withdrawn: its premise was wrong).
13. **Statistics:** four confirmatory tests with Holm correction; stratified bootstrap with 2,000
    resamples; run-to-run variation measured.

**What the pilot did differently from v1** (recorded in `docs/eval/bird_pilot_report.md`):
- original labels instead of corrected ones;
- no manual review;
- no fan-out set;
- no self-consistency, verbalised confidence, judge ensemble or model-free baselines;
- the composite included the tier and retries;
- caveats were not recorded;
- repair ran while the signals were measured.

## Appendix: v1 change log (history)

- 2026-09-30: protocol written. Known before measuring: a live answer's numbered-list
  markers ("1.", "2.") were read as figures; fixed in `number_grounding.py` (commit 590c59a).
- 2026-10-03 (owner): RQ4, H4, the repair-loop conditions and measures, and the
  grounded / correct / intended failure types added. BIRD Mini-Dev downloaded the same day;
  no BIRD question has been run. The fan-out check now also covers COUNT (commit adf27ba),
  which is the version every BIRD run uses. Providers: measurement runs use
  `flags.llm.openai_compat.fallback = "off"`, and every call records the model the server
  says answered (`llm/adapters/openai_compat_adapter.py`).
- 2026-10-03, after a 3-question smoke run and before the pilot: two correctness labels. "Strict"
  is BIRD's rule (equal sets of result rows); "relaxed" ignores column order and extra predicted
  columns (every gold column must be present with the same rows) and is the primary label; both
  are reported. Reason: a smoke answer had the right schools and numbers but an extra name
  column, and another the right fields in a different order; BIRD's rule marks both wrong.
  Pilot: 100 questions, stratified, seed 20261003 (`docs/eval/bird_pilot_sample.json`); BIRD's
  hint is appended as "Hint: ..."; workspaces carry no drafted knowledge.
- 2026-10-03, before the pilot (owner: speed over the free tier): **pilot models.** SQL and answer
  writing: GPT-5 mini (Zed-hosted, through a local OmniRoute; fallback off, served model recorded
  per call; compression off, verified by a verbatim echo). GPT-5 mini rejects a temperature, so it
  runs at the provider's default sampling: less deterministic than the temperature-0 runs on
  record. Light roles (normaliser, supervisor, rewriter): Groq qwen3.8-27b. Judges, from other
  model families than the SQL model: Gemini 3.8 Flash (Zed) and gpt-oss-120b (Groq). Not the shipped
  configuration (gpt-oss-120b for SQL); pilot results are not comparable with the earlier 25/30.
