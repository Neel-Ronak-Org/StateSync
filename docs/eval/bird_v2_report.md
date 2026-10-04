# Evidence-tiers study, protocol v2: results report (DRAFT, methods only)

Status: **draft, 2026-10-04.** The methods are written before any held-out result was looked at.
The results sections are filled in once every run is complete, labels are frozen and the
pre-registered analysis has run.

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
- Error types: blind annotation of every wrong answer (Claude), plus a random 20% blind by the owner.

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

The fan-out set (H1) and the run-to-run repeat are reported below once complete. Holm is applied over
all confirmatory tests then.

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
| self-consistency (5 samples) | **0.739** | **0.135** | 85.8% |
| Gemini 3.8 Flash judge | 0.697 | 0.153 | 85.4% |
| data checks first, then judge | 0.697 | 0.152 | 85.6% |

The secondary judge, gpt-oss-120b, is partial: 220 of 393, because Groq's free-tier daily cap ran out.
It is reported once complete.

### Pre-registered tests

| test | result | outcome |
|---|---|---|
| H1 (fan-out set) | pending | the fan-out run is in progress |
| **H2** checks then judge beats judge (AURC) | gain 0.001 (95% CI −0.005 to 0.009), p = 0.42 | **not supported** |
| **H3** grounding false alarms < 5% of correct texts | 2 of 242 (0.8%; upper 95% bound 2.6%), p = 0.0004 | **supported**, but grounding fired on only 5 answers (recall 4%) |
| **H4** repair fixes > breaks, +2 points | 2 repairs triggered | **untestable** (precondition: 10) |

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

### What this says so far (held-out only; H1 pending)

- **Model-free data checks are not a better warning than an LLM judge.** At the same flag rate they
  catch about as many wrong answers or fewer (−1.8, CI includes 0), and they add nothing to the
  judge's ranking (H2).
- **They rarely apply:** 17 of 393 answers (4%), catching 10% of the wrong ones.
- **The strongest cheap signals are process signals:** the query failed, came back empty, or needed a
  retry (79% precision at 21% recall). Self-consistency is the best ranking. These are ordinary
  baselines, and they beat the checks the study was built around.
- **Most real errors are semantic:** filter values, column meaning, aggregation intent. A check that
  only sees the SQL and its own result cannot see them; the pilot suggested this, and the held-out
  data confirms it on corrected labels.
- **A fifth of the "wrong" answers are scorer false negatives,** and a further 9 of 90 involve
  benchmark problems (hints or gold). Execution-match accuracy on BIRD understates this system by
  about 5 points.
