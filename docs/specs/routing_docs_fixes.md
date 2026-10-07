# Routing and documents-path fixes (Paper B, Part D v3)

Status: **final, owner-approved direction 2026-10-05** (fix everything first; ONDC separate; one call before routing; Jev; SEC test bed; delegation). Implementation starts with T1.

## 0. Why

D-dense (Paper B change 9) ran the shipped product on the 150 FinanceBench questions. Only 20% of the
answers were correct, and the losses came from the design, not from the models. The owner's direction:
*"We have to fix all the design flaws, or what kind of system paper would we be making ... design fix
should come first ... Make sure it is right."*

So Part D becomes: diagnose the shipped system (done), fix it, verify each fix, then run the comparisons.
The comparisons are re-ranker off/on, an LLM router against a Jev decision router, and the hybrid split,
all on the fixed system only.

## 1. Flaws found

Every flaw below has evidence from D-dense or the code. The numbers are from D-dense's 150 answers and
the early grading (change 9).

| ID | Flaw | Evidence | Where |
|---|---|---|---|
| R1 | The router sees only the 20 most recently uploaded titles. | 84 filings. When the question's filing was among the 20, 36/37 reached the documents; otherwise 60% went to SQL. | `mcp/tools/library_tools.py` `_workspace_documents` (`ORDER BY created_at DESC LIMIT router_titles_max`); `supervisor.py` `library_suffix` |
| R2 | The router is told to decide "by where the value lives" but never sees what the database holds. | The general prompt says "a number stored in a table is sql" with no schema, so numeric filing questions went to SQL against an e-commerce database. | `supervisor.py` `_GENERAL_PROMPT` |
| R3 | A wrong route is never recovered. | A SQL question the database cannot answer ends in the error exit; it never tries the documents. 68 of 150 answers came from SQL, and 1 was correct. | `graph.py` `_after_sql`, `_after_error_exit` |
| S1 | The SQL agent cannot say "this database does not hold that". | Its prompt demands exactly one SELECT, so it wrote `SELECT NULL AS answer FROM olist.sellers`. | `sql_agent.py` `_SYSTEM_PROMPT` rule 1 |
| S2 | Layer 1's "statement reads no table" is classified `permission_denied`, which is terminal and misleading. | 45 of 68 SQL answers told users "permission_denied". | `config/error_taxonomy.json` rule `^\[Layer 1 Gate\] Rejected` |
| S3 | The writer is told to name the failure category in the answer. | Users read "classified failure: permission_denied". | `synthesizer.py` (sql_error context) |
| S4 | Zero rows are read as "none or zero". | "Best Buy had 0 stores in both years", from a shop database. | `synthesizer.py` ("The answer may be 'none' or 'zero'") |
| D1 | The writer reads the first 8 of up to 25 passages, ordered by document and then by position in it, not by relevance. | A gold page was among the 25 for 60/82 answers but among the writer's 8 for only 31/82. Correct: 20/31 when inside the 8, 9/51 when not. | `unstructured_knowledge.py` `_semantic_search_expanded`; `synthesizer.py` `_format_doc_context` |
| D2 | D1's re-sort throws away the re-ranker's order. Its +16-point gain was measured on one search call, not on what the writer reads. | `eval_financebench.py` scores `vector.semantic_search` top 10. | as D1 |
| D3 | The wrong filing comes first, and nothing tells the re-ranker or the writer which filing a passage is from. | The right filing ranked first in 38/82. The writer sees `document_id`, not the title, so it said "the documents only cover FY2015". | `vector_tools.py` `_rerank` input; `synthesizer.py` `_format_doc_context` |
| H1 | Hybrid's documents half uses the SQL result only for ONDC seller/provider IDs, and always runs after SQL. | `_extract_party_ids` reads only `seller_id`/`provider_id`. | `unstructured_knowledge.py` `hybrid_docs_node` |
| H2 | Hybrid always splits into two halves, even when a single-source question was labelled hybrid. | `hybrid_plan_node` | as H1 |
| X1 | The output schema rejects titled citations, so every answer citing an uploaded document loses its chart and logs an A7 error. | Repeated in the Part D container log. | `schemas/synthesizer_output.py` (citation `additionalProperties: false`) vs `build_citations` adding `title` |
| X2 | Sample datasets such as olist get the ONDC logistics framing in the SQL prompt. | `datasets.frame_prompt` with `synthetic_schemas = ["public","olist","live"]` | minor |

Checked and **not** a flaw: number grounding checks document answers against their passages
(`main.py` passes `retrievals`).

