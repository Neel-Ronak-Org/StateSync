# Routing and documents-path fixes (Paper B, Part D v3)

Status: **draft for the owner's review, 2026-10-05.** Nothing here is implemented yet.

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

## 2. Fixes

Each fix sits behind a `[pipeline]` flag. This keeps the shipped behaviour reproducible, which Paper A's
frozen runs use the product's SQL path for and which the ablations need. The flags become the new
defaults once §3's gates pass. They change nothing until then.

- **F-R (evidence router: R1, R2).** The router's input is no longer a title list but evidence from both
  sources:
  - a *database card*: the dataset's tables with their columns, cut to a token budget, plus the
    workspace description if there is one;
  - *document evidence*: the 5 best-matching documents for this question from one dense search, each
    with title, best score and the start of its best passage, plus the library's size.

  The prompt says to choose by evidence. The search is reused as the documents route's first round, so
  there is no extra embedding call.
  - Rejected: listing all titles (breaks at scale and depends on titles being descriptive) and keyword
    title matching (fragile).
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
- **F-X2 (X2).** Optional: the SQL prompt's framing follows the dataset.

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

## 4. Router comparison: LLM against Jev (the owner's addition)

**Test bed.** One workspace with the **SEC database** (`demo/sec_db.sh`: the SEC's reported figures for
the same 32 FinanceBench companies) and the **84 filings**. Both sources hold overlapping facts, so
routing is a real decision. With the shop database every filing question is trivially "documents".

**Labelled routing set (about 200 questions, five classes).** Labels are written by rule before any
router runs, and the owner checks a sample, so two people agree on them.
- **docs-only:** FinanceBench's narrative questions.
- **either:** FinanceBench questions whose figures `sec.annual_metrics` holds, checked mechanically by
  metric, company and year. Both routes are acceptable.
- **sql-only:** new questions across companies that no single filing answers, such as "which of the 32
  had the highest FY2022 operating margin".
- **hybrid:** new questions that need a database figure plus a filing's explanation.
- **neither:** out of scope; the right behaviour is to say so.

The set is split dev/test, stratified by class. Prompts and thresholds are fixed on dev only.

**The two routers get the same evidence (F-R).**
- *LLM router:* the shipped role (qwen3.8-27b), returning the JSON route.
- *Jev router:* `typesafe/jev-1.13`, version pinned. It answers two independent Boolean questions,
  *needs the database?* and *needs the documents?*, which give P_db and P_docs. The route follows from
  thresholds τ_db and τ_docs fitted on dev: both high is hybrid, one high is that route, neither is
  "neither". A three-way `choice` variant is reported alongside.

**Measures (test split).**
- route accuracy and macro-F1 by class, with "an acceptable route" for the *either* class;
- agreement between two runs of the same router (consistency);
- Jev's calibration;
- milliseconds and cost per decision;
- end-to-end answer accuracy by router.

## 5. Hybrid: how to split the routes

Shipped: one label → an LLM splits the question → SQL half → documents half (ONDC IDs only).

Proposed:
1. **Decide with two probabilities, not one label** (§4). "Needs both" is then a measured, calibrated
   statement, not a third class the router has to guess.
2. **Plan.** The decomposer returns `sql_subtask`, `doc_subtask` and `doc_needs_sql_entities` (with the
   result column that names them).
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
  5. LLM against Jev routing;
  6. the hybrid split.
- The early look (change 9) and this redesign are disclosed in the protocol's change log.

## 7. Order of work and cut line

| When | Work |
|---|---|
| 6 Oct | F-X1, F-S1–S4, F-RR, F-D, F-T, F-R implemented, with unit tests; G1–G4 run. Routing set written and checked. |
| 7 Oct | Jev router (needs the key). Hybrid plan and run (§5). Thresholds on dev. |
| 8 Oct | Final runs: FinanceBench fixed system, re-ranker off/on; routing test split, LLM and Jev; hybrid. Then grading. |
| 9–14 Oct | Writing. |
| 15 Oct | Submission. |

- **Must:** everything in §2, and the router comparison.
- **Should:** the hybrid redesign (§5).
- **Could:** F-X2, and documents to SQL recovery.

If 8 Oct slips, the hybrid redesign goes to future work, rather than squeezing the verification.

Process (owner, 2026-09-01): the lead writes the specs and acceptance tests. Bulk code is delegated and
checked by running it. The security gate (F-S2) and the design stay in-house. Paper A's session is told
before any shared code changes. The flags keep its runs reproducible.
