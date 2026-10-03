# BIRD pilot report (100 questions, 2026-10-03)

**Status: development run, not evidence for or against the study's hypotheses.** The pilot
deviated from protocol v1 in several ways (listed below), and inspection found harness faults and
label errors large enough to move every number. Protocol v2 (`docs/research/evidence_tiers_study.md`)
makes these 100 questions dev data, excluded from confirmatory results. A first version of this
report (commit `bfa9479`) contained conclusions that the inspection below does not support; they
are corrected here.

## Setup

**Sample:**
- 100 BIRD Mini-Dev questions, stratified by difficulty, seed 20261003 (`bird_pilot_sample.json`);
- PostgreSQL;
- original gold;
- BIRD's hint appended.

**Pipeline:**
- SQL and answers by GPT-5 mini (Zed-hosted, via a local OmniRoute; served model recorded on every
  call);
- light roles by Groq qwen;
- `answer_checks` on, `number_grounding` on, `repair_loop` = evidence;
- workspaces with no drafted knowledge.

**Judges:** Gemini 3.8 Flash (Zed) and gpt-oss-120b (Groq).

**Cost:**
- about $0.55 of Zed credit: GPT-5 mini $0.36, plus about $0.19 for the Gemini judge, priced like
  Gemini 3.5 Flash because Zed lists no price for 3.8;
- gpt-oss runs on Groq's free tier.

## Results as measured (original gold, v1 code)

**Accuracy:**
- 56% relaxed (95% CI 46–66%), 49% strict;
- simple 67%, moderate 54%, challenging 45%;
- 4.2 model calls per question.

