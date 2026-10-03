# BIRD pilot report (100 questions, 2026-10-03)

Protocol: `docs/research/evidence_tiers_study.md` (pilot settings in its change log). Sample: 100 BIRD
Mini-Dev questions, stratified by difficulty, seed 20261003 (`bird_pilot_sample.json`), PostgreSQL,
original gold, BIRD's hint appended. Pipeline: SQL and answers by GPT-5 mini (Zed-hosted, via a local
OmniRoute; served model recorded on every call), light roles by Groq qwen; answer_checks on,
number_grounding on, repair_loop = evidence; workspaces with no drafted knowledge. Judges: Gemini 3.8 Flash
(Zed) and gpt-oss-120b (Groq). Cost: about $0.55 of Zed credit (GPT-5 mini $0.36; the Gemini judge ~$0.19
if priced like Gemini 3.5 Flash, as Zed lists no 3.8 price); gpt-oss on Groq's free tier.

## Results

- **Accuracy:** 56% relaxed (primary; 95% CI 46-66%), 49% strict (BIRD's rule). Simple 67%, moderate 54%,
  challenging 45%. 4.2 model calls per question.
- **What the 44 wrong answers look like:** 25 returned the same number of rows as the gold with different
  values, 17 a different number of rows, 2 nothing. Almost all are plausible-looking results of a
  slightly different question (a wrong column, filter or reading of the hint), not failures.

| signal (warns "may be wrong") | AUROC (95% CI) | flagged | precision | recall | accuracy on the 80% kept |
|---|---|---|---|---|---|
| any data check (tier, grounding, fan-out, SQL retry) | 0.52 (0.45-0.61) | 18 | 0.50 | 0.21 | 57.5% |
| judge: gpt-oss-120b | 0.60 (0.50-0.69) | 28 | 0.57 | 0.36 | 58.8% |
| judge: Gemini 3.8 Flash | 0.63 (0.54-0.72) | 16 | 0.69 | 0.25 | 62.5% |
| checks OR gpt-oss judge | 0.58 (0.47-0.68) | 37 | 0.54 | 0.45 | 56.2% |
| checks OR Gemini judge | 0.63 (0.52-0.72) | 26 | 0.62 | 0.36 | 60.0% |

- **Data checks on BIRD are at chance (AUROC 0.52).** They fired rarely: the fan-out check never (BIRD has
  few one-to-many aggregations), figure grounding 4 times (1 on a wrong answer). This is what H1 predicted
  for filter-value and column-meaning errors, which dominate BIRD; it is not support for H1 on BIRD.
- **Judges are weak but better than chance:** 0.60-0.63, in line with published black-box judges
  (0.61-0.78). Combining them with the checks did not help.
- **Repair loop (RQ4):** triggered on 7 answers (figures that did not trace), fixed the text of 3, left 4
  flagged. It changes prose, not SQL, so it did not change any execution-match label: on BIRD its effect
  on correctness is nil. H4 (+2 points) not met.

## Reading, honestly

The checks that look at the data StateSync already has (do the answer's figures match the result, did a
join double count) catch errors of the kind they were built for, and almost none of BIRD's, because
BIRD's errors are semantic: the query runs and returns a sensible result to a slightly different
question. Nothing that only compares an answer with its own result can see that.

What could: a check against an INDEPENDENT source of the same fact. That is the database-against-filings
study (`docs/specs/sec_crosscheck.md`), where a wrong column or year produces a disagreement. It is now
the main test of the paper's idea; the fan-out set remains the test of the checks on the errors they
target. Limitations: 100 questions (wide intervals), one SQL model run without temperature control,
original BIRD labels only (the corrected labels were not downloaded), 94 of 100 Gemini verdicts report
their model only as "gemini".
