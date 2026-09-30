# Fact sheet for the draft paper (every number here is verified; use no others)

## System (StateSync, self-hosted AI data analyst)
- Questions in natural language over Postgres (+ uploaded documents). Pipeline: normalize →
  (rewrite follow-ups) → router (sql / docs / hybrid) → SQL agent (draft, correction loop up to
  3 retries, SQL gate) → synthesizer (prose, citations built in code) → deterministic checks.
- Security architecture: the orchestrator (LLM side) holds no database credentials; a separate
  MCP service is the only credential holder and runs every query through a 3-layer gate
  (pglast parse: exactly one SELECT, no data-modifying CTEs, function denylist; READ ONLY
  transaction always rolled back; a read-only role). Row-level security on every app table.
- Evidence tiers (orchestrator/evidence.py), a pure function of what ran:
  verified (owner-approved SQL run as is) > compiled (a template derived from approved SQL:
  literals become typed slots via the Postgres parser, values filled in code, no SQL-model
  call) > checked (new SQL, deterministic checks on, no caveat) > unchecked.
- Deterministic answer checks (orchestrator/answer_checks.py): join fan-out probe (for SUM/AVG
  over a join, compare COUNT(key) with COUNT(DISTINCT key) on the joined rows; a mismatch
  means rows were repeated), result truncated at the row limit, NULL-heavy aggregates.
- Figure grounding (orchestrator/number_grounding.py), no model: every number in the prose is
  matched, within the rounding its own digits imply ("1.2M" = 1.15M–1.25M), to a result cell
  (fractions written as percents count), a column sum/avg/min/max or the row count, part of a
  text value, a number in the question, a cited passage, or arithmetic (ratio/difference) of
  two figures already traced. Years, dates, identifiers and list markers are excluded.
  Unmatched figures become a caveat and move the answer out of the "checked" tier; the UI
  marks each figure with its source on hover.
- Self-testing workspace: approved queries re-run on a schedule with no model; the result
  SHAPE signature (column names + whether rows return) is compared to detect schema drift.
- Definitions mined from uploaded documents: passages pre-selected in code by definitional
  wording within a character budget; the model must quote the exact sentence; a proposal is
  kept only if the (normalised) quote occurs in the cited passage and contains the term.
  Live: a 4-definition handbook gave 4/4 definitions, 0 invented, 1 model call, 2.6 s; the
  7-definition demo handbook (2026-09-30) gave 7/7, 0 dropped, 1 model call.
- Connecting an owner's own Postgres: saved only if the login is provably read-only
  (no superuser/createrole/createdb/replication/bypassrls, no write-capable built-in roles,
  no INSERT/UPDATE/DELETE/TRUNCATE on any table); password encrypted with a key only the MCP
  holds; SQL may read only that database's tables. Live check: 24/24.

## Measured baseline (docs/eval/groq_baseline_2026-09-29.json; Groq gpt-oss-120b for SQL)
- 30 questions (olist real e-commerce data 10, synthetic logistics SQL 8, documents 6,
  follow-ups 6): 25/30 correct (83%); olist 10/10, logistics SQL 6/8, docs 5/6, follow-ups 4/6.
- Routing 30/30. Mean 4.7 model calls and ~5,000 tokens (4,652 in / 356 out) per question.
  Latency p50 2.6 s, p90 3.5 s.
- The 5 misses: a percentage where the reference returns a fraction (scored strictly); a
  projection difference; one document retrieval miss; two follow-ups that counted at the
  wrong grain (payment rows instead of orders; reviews duplicated by a join under COUNT(*)
  -- the fan-out probe covers SUM/AVG only so far).

## Pilot: figure grounding over stored answers (2026-09-30; zero model calls)
- Data: every stored conversation turn with executed SQL and an answer (1,268); 944
  analysable (319 had no result columns, 5 had result previews truncated below the row count).
- First run: 2,024 figures, 91.8% traced; 29 answers flagged (3.1%). Manual review of all
  29: 21 were FALSE ALARMS from two checker defects -- numbers inside text values
  ("Seller Store 28", "port=5432") and dates written with Unicode non-breaking hyphens.
- After fixing both (and counting ratios/differences of traced figures as "derived"):
  1,939 figures, 97.5% traced (cell 1,399; question 366; aggregate 65; text 61; unmatched 48);
  8 answers flagged (0.85%). All 8 are genuinely not supported by their own result:
  6 follow-ups repeating figures from an earlier turn (e.g. "96,478 out of 99,441 ... 97.0%"
  where only 96,478 is in the result), 1 hybrid answer quoting document figures (the pilot
  did not pass the passages), 1 answer that described a method instead of answering.
- No figure contradicting its data was found in the 944 answers.
- CAVEAT: in-sample -- the defects were found and fixed on the same data. Out-of-sample
  evaluation is future work (BIRD Mini-Dev).
- Also found live: numbered-list markers ("1.", "2.") read as figures; fixed before the pilot.

## Related work (verified to exist; cite exactly these)
- Richardson 2026, "What Predicts Correctness in Text-to-SQL? A Selective-Prediction Study",
  arXiv:2607.06799 -- black-box signals 0.61–0.68 AUROC; log-prob 0.67; LLM judges
  GPT-4o-mini 0.72, Claude 0.78; two-provider ensemble 0.82; fine-tuned verifiers drop to
  ~0.66 on unseen schemas.
- Jin, Choi, Zhu, Kang 2026, "Pervasive Annotation Errors Break Text-to-SQL Benchmarks and
  Leaderboards", arXiv:2601.08778 -- 52.8% annotation errors in BIRD Mini-Dev, 62.8% in
  Spider 2.0-Snow; corrected sets released.
- Lei et al., Spider 2.0 (ICLR 2025), arXiv:2411.07763 -- enterprise workflows; o1-preview
  based agent 21.3%.
- BIRD Mini-Dev (500 questions, 11 databases; SQLite/MySQL/PostgreSQL; CC BY-SA 4.0).
- Jivani, Maheshwari, Sarawagi, TeCoD, arXiv:2604.28028 -- templates mined from labelled
  workloads, NLI matching + grammar-constrained decoding; up to 36% higher execution accuracy
  than in-context learning, 2.2x lower latency on matched queries.
- dbt Labs 2026 blog "Semantic layer vs text-to-SQL" -- semantic layer 98–100% in scope,
  text-to-SQL 84–90%, on an 11-question set (vendor evidence).
- dbt Labs 2026 report: 71% of respondents name incorrect or hallucinated outputs reaching
  stakeholders as a top concern.
(Do not cite anything else. If a sentence needs a citation not listed, rephrase it.)

## Planned evaluation (from docs/research/evidence_tiers_study.md, written before measuring)
- RQ1: do data-level checks flag wrong answers better/cheaper than model confidence
  (self-consistency k=5, one LLM judge, two-provider judge ensemble)?
- RQ2: which check catches which error type; RQ3: false-alarm rate on correct answers.
- Data: BIRD Mini-Dev (PostgreSQL) scored against the corrected annotations of Jin et al.;
  a hand-built fan-out set (60–100 questions); the 30-question set as a case study.
- Metrics: precision/recall/F1 of warnings, AUROC, risk–coverage, selective accuracy at
  80%/90% coverage, false alarms, extra calls/tokens (checks: zero).
- Hypotheses: H1 checks beat a judge on join/aggregation errors at lower cost, catch little
  on filter-value/column-meaning errors; H2 checks + one judge beat either alone;
  H3 figure grounding false-alarm rate < 5% on correct answers.
