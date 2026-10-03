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

## Results

(To be filled in after all runs, label freezing and the analysis in `scripts/analyze_bird.py`.)

- Accuracy: SQL label (relaxed, strict), answer label.
- Per-signal table: flagged, precision, recall, false-alarm rate.
- Matched flag rate vs the Gemini judge (primary).
- Risk–coverage: judge, checks then judge, self-consistency.
- Confirmatory tests H1–H4 with Holm, including preconditions and "untestable" outcomes.
- Error types and which signal catches which (RQ2), with Cohen's kappa against the owner's 20%.
- Run-to-run label flip rate.
- Sensitivity: original gold; strict label.
- Cost and latency.