### 1b. ONDC tailoring in the general path

The owner's rule, 2026-10-05: *ONDC-related things should be a completely separate thing now.* Anything
built for the synthetic ONDC corpus must not shape answers on other data.

| ID | ONDC tailoring | Applies to | Where |
|---|---|---|---|
| O1 | Normaliser: "query-normalization step for an ONDC logistics analytics system", with seller and Mumbai examples. | **every question** | `language_normalizer.py` |
| O2 | Answer writer: "a data analytics assistant for an ONDC logistics network" (full and short prompts). FinanceBench answers were written under it. | **every answer** | `synthesizer.py` `_SYSTEM_PROMPT`, `_SYSTEM_PROMPT_SHORT` |
| O3 | Follow-up rewriter: "for an ONDC logistics analytics system". | **every question** | `nodes/rewrite.py` |
| O4 | SQL writer and corrector: "logistics data analytics system (ONDC network)". | `public`, **`olist`**, `live` (`synthetic_schemas`) | `sql_agent.py`, `datasets.py` |
| O5 | The router's ONDC prompt lists every table, its columns and the ONDC document kinds; the general prompt replaced that with a rule and no description. | the ONDC prompt only for `public`; the general prompt for everything else | `supervisor.py` |
| O6 | Hybrid splitter: ONDC prompt for `public`; the general one elsewhere. | `public` | `unstructured_knowledge.py` |
| O7 | Hybrid documents half narrows only by `seller_id`/`provider_id` (ONDC columns). | every dataset | `unstructured_knowledge.py` `_extract_party_ids` |
| O8 | DIGIPIN spatial resolver. | `synthetic_schemas` | `spatial_resolver.py` |

O5 carries the lesson. The ONDC router worked because it was told what each source holds. The general
version kept the rule and dropped the description, instead of building the description from each dataset.

## 2. Fixes

Each fix sits behind a `[pipeline]` flag. This keeps the shipped behaviour reproducible, which Paper A's
frozen runs use the product's SQL path for and which the ablations need. The flags become the new
defaults once §3's gates pass. They change nothing until then.

