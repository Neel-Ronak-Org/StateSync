# Spec: external evaluation, Part 1. FinanceBench retrieval

Owner: StateSync lead, who audits the result by execution. Implementer: a coding agent.
Branch: `v3-phase3-workspaces`; do not create or switch branches.

Read first:
- `docs/eval/external_datasets.md`: the data, its licence and the facts already checked.
- `docs/eval/retrieval_v2_report.md`: what is being checked out-of-sample.
- `docs/specs/rag_v2.md` section 1 (Rules). They apply here unchanged.
- `scripts/eval_retrieval.py`: the harness to borrow from.

## 0. Question and pre-registered comparisons

The RAG v2 results came from a test set and documents the project made itself. Do they
hold on real company filings, with questions written by someone else?

These comparisons are fixed **now, before any result**. Do not add, drop or reweight any
after seeing numbers.
- **Primary:** configuration 1 (shipped retrieval) against configuration 4 (the RAG v2
  recommendation) on page hit@5 and MRR@10, over the 150 questions.
- **Secondary:** every other pair in the table in §3, with the same metrics plus document
  hit@k.
- **No tuning.** Change no retrieval code, config value, limit or question during this
  work. If something blocks the run, stop, restore the shipped config and report.

## 1. Data (frozen)

- **Questions.** `data/external/financebench/data/financebench_open_source.jsonl`, all 150,
  text used **verbatim**: instructions inside a question stay in. Record its SHA-256, which
  must match `fixtures/external_eval_checksums.txt`.
- **Corpus.** The 84 PDFs in `data/external/financebench/pdfs/`. Run
  `scripts/fetch_external_eval.sh` first; it must PASS.
- **Gold.** For each question, the set of `(doc_name, evidence_page_num + 1)` over its
  evidence entries. The +1 is because FinanceBench pages count from 0 and StateSync's from
  1; `external_datasets.md` gives the evidence for this.

## 2. Harness: `scripts/eval_financebench.py` (new; stdlib only on the host)

Reuse the host/container split and the helpers of `scripts/eval_retrieval.py`: `api`, the
in-container JSON-RPC snippet and the percentile code. Import them or copy them with a
comment, but do not change `eval_retrieval.py`'s behaviour. Use the dev token via
`api_auth.api_token()`, and never print it.

Subcommands:

**`ingest --chunker {paragraph,layout}`**
- Refuse to start unless `[library] chunker` inside `statesync-orchestrator-1` equals
  `--chunker`.
- Create folder `eval-financebench-<chunker>-<timestamp>`.
- Upload the 84 PDFs **one at a time, in sorted order**: upload, then wait until that
  document leaves `processing`, then upload the next. Concurrent ingests would share one
  CPU Ollama and could trip `processing_stale_s`.
- Record per filing: the HTTP status, and any refusal reason (e.g. too many pages, parse
  timeout; record exactly what the API returns). Also record the final status, chunk count
  (from the API or a read-only `SELECT count(*)` as `statesync_reader`), the seconds from
  upload to ready, and the page count.
- Write the record to `docs/eval/financebench_ingest_<chunker>.json`, including the folder
  id, before and after the uploads so a crash leaves it findable.
- Resumable: re-running skips filings already in the folder.

**`query --chunker C --mode {dense,hybrid} --scope {uploads,all} --label L`**
- Create a workspace (`target_schema` `olist`, as the RAG v2 harness does) and link the
  ingest's folder.
- Run the 150 questions through `vector.semantic_search` from inside the orchestrator
  container, with `top_k = 10` and header `X-Workspace-ID`.
  - `--scope uploads` sends `scope: "dataset", dataset: "live"`. `live` has no sample
    documents, so the search covers only the uploaded filings, as on a real user's install,
    which has no demo corpus.
  - `--scope all` sends no scope: the sample demo corpus (20,245 chunks) stays in the search
    as a distractor, which is exactly the shipped behaviour.
  - `--mode` is forwarded as in `eval_retrieval.py`.
- Delete the workspace in a `finally` block. Keep the folder.
- Save `docs/eval/financebench_<label>.json` with the same top-level shape as the RAG v2
  runs: label, created, the questions file SHA-256, params, errors, summary and questions.

**`cleanup --chunker C`**: delete that ingest's folder, and confirm no
`eval-financebench-*` folder or workspace is left.

**`--compare A B`**: a side-by-side table, as in `eval_retrieval.py`.

### Metrics, per question, over the top 10 passages

