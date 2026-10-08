# Paper B reruns R1–R3: pre-registration

Status: **fixed 2026-10-07, before any item of these three sets exists and before any system is run on them.**
Why these runs exist: the audit (`docs/paperb/AUDIT_2026-10-07.md`) found that the earlier page-finding test
compared Jev with the product's display order instead of the cross-encoder's ranking, that the routing test
half contained FinanceBench questions already used to diagnose the router (and was reused once), and that the
answerability gate's negatives were trivially easy. The literature check (`docs/paperb/LITERATURE_2026-10-07.md`)
places them: typed decision models on text-to-SQL, database-vs-documents routing and page finding in long
filings were not found in prior work.

Common rules:
- **No e-commerce or ONDC data anywhere.** Every workspace has only the SEC database (the routing setup's
  connection to `statesync-sec-db`) and the 84-filing FinanceBench library; the ONDC profile, normaliser and
  DIGIPIN are off in every configuration.
- **Frozen systems.** The orchestrator and MCP images are those built from commit `99e78f7`
  (`research/paperb/partd_images.json`); flags files and Jev wordings are the committed ones named below. Nothing
  is changed after a set exists; any change is logged at the end with its reason, and the paper reports it.
- **Hosts.** Question writing and the LLM router use Cheaper Inference (paid API); Jev uses OpenCode Zen
  (`jev-1.13`). No call goes through the Zed route.
- **Human checks are done by the two authors themselves, independently and blind to our labels**, on the samples
  named below. A model-filled sheet is never reported as a human check.
- **Statistics.** Paired exact McNemar (two-sided, alpha 0.05); 95% intervals for paired differences by bootstrap
  (2,000 resamples, seed 20261008, resampling items); Holm within each experiment's secondary family. Two arms are
  called *comparable* only when the 95% interval of their difference lies within ±8 points; *better* or *worse*
  only on a significant test.

## R1. Page finding in long filings (Jev against a cross-encoder and dense search)

**Question.** Does ordering retrieved passages by Jev's P(yes) to "Does this passage contain the information
needed to answer the question?" put the right page first more often than the cross-encoder's own ranking?

**Set** (`scripts/build_page_set.py --set r1`, written to `docs/eval/page_set_r1.json`):
- pages of the 84 filings with at least 600 characters; excluded: FinanceBench evidence pages ±1, and every page
  of the first page set (`docs/eval/page_set.json`, sampled or kept) ±1;
- sampled with seed **20261008**, at most 4 per filing, 420 candidates;
- writer **`glm-5.3-flash`** (Cheaper Inference), with the first set's WRITER prompt unchanged; checker
  `gpt-oss-120b` (Cheaper Inference), CHECKER prompt unchanged; the first **300** kept, in sampling order;
- gold: the source page.

**Retrieval.** The documents route's two-round search (`scripts/eval_writer_window.py`), in a workspace whose
database is the SEC connection, twice per question: `rel_off` (dense) and `rel_bge_titles` (bge re-ranker reading
titles). Each gives up to 25 passages with their dense `score` and, for bge, `rerank_score`.

**Arms** (each orders one of the two candidate lists; only the order differs within a list):
- **D**: the dense list by `score`, descending (ties: chunk id);
- **B**: the bge list by `rerank_score`, descending (ties: chunk id), the cross-encoder's own ranking;
- **JD**, **JB**: the dense and bge lists by Jev's P(yes), descending (ties: the D or B order). Jev's state text,
  instructions and model are exactly the first test's (`scripts/eval_jev_pages.py`).
- The product's display order (top 8 grouped by filing and position) is reported descriptively, never tested.

