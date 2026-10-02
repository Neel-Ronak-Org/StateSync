# Spec: external evaluation, Part 3. Database against documents (SEC × FinanceBench)

Owner: StateSync lead, who audits the result by execution. Implementer: a coding agent or the lead.
Branch: `v3-phase3-workspaces`; do not create or switch branches. Written 2026-10-02, **before any
result**; nothing below may change after a model is called, except under §8 (deviations).

Read first:
- `docs/eval/external_datasets.md`: FinanceBench, the SEC snapshot and the SEC database.
- `docs/specs/external_eval.md` (Part 1): the FinanceBench ingest and its rules.
- `docs/research/paper_facts.md` and `docs/research/` protocol: the paper's question
  ("data-level checks against model confidence"). This study is one data-level check.

## 0. Question and pre-registered hypotheses

The same number often exists twice: as a figure in a filing and as a value in a database.
StateSync can answer from either. **When it answers the same question both ways, does
agreement between the two answers tell us the answer is right, and does disagreement catch
wrong answers?**

Fixed now:
- **H1 (primary).** Among questions where both arms give a number, the SQL arm is correct
  more often when the two arms agree than when they disagree.
- **H2.** As a warning ("the database and the filing disagree"), disagreement has a recall of
  at least 0.5 for wrong SQL answers, at a precision of at least 0.5.
- **H3 (descriptive, no threshold).** Accuracy of each arm alone, and of "answer only when
  they agree" (selective accuracy and coverage).

n = 50, so every rate is reported with a Wilson 95% interval and the study is called
exploratory. It is not powered to detect small differences, and the report must say so.

## 1. Data (frozen)

- **Questions:** the 50 FinanceBench questions with `question_type == "metrics-generated"` in
  `data/external/financebench/data/financebench_open_source.jsonl`, text **verbatim**, in file
  order. Record the file's SHA-256; it must match `fixtures/external_eval_checksums.txt`.
  Coverage checked 2026-10-02: all 50 come from 10-Ks, and for all 50 the SEC snapshot holds
  10-K facts from the filing's fiscal year (a filing-level check; §3 checks line items).
- **Gold:** FinanceBench's `answer`. Never edited. A gold that looks wrong is reported, and
  scored as given.
- **Database:** `demo/sec_db.sh` built from the 2026-10-02 snapshot
  (`fixtures/sec_companyfacts_2026-10-02.sha256` must PASS first). Schema `sec`: `companies`,
  `facts`, `metric_tags`, views `annual_metrics` (as first reported) and
  `annual_metrics_restated`.
- **Documents:** the 84 FinanceBench filings as ingested by Part 1 with the shipped chunker
  (`paragraph`); reuse that folder. If a question's filing was refused at ingest, the
  document arm for it is "no answer", not dropped.

## 2. Arms (the shipped pipeline, flags as shipped)

Both arms go through `POST /query` with the dev principal, one new session per question, no
follow-ups. The shipped flags apply (`scripts/shipped_flags.py` must PASS before and after),
and the model is the shipped one (`config/flags.toml`), recorded with its id.

- **SQL arm.** A workspace named `eval-sec-sql` on the connected SEC database
  (`host.docker.internal:5435`, database `sec`, user `statesync_reader`), created through the
  product's own Databases flow. The workspace has exactly this business context, and no
  descriptions, definitions or examples beyond what the product drafts and the implementer
  approves **unchanged**:

  > Annual figures reported by US public companies to the SEC (XBRL company facts). Use the
  > view sec.annual_metrics, which holds each figure as first reported in the company's
  > 10-K; sec.facts holds every reported value including later restatements. Amounts are in
  > the unit given in the unit column (usually USD). A fiscal year is identified by
  > period_end; some companies' fiscal years end early in the next calendar year.

- **Document arm.** A workspace named `eval-sec-docs` on dataset `public` (no tables are
  relevant) whose documents are the Part 1 FinanceBench folder; no business context.
- Questions are asked to both arms **verbatim**, including FinanceBench's instructions
  ("answer in USD millions", "round to one decimal place").

## 3. Phase 0, no model calls: line-item coverage

For each question, before any arm runs, the implementer records in
`docs/eval/sec_crosscheck_coverage.json`: the line items the question needs (e.g. capital
expenditure, revenue, PP&E for two years), the us-gaap tag holding each in `sec.facts`, and
whether `sec.annual_metrics` has it **as first reported** for the needed fiscal years. A
question whose line items are missing stays in every denominator and is tagged
`not_in_database`; its expected SQL outcome is "no answer". This file is committed before
§4 starts, and is never edited after.

## 4. Scoring (fixed now)

- **Number extraction:** the first number in the answer text after unit words are applied
  ("1.6 billion" → 1600 when the question asks for millions; "%" kept as percent). Record
  the extracted value and the raw answer.
- **Correct:** relative error ≤ 1%, or equal to the gold after rounding to the gold's stated
  decimals. A refusal, an error or no number is "no answer" and counts as not correct.
- **Agree:** both arms gave a number and they match each other under the same rule.
- **Blind adjudication:** where extraction is ambiguous (a range, two numbers, a units
  mix-up), one reader decides with the arm labels hidden, and every such case is listed.
- Per question, record: both raw answers, extracted values, correct/agree flags, the SQL
  executed, the evidence tier, the figure-grounding result, the cited pages, latency, tokens.

## 5. Analyses

1. H1: P(SQL correct | agree) vs P(SQL correct | disagree), with intervals and a two-sided
   Fisher exact test (reported, not used to claim significance at n = 50).
2. H2: precision and recall of "disagree" as a warning for a wrong SQL answer, and the same
   for a wrong document answer.
3. H3: accuracy of each arm; coverage and accuracy of "answer only on agreement".
4. Secondary, descriptive: how disagreement overlaps the existing signals (evidence tier,
   figure-grounding flags). Does it catch anything they miss?
5. Failure taxonomy for every wrong or missing answer (wrong line item, wrong fiscal year,
   restated value, units, arithmetic, retrieval miss, refusal).

## 6. Budget and order

About 100 questions × ~5 model calls × ~5k tokens ≈ 500k tokens: more than two days of
Groq's free tier for gpt-oss-120b (200k/day); well under $1 at Groq's paid rates. Run when
the paid key exists, or spread the arms over days. The SQL arm first, then the document arm.
Record the date and model id of every answer.

## 7. Rules

- **No tuning.** No code, prompt, config, workspace text or question changes during the run.
- `python3 scripts/verify_deployed.py` and `scripts/shipped_flags.py` PASS before the run.
- Never alter the gold, the scorer or the coverage file to change a number.
- Stop and report if: the SEC database or the Part 1 folder fails its checks; more than 5
  answers in a row error out (quota or outage); any write reaches the SEC database (the
  reader role must refuse it).
- Clean up the two workspaces afterwards only if the owner agrees; keep all records.

## 8. Deviations

Any change after a model call is listed in the report with its reason, before the results.

## 9. Deliverables

- `scripts/eval_sec_crosscheck.py` (subcommands `coverage-template`, `ask --arm sql|docs`,
  `score`; stdlib only on the host, the dev token via `api_auth.api_token()`, never printed).
- `docs/eval/sec_crosscheck_coverage.json`, `docs/eval/sec_crosscheck_answers_<arm>.json`,
  `docs/eval/sec_crosscheck_scores.json`, and `docs/eval/sec_crosscheck_report.md`
  (hypotheses, deviations, results with intervals, taxonomy, limitations).