| signal (warns "may be wrong") | AUROC (95% CI) | flagged | precision | recall |
|---|---|---|---|---|
| tier, grounding, fan-out or SQL retry (the pilot's composite) | 0.52 (0.45–0.61) | 18 | 0.50 | 0.21 |
| judge: gpt-oss-120b (p ≤ 0.5) | 0.60 (0.50–0.69) | 28 | 0.57 | 0.36 |
| judge: Gemini 3.8 Flash (p ≤ 0.5) | 0.63 (0.54–0.72) | 16 | 0.69 | 0.25 |
| composite OR Gemini judge | 0.63 (0.52–0.72) | 26 | 0.62 | 0.36 |

The base rate is 44% wrong: a warning raised at random is right 44% of the time.

**Individual checks:**

| check | flags | precision |
|---|---|---|
| figure grounding, before repair | 7 | 0.43 |
| figure grounding, after repair | 4 | 0.25 |
| SQL retry | 11 | 0.45 |
| "unchecked" tier | 9 | 0.56 |
| fan-out | 0 | n/a |

**Repair loop:**
- triggered on 7 answers: 3 "fixed", 4 still flagged;
- no change to any label, because it rewrites the text and the label scores only the SQL.

**Even taken at face value:**
- the composite's interval includes 0.5;
- the composite's and the Gemini judge's intervals overlap, so the pilot does not show that the
  judges beat the checks either;
- the Gemini judge's scores are nearly yes/no (76 of 100 at 1.0).

## Inspection after the pilot

All checks below are read-only re-runs of the recorded SQL against the BIRD databases. One
reviewer (Claude), not blind to the outcome.

**1. Of the 44 "wrong" answers, at least 13 are not errors of the system's understanding.** The
other 31 were not adjudicated.

| cause | questions | count |
|---|---|---|
| our prompt's "Apply LIMIT 1000" replaced LIMIT 1, though the first row was right | 46, 766, 915 | 3 |
| right answer cut at 1,000 rows (exact match without the limit) | 532, 1088 | 2 |
| name returned as one column where the gold has first and last; the scorer cannot match | 1002, 1410 | 2 |
| gold counts joined rows where the question asks for drivers or patients; our COUNT(DISTINCT) was right | 963, 1254, 1256 | 3 |
| gold uses a different price from the question and returns NULL | 1526 | 1 |
| hint contradicts the data ('stealth' vs 'Stealth'; anti-SM codes) | 765, 1267 | 2 |

The first seven are faults in our pipeline or scorer, the next four are gold errors, and the last
two are hint problems. On the 13 adjudicated, relaxed accuracy would be at least 67%, not 56%.
Every signal in the table was scored against these labels.

**2. The LIMIT rule is systematic.** 97 of 100 queries end in LIMIT 1000, because the SQL prompt
tells the model to apply it "unless the question explicitly asks for all rows". Four more answers
(37, 82, 1107, 1389) also returned several rows for a "the one" question, but their first row
differs from the gold, so they are counted as wrong.

**3. (Withdrawn after checking the code.)** An earlier draft of this revision said the SQL prompt
described a logistics network. It did not: `datasets.frame_prompt` replaces that framing for
connected databases, and every BIRD question ran in one. Only a generic timestamp rule remains, with
no observed effect.

**4. All 7 figure-grounding flags were pipeline artifacts, not wrong figures:**
- the synthesizer's "first 50 rows" disclosure (50 is `synthesizer_preview_rows`): 327, 518, 766,
  773, 1036;
- parenthesised list numbers "(1) (2) (3)": 28 and 518;
- a correct derived remainder, "remaining 610" = 660 − 50: 145.

The repair loop's 3 "fixes" removed these artifacts; none corrected an error. Re-grounding the
100 answers (text before repair, rows re-executed) reproduces exactly these 7 flags under the
shipped grounding, and gives 0 under grounding v2 (fix F3).

**5. Seven answers say they cannot answer** ("I cannot determine…"): six labelled wrong, one labelled
right (518). The SQL label ignores what the user actually reads.

**6. The double-counting check never fired because GPT-5 mini avoided double counting.** In the
count questions inspected, it used COUNT(DISTINCT); the double counting was in BIRD's gold.

## Departures from protocol v1

1. Original labels, not the corrected annotations v1 makes primary.
2. No manual review.
3. No fan-out question set.
4. Several v1 signals not run:
   - self-consistency
   - verbalised confidence
   - the judge ensemble
   - model-free baselines (empty result, SQL failed)
5. The composite included the tier and SQL retries; v1's "any check" did not.
6. Truncation and NULL-heavy caveats were not recorded by the runner.
7. The repair loop ran while the signals were measured.
8. Model: GPT-5 mini instead of gpt-oss-120b (logged before the pilot).

## Corrections to the first version of this report

- **"Almost all of the 44 errors are plausible results of a slightly different question":**
  withdrawn. It rested on row counts, not review; finding 1 above contradicts it.
- **"The checks catch errors of the kind they were built for":** withdrawn. The pilot contains no
  evidence of that: fan-out never fired, and every grounding flag was an artifact.
- **"The database-against-filings study is now the main test":** withdrawn. Protocol v1 named
  corrected BIRD plus the fan-out set. The SEC study can only be a separate, separately
  pre-registered study.
- **"Judges in line with published results":** at the bottom of the published range, with
  intervals reaching 0.50–0.54.

## What the pilot does show

- **Harness faults:** the LIMIT rule (fix F1 in protocol v2) and grounding artifacts (fix F3).
- **The original labels are too noisy to use;** the corrected annotations are needed.
- **Text and SQL can disagree,** so an answer-level label is needed for grounding and repair.
- **On this sample, the data checks rarely fire on BIRD,** which limits how many wrong answers they
  can catch whatever the labels. Protocol v2 reports this as an expectation, not a confirmed
  prediction.

## Dev re-analysis with the v2 tooling (exploratory)

Files: `bird_scores_pilot_gpt5mini_v2_original.json` and `bird_analysis_v2_pilot_gpt5mini_v2_original.json`.

**Setup:**
- BIRD's own SQLite gold run on SQLite (not yet the corrected labels);
- pilot answers as recorded;
- 98 questions after the cross-engine exclusions.

**Results:**
- **Accuracy:** 56.1% relaxed. The cross-engine scorer reproduces the PostgreSQL labels.
- **At the same flag rate (vs the Gemini judge):** all CIs include 0.
  - the data checks catch 6 wrong answers against the judge's expected 8.5 (CI −5.6 to 0.9);
  - truncation: 3 vs 3.1;
  - refusal: 5 vs 5.4.
- **H2:** ordering by the checks, then the judge, does not lower the judge's AURC (gain 0.005, CI
  −0.022 to 0.035).
- **Answer label:** labels 74 answers.

These are dev numbers, measured with the shipped grounding and the original gold. They check that
the tooling works; they are not results.