**Exclusions.** An item is dropped from every comparison, and counted, when either search failed, or when any
passage of its bge list has no `rerank_score` (a re-ranker failure, also checked against the host re-ranker's log).

**Measures.** page@1 (the first passage is on the gold page; primary), page@3, page@8 (the answer writer's
window); document@1 descriptive.

**Hypotheses.**
- **Primary R1-H1:** JB vs B on page@1.
- **Secondary (Holm over three):** R1-H2 JD vs B on page@1 (Jev with no GPU against the GPU cross-encoder);
  R1-H3 JD vs D on page@1 (Jev against dense search alone); R1-H4 JB vs B on page@3.

**Latency and cost** (descriptive): for 50 questions drawn with seed 20261008, Jev's wall time to score one
question's 25 passages sent concurrently; the cross-encoder's milliseconds per search from the host log; Jev's
cost per question from its usage.

**Human check.** Both authors independently read the same 30 kept items (drawn with seed 20261008: question,
answer, page text) and mark whether the page answers the question and whether the question is specific. Reported:
each author's agreement with the checker and with each other.

**Development evidence, labelled as such.** The FinanceBench (development) and first page-set runs are re-analysed
with the D and B orders defined above and reported as development or superseded, never as confirmation.

## R2. Routing between the database and the documents (fresh set)

**Question.** On questions none of the routers or their wordings were developed on, does one Jev choice question
route as accurately as the LLM front door, and do separate yes/no questions fail on two-source questions?

**Set** (`scripts/build_routing_set_r2.py`, written to `docs/eval/routing_set_r2.json`), 180 single questions, no
conversations, no FinanceBench question, labels fixed by construction:

| class | n | built from | acceptable |
|---|---|---|---|
| docs, statement | 30 | a page sampled as in R1 (seed **20261009**, disjoint from R1's sampled pages and the first set ±1); writer `glm-5.3-flash` asked for a question whose answer is a statement on the page, not a number | docs |
| docs, figure | 30 | as above; the answer is a specific figure on the page that is **not** one of the database's 23 consolidated annual metrics for that company and year (a segment, quarterly, per-unit, guidance or footnote figure) | docs |
| either | 30 | templates: one of the 23 metrics for one company and fiscal year, where the database holds it and that year's 10-K is in the library | sql or docs |
| sql | 30 | templates over the 31 companies: rankings, counts and averages across companies, or one company's series over years at least one of which has no 10-K in the library; the reference SQL must return rows | sql |
| hybrid | 30 | templates: 15 dependent ("which of A, B, C had the highest M in Y, and what does that company's Y 10-K say about T?", all three filings in the library) and 15 independent (an aggregate, plus what a named filing says) | hybrid |
| neither | 30 | templates: 10 standard metrics of companies in neither source; 10 live or future data for the 32 companies (current share price, fiscal 2028); 10 requests outside data analysis | neither |

- The writer's questions pass the R1 checker plus, for figures, a check that the answer is not one of the 23
  consolidated annual metrics (`gpt-oss-120b`; items it rejects are dropped before any routing).
- Templates use several phrasings each, chosen at random with seed 20261009, none copied from the first routing
  set; the reference SQL of every sql, either and hybrid item is executed and must return rows.

**Routers** (one run each, first turn only, no history):
- **LLM front door**: `research/paperb/flags_partd_v3_route_llm.toml` (router model `qwen-3-8-27b`, Cheaper
  Inference);
- **Jev choice, wording 1**: `flags_partd_v3_route_jev_choice.toml` with the default wording;
- **Jev choice, wording 2**: the same with `research/paperb/statesync_jev_choice_v2.toml` mounted;
- **Jev yes/no**: `flags_partd_v3_route_jev.toml` with `research/paperb/statesync_jev_jev.toml` (thresholds fitted
  on the first routing set's development half);
- **Semantic router**: `scripts/eval_semantic_router.py`, k = 5, the first routing set's development labels.

**Measures.** Route accuracy against the acceptable routes; per-class accuracy; macro accuracy; median routing
latency; LLM calls before routing.

**Hypotheses.**
- **Primary R2-H1:** Jev choice wording 2 vs the LLM front door on accuracy (and the comparability rule).
- **Secondary (Holm over three):** R2-H2 Jev choice wording 2 vs Jev yes/no on accuracy; R2-H3 Jev choice wording
  1 vs the LLM front door; R2-H4 the semantic router vs the LLM front door.
- **Predicted, descriptive:** Jev yes/no routes few hybrid questions to hybrid (the first set: 2/12).

**Human check.** Both authors independently label the same 40 items (drawn with seed 20261009, stratified by
class) with one of the five classes, from the question alone plus a one-page description of the two sources,
blind to our labels. Reported: agreement with the construction labels and Cohen's kappa between the authors. If
either author agrees with the construction labels on fewer than 85% of items, the paper says so next to every R2
number.

## R3. Answerability gate with hard negatives

**Question.** Can a Jev yes/no question tell whether a database holds the data a question needs when the
database is the right one but the needed tables or columns are missing?

**Pairs.** The held-out BIRD questions whose PostgreSQL reference query is correct under the corrected labels
(`docs/eval/paperb_mutations.json`, op `reference`, `correct`: 298 of 393). Tables and columns the reference
query reads are taken from its parse tree (`pglast`, in the MCP container, resolving aliases against the
database's catalogue); a question whose references cannot be resolved is dropped and counted. Per question:
- **P**: its own database's full schema card (format of `scripts/eval_jev_answerable.py`; every card is under its
  6,000-character cap);
- **N-table**: the card without every table the reference query reads;
- **N-column**: the card without every column the reference query reads (a table left with no column is
  removed).

**Jev.** `jev-1.13`, the first gate's instructions and state format, unchanged. Two forms of the question: as
the pipeline asked it, with BIRD's hint (primary), and without the hint (secondary).

**Measures** (descriptive, 95% bootstrap intervals over questions):
- AUROC of P against N-column (primary) and against N-table;
- at the first gate's frozen threshold (0.11): false refusals on P and the share of each negative refused;
- cross-fitted: questions split in two halves (seed 20261008); on each half, the largest threshold refusing at
  most 1% of P is fitted and applied to the other half; pooled false refusals and negatives refused.

**Expected.** N-column is much harder than the first gate's other-database negatives; the paper reports whatever
it is.

## R4. Telling the router what the database covers (added 2026-10-08, before its code, set or runs exist)

**Why.** In R2, the LLM router and every Jev router sent all ten questions about companies absent from the database
to SQL: the database card lists tables and columns, not which entities and periods the rows cover.

**Change under test.** `[front_door] card_coverage = "on"` (default `"off"`, so every earlier result is unchanged):
the database card gains a coverage line built without any model call, cached per (tenant, dataset). For each table in
the card, every text column with at most `coverage_max_values` (40) distinct values is listed in full, and every date
or timestamp column gets its minimum and maximum; the line is cut at `coverage_max_chars` (2,500). It reads through the
same read-only gate as every other query. Nothing else in the router or its prompts changes.

**Set** (`scripts/build_routing_set_r4.py`, seed 20261010, `docs/eval/routing_set_r4.json`), 90 single questions, labels
by construction, none from FinanceBench or the R2 set:
- *neither, unknown company* (30): a standard annual metric and a fiscal year 2016–2023 for a US public company in
  neither source, drawn from a fixed list disjoint from R2's;
- *neither, out-of-range year* (15): a standard metric for a company in the database, for a fiscal year 1998–2004
  (before any period the database holds);
- *either* (30): a metric the database holds for a company and fiscal year whose 10-K is in the library;
- *sql* (15): a metric the database holds for a company and fiscal year whose 10-K is not in the library.
Every either and sql item's reference query must return a value; templates use new phrasings.

**Arms** (one run each, images rebuilt from the commit that adds the flag; the flag off reproduces the old card):
LLM router with coverage off and on; Jev choice wording 2 with coverage off and on.

**Hypotheses** (exact McNemar, two-sided, alpha 0.05):
- **Primary R4-H1:** Jev choice wording 2, coverage on vs off, accuracy on the 45 *neither* questions.
- **Secondary (Holm over three):** R4-H2 the LLM router, the same comparison; R4-H3 and R4-H4 Jev and the LLM router,
  coverage on vs off, accuracy on the 45 in-coverage questions (the change must not cost them: reported with the
  95% interval and the ±8-point comparability rule).
- **Also reported, descriptive:** both routers with coverage on over R2's 180 questions, against their R2 runs. R2's
  ten unknown-company questions motivated this change, so this is a no-harm check, not confirmation.

**Human check.** The construction is mechanical (company lists, years, executed queries); both authors read the 90
questions for wording only.

## Change log

1. **2026-10-07, R3, before any Jev call (a note, nothing changed).** The pair builder resolved all 298 reference
   queries (0 dropped). Two N-table cards are empty (single-table databases). BIRD repeats some data across tables
   (for example, school names are in both `schools` and `frpm`), so a few N-column negatives may still be
   answerable through another column; the N-column AUROC is therefore a lower bound on how well Jev separates
   answerable from unanswerable cards.
2. **2026-10-07, R1 set build (operational).** The first build ran the 420 writer calls one at a time; the
   reasoning writer takes 8–20 s a call (about 2 hours). It was stopped after about 55 minutes, before it had
   written anything (the script saves only at the end, so no question was seen), and rerun with the same calls
   split into 8 concurrent batches (`build_page_set.py`, `PARALLEL`). Prompts, models, seed and sampling are
   unchanged; only the sampled outputs of a non-deterministic model differ from what the stopped run would have
   produced.
3. **2026-10-08, R1 set (a shortfall, nothing changed).** Under the pre-registered cap of 4 pages per filing only
   330 pages could be sampled (83 filings), not 420. The checker kept 329 (1 dropped: the page does not answer it),
   so the first 300 kept, in sampling order, form the set as planned.
4. **2026-10-08, R2 set build (operational).** With 48 candidate pages per documents kind, the checker kept only
   25 figure questions (fewer than 30), and the builder stopped, as written, before saving anything; no question
   was looked at. The candidate pool was raised to 80 pages per kind (same seed, same alternation, same prompts and
   checks); the first 30 kept of each kind are used.
5. **2026-10-08, R2 set build (operational).** The rebuild kept only 15 statement questions, far fewer than the first
   attempt, and stopped before saving. Calls that fail at the provider (for example under concurrency) return no
   text and were being counted as "writer reply did not parse". Failed calls are now asked again (up to three
   rounds, same prompt; answered calls are never asked again), and the builder prints the drop reasons and call
   errors. Nothing else changed; no question was looked at.
6. **2026-10-08, R2 set built (a note, nothing changed).** 180 items, 30 per class. No call failed; 35 of 80
   figure-question replies did not parse (the reasoning writer most likely used its output budget before writing
   JSON), so the 30 figure questions come from the pages where it did produce one. Statement questions: 1 of 80
   dropped by the checker. The set and the authors' sheet are committed before any router runs.
7. **2026-10-08, R1 latency (after the results, descriptive only).** Sending one question's 25 Jev calls at once
   was refused by the API (HTTP 429). The latency measure therefore sends at most 8 at once and retries 429s with
   back-off, counting the retries in the wall time, and reports how many retries were needed. The rate limit
   itself is reported as a deployment constraint.