Map each passage's `document_id` to its filing name through the ingest record.
- **Page hit@k:** a passage in the top k is from a gold filing and its `page` is a gold
  page. This is the primary measure.
- **Page±1 hit@k:** the same, with a page within one of a gold page; tables often continue
  onto the next page. It is reported as secondary and was fixed now.
- **Document hit@k:** a passage in the top k is from the gold filing. Several filings per
  company (up to 5) make this non-trivial.
- **MRR@10** on page hits.
- **Latency** of the call.

Groups:
- overall, over all 150;
- `ingested`: questions whose gold filing was ingested `ready`;
- `refused`: the rest;
- per `question_type`;
- per gold filing `doc_type` (10k, 10q, 8k, Earnings).

A question whose filing was refused counts as a miss in `overall`, because the product
cannot answer it, and is excluded from `ingested`. Report both denominators.

## 3. Runs (pre-registered)

| # | label | chunker | mode | scope |
|---|---|---|---|---|
| 1 | `fb_paragraph_dense` | paragraph | dense | uploads |
| 2 | `fb_paragraph_hybrid` | paragraph | hybrid | uploads |
| 3 | `fb_layout_dense` | layout | dense | uploads |
| 4 | `fb_layout_hybrid` | layout | hybrid | uploads |
| 5 | `fb_paragraph_dense_shipped` | paragraph | dense | all |

`hybrid_dense_lists` stays at `"separate"`. With uploads only there is one non-empty dense
list, so `"merged"` gives the same result; state this in the report rather than running it.

Order of work:
1. Ingest paragraph with the shipped config. Run 1, 2 and 5. Clean up.
2. Set `[library] chunker = "layout"` (edit with Python, not BSD sed), recreate only the
   orchestrator, and confirm `layout` inside the container. Ingest layout. Run 3 and 4.
   Clean up.
3. Restore `chunker = "paragraph"`, recreate the orchestrator, and check that
   `/opt/anaconda3/bin/python3 scripts/shipped_flags.py` exits 0.

Ingest takes hours: about 12,000 pages at ~0.3 s per chunk on CPU. Run long commands
detached (`nohup … > <scratchpad>/fb_ingest_<chunker>.log 2>&1 &`) and check progress
periodically. Never block a single shell call for longer than its timeout.

## 4. Report: `docs/eval/financebench_report.md` (generated from the saved JSON by a script)

Contents:
- The questions-file SHA-256, the source commits and the licence line.
- The ingest table:
  - filings refused and why;
  - filings that failed;
  - total chunks and the time per chunker;
  - any filing where the layout pass fell back to flat pages, if visible from logs or
    data; say how it was detected.
- The results table for the five runs, each with:
  - overall and `ingested` page hit@1/5/10, page±1 hit@5, document hit@5, MRR@10 and
    p50/p90 latency;
  - the per-group tables.
- **The primary comparison, stated plainly, including when it is a loss or a tie.** Put the
  RAG v2 numbers beside it: the in-house set said hit@5 77 → 98. Say whether that held,
  shrank or reversed, and which categories drove it.
- What the sample demo corpus does as a distractor (run 5 against run 1).
- **Product findings:** the six filings over `max_pages = 300`, and any parse timeouts.
  Record them as findings with counts; do not change the limits.
- **Limitations:**
  - 150 questions;
  - one domain;
  - retrieval only, with no answer quality;
  - gold is the annotators' evidence pages, so another page that also answers counts as a
    miss;
  - latency measured on one Mac.

## 5. Completion criteria (report each with trimmed command output)

1. `scripts/fetch_external_eval.sh` PASS, and the questions-file SHA-256 is recorded in every
   saved run.
2. Two ingest records and five runs are saved, with `errors` 0 or each error explained.
3. Cleanup leaves no `eval-financebench-*` folder or workspace (shown by an API listing).
4. `/opt/anaconda3/bin/python3 -m pytest -q tests` passes. Add a small host test for the
   scoring: page and page±1 hits, the +1 page offset and the refused-filing accounting, on
   hand-made inputs with WHY docstrings. `ruff check --select F,B,UP` on new files is clean.
5. `scripts/shipped_flags.py` exits 0, the containers report `chunker = "paragraph"`, and
   `verify_deployed.py` shows only the 2 evaluator failures.
6. No retrieval code, config limit or question was changed. `git status --porcelain` lists
   only:
   - `scripts/eval_financebench.py`
   - the test
   - `docs/eval/financebench_*.json`
   - `docs/eval/financebench_report.md`
   - the report-generator script, if it is kept in `scripts/`
7. No Groq/LLM calls, and the evaluator was not started.