- **F-FD (the front door: R1, R2, O1, O3, and the owner's one-call design).** The normaliser, follow-up
  rewriter and router (2–3 model calls before any answer work) are replaced by one front-door step. It
  decides from evidence about both sources. §2b has the protocol.
- **F-S1 (S1).** The SQL agent may answer `NOT_ANSWERABLE: <reason>` instead of SQL. This becomes a
  typed outcome, not an error string.
- **F-S2 (S2).** A new taxonomy category, `no_data_source`, covers "reads no table" and F-S1. It is not
  retried. Every other Layer 1 rejection stays `permission_denied`, so the security behaviour is
  unchanged. Kept in-house because it touches the security gate.
- **F-RR (R3, route recovery).** A SQL route ending in `no_data_source`, in a workspace that has
  documents, runs the documents route once. The trace records `recovered_from: sql`, and the answer says
  plainly that the database had no data on it.
  - Documents to SQL recovery needs a reliable "nothing relevant" signal, which RAG v2 found it does not
    have (abstention 1/10, fitted in-sample), so it is not in this round.
- **F-S3 (S3).** Failures are explained in plain words, mapped from the category. The category stays in
  the trace and the "How I got this" panel.
- **F-S4 (S4).** Zero rows means "no matching records"; the writer is told never to turn that into a
  figure about something the query did not measure.
- **F-D (D1, D2).** The writer's passages are chosen by relevance: the re-ranker's score when
  re-ranked, else the retrieval score. The top N stays at 8, unchanged, to isolate the fix. The
  exception is a short document (N chunks or fewer), which is kept whole, because short ONDC documents
  are the case the document-order rule was built for. Each passage is labelled with its title and page.
- **F-T (D3).** The re-ranker and the writer see `Document: <title> · page <p>` above each passage. No
  re-embedding.
- **F-X1 (X1).** The citation schema allows `title`.
- **F-O (O1–O8; replaces F-X2).** ONDC becomes a separate *dataset profile*. Its prompts, table list,
  document kinds, DIGIPIN and party-ID join move into one module that is loaded only for the ONDC sample
  dataset.
  - The core path gets neutral prompts: normaliser, rewriter, writer, SQL writer, router and splitter.
  - Source descriptions are generated per dataset: the database card and document evidence of F-FD (§2b).
  - Multilingual input (Hindi, Hinglish) stays a core feature, with neutral examples, if the owner keeps
    it.
  - The ONDC sample keeps working through its profile, so its existing tests still pass and its own
    regression check runs on it.
  - `olist` and `live` leave `synthetic_schemas`.

## 2b. The front door: one model call before routing, and Jev

**What it replaces.** Today a question passes through three steps before any answer work:
- the normaliser, a model call on *every* question, which for English can only pass the text through.
  It ran on all 393 BIRD questions in Paper A (median 5.4 s) and on all 150 FinanceBench questions
  (5–10 s), changing none of them;
- the follow-up rewriter, a model call on every turn after the first;
- the router, a model call, blind (R1, R2).

The normaliser exists for Hindi and Hinglish questions (ONDC's market). It ran on the English
benchmarks only because `language_gate = "llm"` ships on. A script gate (Devanagari or Hinglish marker
words) has existed since 29 Sep, switched off.

**Step 1: evidence, with no model.**
- *Language gate:* the script test.
- *Search text:* the question; for a turn after the first, the question followed by the previous turn's
  standalone question.
- *Document evidence:* one dense search, giving the 5 best documents (title, best score, first 300
  characters of the best passage) and the library's size. It is reused as the documents route's first
  round.
- *Database card:* the dataset's tables and columns from the schema, plus the workspace description if
  there is one. It is cached per dataset and cut to a token budget.
- *Session state:* the last 3 turns from `session_state.conversation_turns`, each as standalone
  question, route, whether it was answered, and the first 200 characters of the answer.

**Step 2: understanding and routing.** Two variants, and the paper compares them.

**(a) One LLM call (the owner's design).** The call gets the question, the session state, the database
card and the document evidence, and returns one JSON object:
`{language, standalone_question, uses_history, needs_database, needs_documents, database_question,
documents_question, documents_depend_on_database, out_of_scope}`.
- `standalone_question` is in English. A follow-up is resolved from the history. Named entities are
  copied exactly, as the normaliser does now.
- `needs_database` and `needs_documents` are each one of yes, no or unsure.
- The two `*_question` fields are filled only when both sources are needed. This is the split.

So translation, follow-up resolution, routing and the hybrid split cost one call instead of up to
four.

**(b) Jev decides, and the LLM writes only when text must be written.** Jev cannot write text, so the
LLM is called only to translate, to resolve a follow-up, or to split a two-source question. Every
decision is Jev's.
1. **Jev, one request with typed questions over the state below:**
   - `uses_history` (noul): "does the new question depend on earlier turns to be understood?";
   - `needs_database` (noul);
   - `needs_documents` (noul);
   - `out_of_scope` (noul).
2. **LLM, only if** the language gate fired, or P(uses_history) ≥ τ_h, or both sources are needed.
   The call returns `standalone_question` and the split, with no routing fields.
3. **Re-check:** if the LLM rewrote the question, the evidence is re-searched with the standalone
   question, and Jev's needs-questions are asked again.

The common case, a first-turn English question that needs one source, then makes **no** LLM call
before answering, only one Jev request (input tokens at $0.042 per million).

**The state Jev sees, including across sessions.** Jev is stateless, so the session goes into each
request as application state:

```json
{"question": "And in FY2022?",
 "conversation": [{"turn": 1, "question": "What was 3M's FY2018 capital expenditure?",
                   "route": "documents", "answered": true, "answer_start": "3M's FY2018 capex was $1,577 million ..."}],
 "database": {"description": "SEC reported figures for 32 companies",
              "tables": [{"name": "annual_metrics", "columns": ["company", "metric", "period_end_year", "value", "unit"]}]},
 "documents": {"library_size": 84,
               "best_matches": [{"title": "3M 2022 10K", "score": 0.71, "excerpt": "..."}]}}
```

- The questions' `instructions` and `criteria` are fixed text.
- The thresholds τ (P_db, P_docs, P_history) are fitted on the routing set's dev split only.
- The model is pinned to `typesafe/jev-1.13`, and the served version is recorded per call.

History and passages are untrusted data. For Jev, injected text can at worst flip a decision; it
cannot produce text or call a tool. That is a smaller surface than an LLM router's, and the paper
notes it.

**Step 3: routes.**
- Only one source needed: that route.
- Both needed: the split (§5).
- Neither: an honest "this workspace does not hold that". Routing never silently defaults to SQL.
- A wrong pick is still recovered by F-RR.

The old path stays behind `[pipeline] front_door = "off"`; the shipped path is unchanged until the
gates pass.

## 3. Verification: the gates, fixed before any code is written

1. **Unit tests** for every pure function: the router's input assembly, the `NOT_ANSWERABLE` parser,
   the taxonomy split, the recovery decision, passage selection and the schema.
2. **SQL regression (G1).** BIRD's 393 held-out questions and the olist questions through the product.
   - False `NOT_ANSWERABLE` on answerable questions must be ≤ 1%.
   - Accuracy must not drop significantly (paired exact test).
   - Questions whose true answer is zero must still say zero.
3. **ONDC documents regression (G2).** The in-house retrieval set (`eval_retrieval.py`), scored on the
   passages the writer reads: no worse than shipped.
4. **FinanceBench, development use (G3).** Measured on the writer's passages, not the search output:
   documents reached, gold page among the writer's passages, right filing first. Then end-to-end
   accuracy.
5. **Live checks (G4).** `verify_library`, `verify_workspaces`, `verify_deployed`, and charts on
   uploaded-document answers.

A fix that fails its gate is reworked before anything is measured with it.

## 4. Router comparison (owner-approved test bed, 2026-10-05)

**Test bed.** One workspace holds the **SEC database** (`demo/sec_db.sh`: the SEC's reported figures,
23 metrics, for the same 32 FinanceBench companies, connected as the workspace's database) and the
**84 filings**. Both sources hold overlapping facts, so routing is a real decision. With the shop
database every filing question is trivially "documents".

**Labelled routing set.** Labels are written by rule before any router runs (`docs/eval/routing_set.json`).
The owner checks a random sample of at least 30, so two people agree on them.

*Single-turn, about 200 questions in five classes:*
- **docs-only:** FinanceBench's narrative questions.
- **either:** FinanceBench questions whose figures `sec.annual_metrics` holds, checked against the
  database by metric, company and year. Both routes are acceptable.
- **sql-only:** new questions across companies that no single filing answers, such as "which of the 32
  had the highest FY2022 operating margin".
- **hybrid:** new questions that need a database figure plus a filing's explanation.
- **neither:** out of scope; the right behaviour is to say so.

*Multi-turn, about 30 conversations of 2–3 turns.* Each turn carries:
- a gold standalone question;
- whether it depends on the history;
- its gold route.

These test follow-up resolution and routing with session state, which is how Jev works across a
session (§2b).

The set is split dev/test, stratified by class, with conversations kept whole. Prompts and thresholds
are fixed on dev only.

**Arms, one per standard family of router, all given the same Step 1 evidence where they use any:**
1. **shipped:** normaliser, rewriter and a blind router, with titles capped at 20; measures the fix;
2. **semantic router:** embedding similarity to labelled dev examples; cheap and deterministic;
3. **front door (a), one LLM call:** qwen3.8-27b, the role the router has now;
4. **front door (b), Jev decides:** `typesafe/jev-1.13`, pinned; the LLM writes only when needed. A
   three-way `choice` variant is reported alongside.

Related: rule-driven routing between a database and documents (Bai et al., arXiv 2510.02388) found the
two sources complementary. It also found that naively querying both adds noise and cost, which is the
case for deciding "both" deliberately (§5). Trained classifiers for routing: Adaptive-RAG (NAACL 2024).

**Measures (test split).**
- route accuracy and macro-F1 by class, counting either route as acceptable for the *either* class;
- follow-up accuracy: dependence detected, and the standalone question right (judged against gold);
- agreement between two runs of the same arm;
- Jev's calibration;
- model calls, milliseconds and cost before the answer work starts;
- end-to-end answer accuracy by arm.

## 5. Hybrid: how to split the routes

Shipped: one label → an LLM splits the question → SQL half → documents half (ONDC IDs only).

Proposed:
1. **Decide with two probabilities, not one label** (§2b). "Needs both" is then a measured, calibrated
   statement, not a third class the router has to guess.
2. **Plan.** In the same front-door call (a), or the one LLM call (b) makes when both are needed, the
   split returns `database_question`, `documents_question`, `documents_depend_on_database`, and the
   result column that names the entities.
3. **Run.**
   - Independent halves run in parallel.
   - A dependent documents half runs after SQL, restricted to documents whose title names one of the
     entities SQL returned. This generalises ONDC's seller IDs to any entity, for example company names
     to their filings.
4. **Merge.** The writer gets both, labelled. If one half fails, the answer says which part could not be
   answered.

Measured on the hybrid class: both halves right, one, or none, against the shipped hybrid path.

## 6. Keeping the results honest

- FinanceBench's 150 questions revealed these flaws, so they are a development set. The paper says so.
  The fixes are general (nothing FinanceBench-specific), and their parameters are fixed here, in advance.
- The routing set is new, written before any router runs, and split dev/test.
- The paper reports, in order:
  1. the system as shipped (D-dense);
  2. each fix's contribution (writer-input metrics for all fixes, end-to-end for the largest three);
  3. the fixed system;
  4. re-ranker off/on, in the fixed system only;
  5. the routing arms;
  6. the hybrid split.
- The early look (change 9) and this redesign are disclosed in the protocol's change log, as Part D v3.

## 7. Execution

**Isolation.** Part D v3 runs its own MCP and orchestrator containers, built from the new code under
their own image tag: `statesync-mcp-paperb` and `statesync-orchestrator-paperb`. The shared stack
(`statesync-mcp-1`, `statesync-orchestrator-1`) is never rebuilt, so Paper A's environment does not
change.
- Behaviour changes sit behind flags that are off by default.
- ONDC's separation is structural, since its prompts move into the profile; the git history pins
  Paper A's code.

**Delegation (owner, 2026-09-01 and 2026-10-05).** The lead writes a task spec and its acceptance tests
and commits its own work first. A `spec-coder` delegate implements in its own git worktree. The lead
reads the diff, runs the tests and probes past the spec, then merges. Kept in-house: F-S2 (the security
gate), the routing set's labels, the thresholds, and all measurement.

| Task | Content | Depends on |
|---|---|---|
| T1 | F-O (ONDC profile, neutral prompts) + F-X1 (citation schema) | – |
| T2 | F-S1, F-S3, F-S4, F-RR (SQL outcome, plain failures, zero rows, route recovery); the lead does F-S2 | T1 |
| T3 | F-D, F-T (writer's passages by relevance, titles to the re-ranker and the writer; MCP request parameter, off by default) | T1 |
| T4 | F-FD: Step 1 evidence, front door (a), and the Jev client and front door (b) | T1–T3 |
| T5 | §5 hybrid split and run | T4 |

**Schedule.**

| When | Work |
|---|---|
| 5–6 Oct | T1, T2, T3; routing set written and the owner's check |
| 7 Oct | T4 (Jev key from the owner, via OpenRouter); gates G1–G4; thresholds on dev |
| 8 Oct | T5; final runs and grading |
| 9–14 Oct | Writing |
| 15 Oct | Submission |

- **Must:** T1–T4 and the routing comparison.
- **Should:** T5.
- **Could:** documents to SQL recovery.

If 8 Oct slips, T5 goes to future work, rather than squeezing the verification.

## 8. Notes for T4 (decided 2026-10-05, not yet specified as a task)

- **Split.** T4a: Step 1 evidence, front door (a), out-of-scope handling and the test hooks. T4b: the Jev
  client (OpenRouter Decisions API, `typesafe/jev-1.13`) and front door (b). T4a starts only after T2 is
  merged, because both touch `graph.py`, `state.py` and `main.py`.
- **Database card.** Built from `mcp_client.schema_list_tables` and `schema_describe_table`, cached per
  dataset, plus the workspace's `business_context` (`workspaces_manage` "context", as the SQL agent
  gets it).
- **Out of scope.** A state field `out_of_scope: bool` sends the graph straight to the writer with a
  fixed config message and no model call. The `Route` literal stays three routes, and the turn record
  stores route `"neither"`.
- **Mapping for front door (a).** The answers to "needs database" and "needs documents" map to a route:

  | needs database | needs documents | route |
  |---|---|---|
  | yes | yes | hybrid |
  | yes | no or unsure | sql |
  | no or unsure | yes | docs |
  | unsure | unsure | hybrid |
  | unsure | no | sql |
  | no | unsure | docs |
  | no | no | neither |

  `out_of_scope` true means neither. An unreadable reply is treated as unsure on both, so hybrid, and
  the trace flags it. There is never a silent SQL default.
- **Jev failure.** If the Jev call fails, front door (a) answers instead, and the trace records the
  fallback.
- **Test hooks.** Off unless `[pipeline] debug_hooks = "on"`, which only Part D's own container sets:
  - `debug_route_only`: return after routing, with the trace, run no specialist, write no turn;
  - `debug_history`: gold earlier turns, used instead of the stored history.

  The routing evaluation (`scripts/eval_routing.py`, in-house) uses both, for every arm.
- **New config is additive only:** a `[front_door]` section for the thresholds and caps, and new
  `[pipeline]` flags. config/ is mounted live into older images.
