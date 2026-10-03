# RAG v2 retrieval report

Branch `v3-phase3-workspaces`. Spec: `docs/specs/rag_v2.md`. All runs were made on 2026-10-01 against the frozen test set, through the production MCP search path, with no model calls. Every number below comes from a saved run in `docs/eval/retrieval_v2_<label>.json`; the run is named next to it. The tables were generated from those files by a script, not typed.

## Summary

The numbers are hit@k (is a gold passage in the top k of 10) and MRR@10, over the 100 answerable questions of 110. Hybrid retrieval with the layout chunker is better than shipped retrieval at finding the right passage somewhere in the top 10. It is not better everywhere, and it costs time.

- **Recall.** Gold in the top 10 goes from 79.0% (`final_paragraph_dense`) to 100.0% (`final_layout_hybrid_merged`). Hit@5 goes from 77.0% to 98.0%. The ID questions drive this: hit@10 on `category:id` goes from 30.0% to 100.0%.
- **Rank 1 improved less than recall.** Overall hit@1 goes from 60.0% (`final_paragraph_dense`) to 67.0% (`final_layout_hybrid_merged`), and MRR from 0.670 to 0.803. ID questions still reach rank 1 only 30.0% of the time (`final_layout_hybrid_merged`), against 13.3% before. The cause is a tie in the fusion step (see "Two RRF ties").
- **Paraphrase questions got worse.** Hit@1 on `category:paraphrase` is 60.0% in dense mode (`final_layout_dense`) and 40.0% in hybrid mode (`final_layout_hybrid`); MRR goes from 0.800 to 0.582. D4 and D5 did not fix this (`final_layout_hybrid_merged`: 40.0%). Hit@5 stays at 100.0%, so the gold passage is still found, just lower.
- **Hybrid is slower.** Median latency per search goes from 153 ms (`final_layout_dense`) to 279 ms (`final_layout_hybrid`) and 257 ms (`final_layout_hybrid_merged`); p90 from 183 ms to 430 / 342 ms. One Mac; latency varies by tens of milliseconds between runs, so the 279 against 257 ms gap between those two hybrid runs is not a finding.
- **The layout chunker helps uploads, not the sample corpus.** Upload hit@1 goes from 80.0% (`final_paragraph_dense`) to 92.0% (`final_layout_dense`), MRR 0.887 to 0.960. Sample-corpus numbers are identical, as expected: the sample corpus was not re-chunked.
- **Plain hybrid made some upload questions worse than dense.** Upload hit@1 is 92.0% in dense mode (`final_layout_dense`) and 88.0% in hybrid mode (`final_layout_hybrid`); with dataset scope it is 84.0% (`final_layout_hybrid_scoped`), and `category:table` hit@1 is 75.8% against 84.8% in dense mode. In `final_layout_hybrid`, rv2_087 and rv2_093 lose rank 1 to a sample chunk in a dense-versus-dense RRF tie; in `final_layout_hybrid_scoped`, rv2_076, rv2_078 and rv2_081 do (section 8).
- **D5 (one merged dense list) removes that loss.** With `hybrid_dense_lists = "merged"`, upload hit@1 is 96.0% (`final_layout_hybrid_merged`) and 96.0% with scope (`final_layout_hybrid_scoped_merged`), which is above dense (92.0%). Overall hit@1 is 67.0% in both. No question got worse. Sample-corpus numbers do not move. D5 was made after seeing these results on this same set, so it is post-hoc.
- **Dataset scope did not help retrieval on this set.** Compare `final_layout_hybrid` with `final_layout_hybrid_scoped`: hit@1 65.0% against 64.0%, hit@5 98.0% against 97.0%, MRR 0.791 against 0.788. Paraphrase hit@5 drops from 100.0% to 90.0%. With the merged list, scope is neutral on hit@1 and MRR (`final_layout_hybrid_merged` against `final_layout_hybrid_scoped_merged`) and still costs that paraphrase hit@5.
- **Abstention is weak.** At the fitted thresholds it catches 1 of 10 unanswerable questions and loses 0 of 100 answerable ones. The thresholds were chosen on this set, so even that is an in-sample figure.
- **The shipped configuration reproduces the baseline.** `final_paragraph_dense` matches `baseline` on first-hit rank, passage text and scores for all 110 questions (see "Reproduction checks").

Hybrid changes which passages the answer model sees. Whether answers get better is not measured here. It needs model calls.

## 1. The test set

`fixtures/golden/retrieval_v2.json`, SHA-256 `652f11dd2309a75267c8be35f45d597036c002c7398b3a04aff6513eef6cf46c`.

110 questions: 100 answerable and 10 unanswerable. Gold is evidence text in a gold document, not a chunk id, so it survives re-chunking. A question is a hit at k when one of the top k passages is from the gold document and contains an evidence string (lower-cased, whitespace collapsed). Questions per group:

| group | questions | answerable |
|---|---:|---:|
| overall | 110 | 100 |
| corpus:sample | 80 | 75 |
| corpus:upload | 30 | 25 |
| category:cross_section | 4 | 4 |
| category:definition | 7 | 7 |
| category:fact | 16 | 16 |
| category:id | 30 | 30 |
| category:paraphrase | 10 | 10 |
| category:table | 33 | 33 |
| category:unanswerable | 10 | 0 |

**Label fixes: none.** The file has one commit (`28bdabe`, Phase A). Its hash has not changed, and every saved run in this report records the same hash. No question or gold was edited after a result was seen.

## 2. The runs

Six fresh `final_` runs were made on the final code, plus the Phase A `baseline`. Older saved runs are used for history and for the reproduction checks. `chunker` is `[library] chunker`; `dense lists` is `[retrieval] hybrid_dense_lists`.

| run | chunker | mode | scope | dense lists | created (UTC) | note |
|---|---|---|---|---|---|---|
| `baseline` | paragraph | dense | all | n/a | 2026-10-01 10:41:44Z | Phase A, today's code before any RAG v2 change |
| `final_paragraph_dense` | paragraph | dense | all | n/a | 2026-10-01 17:14:41Z | shipped configuration, fresh run on the final code |
| `final_layout_dense` | layout | dense | all | n/a | 2026-10-01 17:15:20Z |  |
| `final_layout_hybrid` | layout | hybrid | all | separate | 2026-10-01 17:16:49Z |  |
| `final_layout_hybrid_scoped` | layout | hybrid | dataset | separate | 2026-10-01 17:17:18Z |  |
| `final_layout_hybrid_merged` | layout | hybrid | all | merged | 2026-10-01 17:18:04Z | D5 |
| `final_layout_hybrid_scoped_merged` | layout | hybrid | dataset | merged | 2026-10-01 17:18:32Z | D5 |
| `paragraph_hybrid` | paragraph | hybrid | all | separate | 2026-10-01 11:16:18Z | Phase D, before D4 |
| `paragraph_hybrid_d4` | paragraph | hybrid | all | separate | 2026-10-01 16:49:12Z | Phase D + D4 |
| `layout_dense` | layout | dense | all | n/a | 2026-10-01 10:57:36Z | Phase C |
| `layout_dense_d4` | layout | dense | all | n/a | 2026-10-01 16:50:25Z | after D4 (dense mode is unchanged by D4) |
| `layout_hybrid` | layout | hybrid | all | separate | 2026-10-01 11:19:55Z | Phase D, before D4 |
| `layout_hybrid_d4` | layout | hybrid | all | separate | 2026-10-01 16:50:05Z | Phase D + D4 |
| `layout_dense_scoped` | layout | dense | dataset | n/a | 2026-10-01 17:05:12Z | Phase E |
| `layout_hybrid_scoped` | layout | hybrid | dataset | separate | 2026-10-01 17:04:54Z | Phase E; the abstention thresholds were fitted on this run |

`baseline` was made before the harness recorded per-leg scores, so it has no `best_dense_score`. Its passages and ranks are what the comparison uses.

Between `baseline` and `final_layout_*` two things change at once for upload questions: the chunker and, in hybrid runs, the retrieval mode. Read the pairs in section 5 to separate them.

## 3. Headline table

Overall, over all questions for latency and over the answerable ones for hit@k and MRR. Latency is the time of one `vector.semantic_search` call, in ms.

| run | hit@1 | hit@5 | hit@10 | MRR@10 | p50 ms | p90 ms |
|---|---:|---:|---:|---:|---:|---:|
| `baseline` | 60.0 | 77.0 | 79.0 | 0.670 | 145 | 179 |
| `final_paragraph_dense` | 60.0 | 77.0 | 79.0 | 0.670 | 149 | 172 |
| `final_layout_dense` | 63.0 | 77.0 | 79.0 | 0.688 | 153 | 183 |
| `final_layout_hybrid` | 65.0 | 98.0 | 100.0 | 0.791 | 279 | 430 |
| `final_layout_hybrid_scoped` | 64.0 | 97.0 | 100.0 | 0.788 | 236 | 312 |
| `final_layout_hybrid_merged` | 67.0 | 98.0 | 100.0 | 0.803 | 257 | 342 |
| `final_layout_hybrid_scoped_merged` | 67.0 | 97.0 | 100.0 | 0.803 | 230 | 298 |

**hit@1 by group**

| group | `baseline` | `f_paragraph_dense` | `f_layout_dense` | `f_layout_hybrid` | `f_layout_hybrid_scoped` | `f_layout_hybrid_merged` | `f_layout_hybrid_scoped_merged` |
|---|---:|---:|---:|---:|---:|---:|---:|
| overall | 60.0 | 60.0 | 63.0 | 65.0 | 64.0 | 67.0 | 67.0 |
| corpus:sample | 53.3 | 53.3 | 53.3 | 57.3 | 57.3 | 57.3 | 57.3 |
| corpus:upload | 80.0 | 80.0 | 92.0 | 88.0 | 84.0 | 96.0 | 96.0 |
| category:cross_section | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 |
| category:definition | 71.4 | 71.4 | 85.7 | 71.4 | 85.7 | 85.7 | 85.7 |
| category:fact | 81.2 | 81.2 | 93.8 | 93.8 | 100.0 | 100.0 | 100.0 |
| category:id | 13.3 | 13.3 | 13.3 | 30.0 | 30.0 | 30.0 | 30.0 |
| category:paraphrase | 60.0 | 60.0 | 60.0 | 40.0 | 40.0 | 40.0 | 40.0 |
| category:table | 84.8 | 84.8 | 84.8 | 84.8 | 75.8 | 84.8 | 84.8 |

**hit@5 by group**

| group | `baseline` | `f_paragraph_dense` | `f_layout_dense` | `f_layout_hybrid` | `f_layout_hybrid_scoped` | `f_layout_hybrid_merged` | `f_layout_hybrid_scoped_merged` |
|---|---:|---:|---:|---:|---:|---:|---:|
| overall | 77.0 | 77.0 | 77.0 | 98.0 | 97.0 | 98.0 | 97.0 |
| corpus:sample | 69.3 | 69.3 | 69.3 | 97.3 | 96.0 | 97.3 | 96.0 |
| corpus:upload | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 |
| category:cross_section | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 |
| category:definition | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 |
| category:fact | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 |
| category:id | 30.0 | 30.0 | 30.0 | 96.7 | 96.7 | 96.7 | 96.7 |
| category:paraphrase | 100.0 | 100.0 | 100.0 | 100.0 | 90.0 | 100.0 | 90.0 |
| category:table | 93.9 | 93.9 | 93.9 | 97.0 | 97.0 | 97.0 | 97.0 |

**hit@10 by group**

| group | `baseline` | `f_paragraph_dense` | `f_layout_dense` | `f_layout_hybrid` | `f_layout_hybrid_scoped` | `f_layout_hybrid_merged` | `f_layout_hybrid_scoped_merged` |
|---|---:|---:|---:|---:|---:|---:|---:|
| overall | 79.0 | 79.0 | 79.0 | 100.0 | 100.0 | 100.0 | 100.0 |
| corpus:sample | 72.0 | 72.0 | 72.0 | 100.0 | 100.0 | 100.0 | 100.0 |
| corpus:upload | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 |
| category:cross_section | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 |
| category:definition | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 |
| category:fact | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 |
| category:id | 30.0 | 30.0 | 30.0 | 100.0 | 100.0 | 100.0 | 100.0 |
| category:paraphrase | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 |
| category:table | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 | 100.0 |

**MRR@10 by group**

| group | `baseline` | `f_paragraph_dense` | `f_layout_dense` | `f_layout_hybrid` | `f_layout_hybrid_scoped` | `f_layout_hybrid_merged` | `f_layout_hybrid_scoped_merged` |
|---|---:|---:|---:|---:|---:|---:|---:|
| overall | 0.670 | 0.670 | 0.688 | 0.791 | 0.788 | 0.803 | 0.803 |
| corpus:sample | 0.597 | 0.597 | 0.597 | 0.743 | 0.744 | 0.743 | 0.744 |
| corpus:upload | 0.887 | 0.887 | 0.960 | 0.933 | 0.920 | 0.980 | 0.980 |
| category:cross_section | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| category:definition | 0.857 | 0.857 | 0.929 | 0.833 | 0.929 | 0.929 | 0.929 |
| category:fact | 0.885 | 0.885 | 0.969 | 0.969 | 1.000 | 1.000 | 1.000 |
| category:id | 0.183 | 0.183 | 0.183 | 0.611 | 0.615 | 0.611 | 0.615 |
| category:paraphrase | 0.800 | 0.800 | 0.800 | 0.582 | 0.577 | 0.582 | 0.577 |
| category:table | 0.889 | 0.889 | 0.889 | 0.897 | 0.851 | 0.897 | 0.897 |

(`f_` stands for `final_`.)

**Earlier saved runs (history; same test set)**

| run | hit@1 | hit@5 | hit@10 | MRR@10 | p50 ms | p90 ms |
|---|---:|---:|---:|---:|---:|---:|
| `paragraph_hybrid` | 62.0 | 81.0 | 84.0 | 0.689 | 607 | 850 |
| `paragraph_hybrid_d4` | 65.0 | 98.0 | 100.0 | 0.793 | 294 | 521 |
| `layout_dense` | 63.0 | 77.0 | 79.0 | 0.688 | 147 | 176 |
| `layout_dense_d4` | 63.0 | 77.0 | 79.0 | 0.688 | 148 | 178 |
| `layout_hybrid` | 62.0 | 81.0 | 84.0 | 0.687 | 657 | 895 |
| `layout_hybrid_d4` | 65.0 | 98.0 | 100.0 | 0.791 | 274 | 383 |
| `layout_dense_scoped` | 64.0 | 77.0 | 79.0 | 0.693 | 101 | 167 |
| `layout_hybrid_scoped` | 64.0 | 97.0 | 100.0 | 0.788 | 234 | 512 |

## 4. Per-run tables

One table per run: overall, per corpus and per category. `ans` is the number of answerable questions. Latency covers all questions in the group.

### `baseline`

chunker `paragraph`, mode `dense`, scope `all`, dense lists `n/a`. File `docs/eval/retrieval_v2_baseline.json`.

| group | n | ans | hit@1 | hit@5 | hit@10 | MRR@10 | p50 ms | p90 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| overall | 110 | 100 | 60.0 | 77.0 | 79.0 | 0.670 | 145 | 179 |
| corpus:sample | 80 | 75 | 53.3 | 69.3 | 72.0 | 0.597 | 146 | 181 |
| corpus:upload | 30 | 25 | 80.0 | 100.0 | 100.0 | 0.887 | 137 | 168 |
| category:cross_section | 4 | 4 | 100.0 | 100.0 | 100.0 | 1.000 | 160 | 166 |
| category:definition | 7 | 7 | 71.4 | 100.0 | 100.0 | 0.857 | 134 | 259 |
| category:fact | 16 | 16 | 81.2 | 100.0 | 100.0 | 0.885 | 135 | 145 |
| category:id | 30 | 30 | 13.3 | 30.0 | 30.0 | 0.183 | 160 | 183 |
| category:paraphrase | 10 | 10 | 60.0 | 100.0 | 100.0 | 0.800 | 141 | 220 |
| category:table | 33 | 33 | 84.8 | 93.9 | 100.0 | 0.889 | 145 | 169 |
| category:unanswerable | 10 | 0 | n/a | n/a | n/a | n/a | 145 | 183 |

### `final_paragraph_dense`

chunker `paragraph`, mode `dense`, scope `all`, dense lists `n/a`. File `docs/eval/retrieval_v2_final_paragraph_dense.json`.

| group | n | ans | hit@1 | hit@5 | hit@10 | MRR@10 | p50 ms | p90 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| overall | 110 | 100 | 60.0 | 77.0 | 79.0 | 0.670 | 149 | 172 |
| corpus:sample | 80 | 75 | 53.3 | 69.3 | 72.0 | 0.597 | 151 | 174 |
| corpus:upload | 30 | 25 | 80.0 | 100.0 | 100.0 | 0.887 | 144 | 171 |
| category:cross_section | 4 | 4 | 100.0 | 100.0 | 100.0 | 1.000 | 150 | 166 |
| category:definition | 7 | 7 | 71.4 | 100.0 | 100.0 | 0.857 | 151 | 162 |
| category:fact | 16 | 16 | 81.2 | 100.0 | 100.0 | 0.885 | 144 | 157 |
| category:id | 30 | 30 | 13.3 | 30.0 | 30.0 | 0.183 | 155 | 176 |
| category:paraphrase | 10 | 10 | 60.0 | 100.0 | 100.0 | 0.800 | 149 | 178 |
| category:table | 33 | 33 | 84.8 | 93.9 | 100.0 | 0.889 | 151 | 170 |
| category:unanswerable | 10 | 0 | n/a | n/a | n/a | n/a | 138 | 319 |

### `final_layout_dense`

chunker `layout`, mode `dense`, scope `all`, dense lists `n/a`. File `docs/eval/retrieval_v2_final_layout_dense.json`.

| group | n | ans | hit@1 | hit@5 | hit@10 | MRR@10 | p50 ms | p90 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| overall | 110 | 100 | 63.0 | 77.0 | 79.0 | 0.688 | 153 | 183 |
| corpus:sample | 80 | 75 | 53.3 | 69.3 | 72.0 | 0.597 | 158 | 184 |
| corpus:upload | 30 | 25 | 92.0 | 100.0 | 100.0 | 0.960 | 144 | 174 |
| category:cross_section | 4 | 4 | 100.0 | 100.0 | 100.0 | 1.000 | 144 | 172 |
| category:definition | 7 | 7 | 85.7 | 100.0 | 100.0 | 0.929 | 136 | 149 |
| category:fact | 16 | 16 | 93.8 | 100.0 | 100.0 | 0.969 | 153 | 172 |
| category:id | 30 | 30 | 13.3 | 30.0 | 30.0 | 0.183 | 163 | 303 |
| category:paraphrase | 10 | 10 | 60.0 | 100.0 | 100.0 | 0.800 | 153 | 170 |
| category:table | 33 | 33 | 84.8 | 93.9 | 100.0 | 0.889 | 154 | 188 |
| category:unanswerable | 10 | 0 | n/a | n/a | n/a | n/a | 146 | 165 |

### `final_layout_hybrid`

chunker `layout`, mode `hybrid`, scope `all`, dense lists `separate`. File `docs/eval/retrieval_v2_final_layout_hybrid.json`.

| group | n | ans | hit@1 | hit@5 | hit@10 | MRR@10 | p50 ms | p90 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| overall | 110 | 100 | 65.0 | 98.0 | 100.0 | 0.791 | 279 | 430 |
| corpus:sample | 80 | 75 | 57.3 | 97.3 | 100.0 | 0.743 | 285 | 411 |
| corpus:upload | 30 | 25 | 88.0 | 100.0 | 100.0 | 0.933 | 261 | 465 |
| category:cross_section | 4 | 4 | 100.0 | 100.0 | 100.0 | 1.000 | 449 | 658 |
| category:definition | 7 | 7 | 71.4 | 100.0 | 100.0 | 0.833 | 193 | 204 |
| category:fact | 16 | 16 | 93.8 | 100.0 | 100.0 | 0.969 | 288 | 425 |
| category:id | 30 | 30 | 30.0 | 96.7 | 100.0 | 0.611 | 251 | 417 |
| category:paraphrase | 10 | 10 | 40.0 | 100.0 | 100.0 | 0.582 | 321 | 349 |
| category:table | 33 | 33 | 84.8 | 97.0 | 100.0 | 0.897 | 301 | 349 |
| category:unanswerable | 10 | 0 | n/a | n/a | n/a | n/a | 256 | 344 |

### `final_layout_hybrid_scoped`

chunker `layout`, mode `hybrid`, scope `dataset`, dense lists `separate`. File `docs/eval/retrieval_v2_final_layout_hybrid_scoped.json`.

| group | n | ans | hit@1 | hit@5 | hit@10 | MRR@10 | p50 ms | p90 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| overall | 110 | 100 | 64.0 | 97.0 | 100.0 | 0.788 | 236 | 312 |
| corpus:sample | 80 | 75 | 57.3 | 96.0 | 100.0 | 0.744 | 233 | 295 |
| corpus:upload | 30 | 25 | 84.0 | 100.0 | 100.0 | 0.920 | 259 | 365 |
| category:cross_section | 4 | 4 | 100.0 | 100.0 | 100.0 | 1.000 | 354 | 450 |
| category:definition | 7 | 7 | 85.7 | 100.0 | 100.0 | 0.929 | 201 | 218 |
| category:fact | 16 | 16 | 100.0 | 100.0 | 100.0 | 1.000 | 233 | 304 |
| category:id | 30 | 30 | 30.0 | 96.7 | 100.0 | 0.615 | 228 | 291 |
| category:paraphrase | 10 | 10 | 40.0 | 90.0 | 100.0 | 0.577 | 251 | 331 |
| category:table | 33 | 33 | 75.8 | 97.0 | 100.0 | 0.851 | 242 | 305 |
| category:unanswerable | 10 | 0 | n/a | n/a | n/a | n/a | 202 | 249 |

### `final_layout_hybrid_merged`

chunker `layout`, mode `hybrid`, scope `all`, dense lists `merged`. File `docs/eval/retrieval_v2_final_layout_hybrid_merged.json`.

| group | n | ans | hit@1 | hit@5 | hit@10 | MRR@10 | p50 ms | p90 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| overall | 110 | 100 | 67.0 | 98.0 | 100.0 | 0.803 | 257 | 342 |
| corpus:sample | 80 | 75 | 57.3 | 97.3 | 100.0 | 0.743 | 264 | 338 |
| corpus:upload | 30 | 25 | 96.0 | 100.0 | 100.0 | 0.980 | 244 | 348 |
| category:cross_section | 4 | 4 | 100.0 | 100.0 | 100.0 | 1.000 | 329 | 349 |
| category:definition | 7 | 7 | 85.7 | 100.0 | 100.0 | 0.929 | 195 | 221 |
| category:fact | 16 | 16 | 100.0 | 100.0 | 100.0 | 1.000 | 277 | 417 |
| category:id | 30 | 30 | 30.0 | 96.7 | 100.0 | 0.611 | 247 | 271 |
| category:paraphrase | 10 | 10 | 40.0 | 100.0 | 100.0 | 0.582 | 316 | 339 |
| category:table | 33 | 33 | 84.8 | 97.0 | 100.0 | 0.897 | 282 | 338 |
| category:unanswerable | 10 | 0 | n/a | n/a | n/a | n/a | 240 | 251 |

### `final_layout_hybrid_scoped_merged`

chunker `layout`, mode `hybrid`, scope `dataset`, dense lists `merged`. File `docs/eval/retrieval_v2_final_layout_hybrid_scoped_merged.json`.

| group | n | ans | hit@1 | hit@5 | hit@10 | MRR@10 | p50 ms | p90 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| overall | 110 | 100 | 67.0 | 97.0 | 100.0 | 0.803 | 230 | 298 |
| corpus:sample | 80 | 75 | 57.3 | 96.0 | 100.0 | 0.744 | 223 | 266 |
| corpus:upload | 30 | 25 | 96.0 | 100.0 | 100.0 | 0.980 | 259 | 333 |
| category:cross_section | 4 | 4 | 100.0 | 100.0 | 100.0 | 1.000 | 329 | 362 |
| category:definition | 7 | 7 | 85.7 | 100.0 | 100.0 | 0.929 | 195 | 220 |
| category:fact | 16 | 16 | 100.0 | 100.0 | 100.0 | 1.000 | 214 | 308 |
| category:id | 30 | 30 | 30.0 | 96.7 | 100.0 | 0.615 | 232 | 263 |
| category:paraphrase | 10 | 10 | 40.0 | 90.0 | 100.0 | 0.577 | 232 | 261 |
| category:table | 33 | 33 | 84.8 | 97.0 | 100.0 | 0.897 | 250 | 296 |
| category:unanswerable | 10 | 0 | n/a | n/a | n/a | n/a | 213 | 266 |

## 5. Comparisons

Output of `scripts/eval_retrieval.py --compare A B`, unedited. Each cell is `A -> B`. The `top-1 median` lines compare a cosine score with an RRF score when one run is dense and the other hybrid; those two scales are not comparable.

### `baseline` against `final_layout_hybrid`

Everything at once: shipped retrieval against layout chunker plus hybrid.

```
A = baseline   B = final_layout_hybrid
group                     ans    hit@1 A -> B    hit@5 A -> B   hit@10 A -> B   MRR@10 A -> B   p50 ms A -> B
overall                   100   60.0 -> 65.0    77.0 -> 98.0    79.0 -> 100.0  0.670 -> 0.791    145 -> 279  
corpus:sample              75   53.3 -> 57.3    69.3 -> 97.3    72.0 -> 100.0  0.597 -> 0.743    146 -> 285  
corpus:upload              25   80.0 -> 88.0   100.0 -> 100.0  100.0 -> 100.0  0.887 -> 0.933    137 -> 261  
category:cross_section      4  100.0 -> 100.0  100.0 -> 100.0  100.0 -> 100.0  1.000 -> 1.000    160 -> 449  
category:definition         7   71.4 -> 71.4   100.0 -> 100.0  100.0 -> 100.0  0.857 -> 0.833    134 -> 193  
category:fact              16   81.2 -> 93.8   100.0 -> 100.0  100.0 -> 100.0  0.885 -> 0.969    135 -> 288  
category:id                30   13.3 -> 30.0    30.0 -> 96.7    30.0 -> 100.0  0.183 -> 0.611    160 -> 251  
category:paraphrase        10   60.0 -> 40.0   100.0 -> 100.0  100.0 -> 100.0  0.800 -> 0.582    141 -> 321  
category:table             33   84.8 -> 84.8    93.9 -> 97.0   100.0 -> 100.0  0.889 -> 0.897    145 -> 301  
category:unanswerable       0    n/a -> n/a      n/a -> n/a      n/a -> n/a      n/a -> n/a      145 -> 256  
unanswerable top-1 median: 0.648 -> 0.033
unanswerable best dense score median: n/a -> 0.679
unanswerable best keyword score median: n/a -> 20.521
answerable top-1 median: 0.739 -> 0.033
answerable best dense score median: n/a -> 0.755
answerable best keyword score median: n/a -> 29.227
```

By question (first-hit rank, `-` = not in the top 10): 29 improved, 8 got worse, 63 unchanged.

Got worse:

| question | corpus | category | first-hit rank before | after |
|---|---|---|---:|---:|
| rv2_057 | sample | paraphrase | 1 | 4 |
| rv2_058 | sample | paraphrase | 2 | 3 |
| rv2_059 | sample | paraphrase | 2 | 3 |
| rv2_063 | sample | paraphrase | 2 | 5 |
| rv2_064 | sample | paraphrase | 1 | 5 |
| rv2_074 | sample | id | 4 | 5 |
| rv2_084 | upload | definition | 1 | 3 |
| rv2_093 | upload | fact | 1 | 2 |

Improved, by corpus and category: sample/id 22; sample/table 3; upload/definition 1; upload/fact 3.

### `final_layout_dense` against `final_layout_hybrid`

Hybrid against dense, same chunker. This isolates the retrieval mode.

```
A = final_layout_dense   B = final_layout_hybrid
group                     ans    hit@1 A -> B    hit@5 A -> B   hit@10 A -> B   MRR@10 A -> B   p50 ms A -> B
overall                   100   63.0 -> 65.0    77.0 -> 98.0    79.0 -> 100.0  0.688 -> 0.791    153 -> 279  
corpus:sample              75   53.3 -> 57.3    69.3 -> 97.3    72.0 -> 100.0  0.597 -> 0.743    158 -> 285  
corpus:upload              25   92.0 -> 88.0   100.0 -> 100.0  100.0 -> 100.0  0.960 -> 0.933    144 -> 261  
category:cross_section      4  100.0 -> 100.0  100.0 -> 100.0  100.0 -> 100.0  1.000 -> 1.000    144 -> 449  
category:definition         7   85.7 -> 71.4   100.0 -> 100.0  100.0 -> 100.0  0.929 -> 0.833    136 -> 193  
category:fact              16   93.8 -> 93.8   100.0 -> 100.0  100.0 -> 100.0  0.969 -> 0.969    153 -> 288  
category:id                30   13.3 -> 30.0    30.0 -> 96.7    30.0 -> 100.0  0.183 -> 0.611    163 -> 251  
category:paraphrase        10   60.0 -> 40.0   100.0 -> 100.0  100.0 -> 100.0  0.800 -> 0.582    153 -> 321  
category:table             33   84.8 -> 84.8    93.9 -> 97.0   100.0 -> 100.0  0.889 -> 0.897    154 -> 301  
category:unanswerable       0    n/a -> n/a      n/a -> n/a      n/a -> n/a      n/a -> n/a      146 -> 256  
unanswerable top-1 median: 0.679 -> 0.033
unanswerable best dense score median: 0.679 -> 0.679
unanswerable best keyword score median: n/a -> 20.521
answerable top-1 median: 0.755 -> 0.033
answerable best dense score median: 0.755 -> 0.755
answerable best keyword score median: n/a -> 29.227
```

By question (first-hit rank, `-` = not in the top 10): 26 improved, 9 got worse, 65 unchanged.

Got worse:

| question | corpus | category | first-hit rank before | after |
|---|---|---|---:|---:|
| rv2_057 | sample | paraphrase | 1 | 4 |
| rv2_058 | sample | paraphrase | 2 | 3 |
| rv2_059 | sample | paraphrase | 2 | 3 |
| rv2_063 | sample | paraphrase | 2 | 5 |
| rv2_064 | sample | paraphrase | 1 | 5 |
| rv2_074 | sample | id | 4 | 5 |
| rv2_084 | upload | definition | 2 | 3 |
| rv2_087 | upload | definition | 1 | 2 |
| rv2_093 | upload | fact | 1 | 2 |

Improved, by corpus and category: sample/id 22; sample/table 3; upload/fact 1.

### `final_layout_hybrid` against `final_layout_hybrid_merged`

D5 without scope.

```
A = final_layout_hybrid   B = final_layout_hybrid_merged
group                     ans    hit@1 A -> B    hit@5 A -> B   hit@10 A -> B   MRR@10 A -> B   p50 ms A -> B
overall                   100   65.0 -> 67.0    98.0 -> 98.0   100.0 -> 100.0  0.791 -> 0.803    279 -> 257  
corpus:sample              75   57.3 -> 57.3    97.3 -> 97.3   100.0 -> 100.0  0.743 -> 0.743    285 -> 264  
corpus:upload              25   88.0 -> 96.0   100.0 -> 100.0  100.0 -> 100.0  0.933 -> 0.980    261 -> 244  
category:cross_section      4  100.0 -> 100.0  100.0 -> 100.0  100.0 -> 100.0  1.000 -> 1.000    449 -> 329  
category:definition         7   71.4 -> 85.7   100.0 -> 100.0  100.0 -> 100.0  0.833 -> 0.929    193 -> 195  
category:fact              16   93.8 -> 100.0  100.0 -> 100.0  100.0 -> 100.0  0.969 -> 1.000    288 -> 277  
category:id                30   30.0 -> 30.0    96.7 -> 96.7   100.0 -> 100.0  0.611 -> 0.611    251 -> 247  
category:paraphrase        10   40.0 -> 40.0   100.0 -> 100.0  100.0 -> 100.0  0.582 -> 0.582    321 -> 316  
category:table             33   84.8 -> 84.8    97.0 -> 97.0   100.0 -> 100.0  0.897 -> 0.897    301 -> 282  
category:unanswerable       0    n/a -> n/a      n/a -> n/a      n/a -> n/a      n/a -> n/a      256 -> 240  
unanswerable top-1 median: 0.033 -> 0.033
unanswerable best dense score median: 0.679 -> 0.679
unanswerable best keyword score median: 20.521 -> 20.521
answerable top-1 median: 0.033 -> 0.033
answerable best dense score median: 0.755 -> 0.755
answerable best keyword score median: 29.227 -> 29.227
```

By question (first-hit rank, `-` = not in the top 10): 3 improved, 0 got worse, 97 unchanged.

Improved:

| question | corpus | category | first-hit rank before | after |
|---|---|---|---:|---:|
| rv2_084 | upload | definition | 3 | 2 |
| rv2_087 | upload | definition | 2 | 1 |
| rv2_093 | upload | fact | 2 | 1 |

### `final_layout_hybrid_scoped` against `final_layout_hybrid_scoped_merged`

D5 with scope.

```
A = final_layout_hybrid_scoped   B = final_layout_hybrid_scoped_merged
group                     ans    hit@1 A -> B    hit@5 A -> B   hit@10 A -> B   MRR@10 A -> B   p50 ms A -> B
overall                   100   64.0 -> 67.0    97.0 -> 97.0   100.0 -> 100.0  0.788 -> 0.803    236 -> 230  
corpus:sample              75   57.3 -> 57.3    96.0 -> 96.0   100.0 -> 100.0  0.744 -> 0.744    233 -> 223  
corpus:upload              25   84.0 -> 96.0   100.0 -> 100.0  100.0 -> 100.0  0.920 -> 0.980    259 -> 259  
category:cross_section      4  100.0 -> 100.0  100.0 -> 100.0  100.0 -> 100.0  1.000 -> 1.000    354 -> 329  
category:definition         7   85.7 -> 85.7   100.0 -> 100.0  100.0 -> 100.0  0.929 -> 0.929    201 -> 195  
category:fact              16  100.0 -> 100.0  100.0 -> 100.0  100.0 -> 100.0  1.000 -> 1.000    233 -> 214  
category:id                30   30.0 -> 30.0    96.7 -> 96.7   100.0 -> 100.0  0.615 -> 0.615    228 -> 232  
category:paraphrase        10   40.0 -> 40.0    90.0 -> 90.0   100.0 -> 100.0  0.577 -> 0.577    251 -> 232  
category:table             33   75.8 -> 84.8    97.0 -> 97.0   100.0 -> 100.0  0.851 -> 0.897    242 -> 250  
category:unanswerable       0    n/a -> n/a      n/a -> n/a      n/a -> n/a      n/a -> n/a      202 -> 213  
unanswerable top-1 median: 0.033 -> 0.033
unanswerable best dense score median: 0.679 -> 0.679
unanswerable best keyword score median: 15.051 -> 15.051
answerable top-1 median: 0.033 -> 0.033
answerable best dense score median: 0.755 -> 0.755
answerable best keyword score median: 18.903 -> 18.903
```

By question (first-hit rank, `-` = not in the top 10): 3 improved, 0 got worse, 97 unchanged.

Improved:

| question | corpus | category | first-hit rank before | after |
|---|---|---|---:|---:|
| rv2_076 | upload | table | 2 | 1 |
| rv2_078 | upload | table | 2 | 1 |
| rv2_081 | upload | table | 2 | 1 |

## 6. What D5 (merged dense list) did

`[retrieval] hybrid_dense_lists = "merged"` sorts the sample and upload dense candidates together by cosine and gives RRF one dense list. The BM25 lists stay separate. It issues the same SQL; only the order of rows in Python differs.

| pair | group | hit@1 | hit@5 | MRR@10 |
|---|---|---:|---:|---:|
| `final_layout_hybrid` -> `final_layout_hybrid_merged` | overall | 65.0 -> 67.0 | 98.0 -> 98.0 | 0.791 -> 0.803 |
| `final_layout_hybrid` -> `final_layout_hybrid_merged` | corpus:sample | 57.3 -> 57.3 | 97.3 -> 97.3 | 0.743 -> 0.743 |
| `final_layout_hybrid` -> `final_layout_hybrid_merged` | corpus:upload | 88.0 -> 96.0 | 100.0 -> 100.0 | 0.933 -> 0.980 |
| `final_layout_hybrid` -> `final_layout_hybrid_merged` | category:table | 84.8 -> 84.8 | 97.0 -> 97.0 | 0.897 -> 0.897 |
| `final_layout_hybrid` -> `final_layout_hybrid_merged` | category:definition | 71.4 -> 85.7 | 100.0 -> 100.0 | 0.833 -> 0.929 |
| `final_layout_hybrid` -> `final_layout_hybrid_merged` | category:fact | 93.8 -> 100.0 | 100.0 -> 100.0 | 0.969 -> 1.000 |
| `final_layout_hybrid_scoped` -> `final_layout_hybrid_scoped_merged` | overall | 64.0 -> 67.0 | 97.0 -> 97.0 | 0.788 -> 0.803 |
| `final_layout_hybrid_scoped` -> `final_layout_hybrid_scoped_merged` | corpus:sample | 57.3 -> 57.3 | 96.0 -> 96.0 | 0.744 -> 0.744 |
| `final_layout_hybrid_scoped` -> `final_layout_hybrid_scoped_merged` | corpus:upload | 84.0 -> 96.0 | 100.0 -> 100.0 | 0.920 -> 0.980 |
| `final_layout_hybrid_scoped` -> `final_layout_hybrid_scoped_merged` | category:table | 75.8 -> 84.8 | 97.0 -> 97.0 | 0.851 -> 0.897 |
| `final_layout_hybrid_scoped` -> `final_layout_hybrid_scoped_merged` | category:definition | 85.7 -> 85.7 | 100.0 -> 100.0 | 0.929 -> 0.929 |
| `final_layout_hybrid_scoped` -> `final_layout_hybrid_scoped_merged` | category:fact | 100.0 -> 100.0 | 100.0 -> 100.0 | 1.000 -> 1.000 |

`final_layout_hybrid` to `final_layout_hybrid_merged`: 3 questions improved, 0 got worse, 97 unchanged. Questions that changed rank:

| question | corpus | category | first-hit rank before | after |
|---|---|---|---:|---:|
| rv2_084 | upload | definition | 3 | 2 |
| rv2_087 | upload | definition | 2 | 1 |
| rv2_093 | upload | fact | 2 | 1 |

`final_layout_hybrid_scoped` to `final_layout_hybrid_scoped_merged`: 3 questions improved, 0 got worse, 97 unchanged. Questions that changed rank:

| question | corpus | category | first-hit rank before | after |
|---|---|---|---:|---:|
| rv2_076 | upload | table | 2 | 1 |
| rv2_078 | upload | table | 2 | 1 |
| rv2_081 | upload | table | 2 | 1 |

Does scope plus merge recover the upload loss? The upload questions that scope made worse (`final_layout_hybrid` to `final_layout_hybrid_scoped`) are rv2_076, rv2_078, rv2_081. Their ranks in `final_layout_hybrid_scoped` and `final_layout_hybrid_scoped_merged`:

| question | category | dense | hybrid | hybrid scoped | hybrid merged | scoped merged |
|---|---|---:|---:|---:|---:|---:|
| rv2_076 | table | 1 | 1 | 2 | 1 | 1 |
| rv2_078 | table | 1 | 1 | 2 | 1 | 1 |
| rv2_081 | table | 1 | 1 | 2 | 1 | 1 |

`category:table` hit@1 (sample and upload questions together): 84.8% dense (`final_layout_dense`), 84.8% hybrid, 75.8% hybrid scoped, 84.8% merged, 84.8% scoped and merged. So scope plus merge recovers the table loss: 84.8% is the dense value, not above it.

## 7. Seller-ID questions

The 20 questions `rv2_016` to `rv2_035` ask "What do customers say about seller <32-hex id>?". The gold is that seller's review digest in the sample corpus. Cells are the rank of the first hit in the top 10; `-` = not found in the top 10. Sample documents do not depend on the chunker, so the paragraph and layout runs are comparable here.

| question (seller id) | baseline (dense) | Phase D (`paragraph_hybrid`) | D4 (`paragraph_hybrid_d4`) | `final_layout_hybrid` | `final_layout_hybrid_merged` |
|---|---:|---:|---:|---:|---:|
| rv2_016 (02dcd3e8...) | - | - | 2 | 2 | 2 |
| rv2_017 (06579cb2...) | - | - | 2 | 2 | 2 |
| rv2_018 (1835b56c...) | - | 1 | 1 | 1 | 1 |
| rv2_019 (3969863b...) | - | - | 2 | 2 | 2 |
| rv2_020 (3f3486b6...) | - | - | 2 | 2 | 2 |
| rv2_021 (4c854574...) | - | 6 | 1 | 1 | 1 |
| rv2_022 (6e4f5ff6...) | - | - | 2 | 2 | 2 |
| rv2_023 (75fdd0a7...) | - | - | 2 | 2 | 2 |
| rv2_024 (79ebd9a6...) | - | - | 2 | 2 | 2 |
| rv2_025 (79ed7553...) | - | - | 2 | 2 | 2 |
| rv2_026 (7dc9a335...) | - | - | 2 | 2 | 2 |
| rv2_027 (9cea4682...) | - | - | 2 | 2 | 2 |
| rv2_028 (9e9b539e...) | - | - | 2 | 2 | 2 |
| rv2_029 (b6c6854d...) | - | - | 2 | 2 | 2 |
| rv2_030 (cac4c8e7...) | - | - | 2 | 2 | 2 |
| rv2_031 (d5ba419e...) | - | 4 | 1 | 1 | 1 |
| rv2_032 (e883aa81...) | - | 5 | 1 | 1 | 1 |
| rv2_033 (f3b80352...) | - | - | 2 | 2 | 2 |
| rv2_034 (fe26f3ec...) | - | - | 2 | 2 | 2 |
| rv2_035 (fec62752...) | - | - | 2 | 2 | 2 |
| **gold in top 1** | 0/20 | 1/20 | 4/20 | 4/20 | 4/20 |
| **gold in top 5** | 0/20 | 3/20 | 20/20 | 20/20 | 20/20 |
| **gold in top 10** | 0/20 | 4/20 | 20/20 | 20/20 | 20/20 |

## 8. Two RRF ties

Fusion scores a chunk as the sum of 1 / (60 + rank) over the lists it is in, with rank counted from 1. A chunk that is rank 1 in exactly one list scores 1/61 = 0.016393. Two chunks with equal scores keep the order of first appearance, and the dense lists are visited before the keyword lists.

**Tie 1: dense rank 1 against keyword rank 1.** For a seller-ID question, the gold digest is rank 1 of the keyword list only (the dense list does not know the id). Some other digest is rank 1 of the dense list only. Both score 1/61. The tie goes to the dense one, so the gold is rank 2.

| run | seller golds at rank 2 | of which: rank 1 is dense-only, gold is keyword-only, equal score |
|---|---:|---:|
| `final_layout_hybrid` | 16/20 | 16/20 |
| `final_layout_hybrid_merged` | 16/20 | 16/20 |
| `final_layout_hybrid_scoped` | 16/20 | 16/20 |

Neither D4 nor D5 changes this tie. D5 only merges the two dense lists; the keyword list is still separate. A fix was not tried and is not claimed (a change to the tie-break or to list weights is outside the spec: "do not change the tie-break, weights or rrf_k").

**Tie 2: dense rank 1 of the sample list against dense rank 1 of the upload list.** The two lists use the same model and cosine, so their scores compare, but as two RRF lists their rank-1 chunks tie. The tie goes to list order (sample first), whatever the cosines were. Passages at ranks 1 and 2 with exactly equal fused scores, in answerable questions, by what they are:

| run | rank 1 | rank 2 | tied questions | of which gold is rank 2 |
|---|---|---|---:|---:|
| `final_layout_hybrid` | sample (both) | sample (both) | 4 | rv2_004, rv2_039 |
| `final_layout_hybrid` | sample (both) | upload (both) | 2 | rv2_087, rv2_093 |
| `final_layout_hybrid` | sample (dense only) | sample (keyword only) | 18 | rv2_016, rv2_017, rv2_019, rv2_020, rv2_022, rv2_023, rv2_024, rv2_025, rv2_026, rv2_027, rv2_028, rv2_029, rv2_030, rv2_033, rv2_034, rv2_035 |
| `final_layout_hybrid_scoped` | sample (both) | sample (both) | 3 | rv2_004, rv2_039 |
| `final_layout_hybrid_scoped` | sample (both) | upload (both) | 3 | rv2_076, rv2_078, rv2_081 |
| `final_layout_hybrid_scoped` | sample (dense only) | sample (keyword only) | 16 | rv2_016, rv2_017, rv2_019, rv2_020, rv2_022, rv2_023, rv2_024, rv2_025, rv2_026, rv2_027, rv2_028, rv2_029, rv2_030, rv2_033, rv2_034, rv2_035 |
| `final_layout_hybrid_scoped` | upload (both) | upload (both) | 1 | rv2_084 |
| `final_layout_hybrid_merged` | sample (both) | sample (both) | 4 | rv2_004, rv2_039 |
| `final_layout_hybrid_merged` | sample (dense only) | sample (keyword only) | 18 | rv2_016, rv2_017, rv2_019, rv2_020, rv2_022, rv2_023, rv2_024, rv2_025, rv2_026, rv2_027, rv2_028, rv2_029, rv2_030, rv2_033, rv2_034, rv2_035 |
| `final_layout_hybrid_merged` | upload (both) | upload (both) | 1 | rv2_084 |
| `final_layout_hybrid_scoped_merged` | sample (both) | sample (both) | 3 | rv2_004, rv2_039 |
| `final_layout_hybrid_scoped_merged` | sample (dense only) | sample (keyword only) | 16 | rv2_016, rv2_017, rv2_019, rv2_020, rv2_022, rv2_023, rv2_024, rv2_025, rv2_026, rv2_027, rv2_028, rv2_029, rv2_030, rv2_033, rv2_034, rv2_035 |
| `final_layout_hybrid_scoped_merged` | upload (both) | upload (both) | 1 | rv2_084 |

In `final_layout_hybrid_scoped` the sample-versus-upload ties with gold at rank 2 are the upload questions rv2_076, rv2_078, rv2_081. Their fused scores: rv2_076 rank 1 0.032787 (sample), rank 2 0.032787 (upload); rv2_078 rank 1 0.032787 (sample), rank 2 0.032787 (upload); rv2_081 rank 1 0.032787 (sample), rank 2 0.032787 (upload).

In `final_layout_hybrid_merged` and `final_layout_hybrid_scoped_merged` no top-2 tie is between a sample and an upload passage (sample-versus-upload ties in `final_layout_hybrid_merged`: 0). One tie between two upload passages remains (rv2_084); D5 does not touch it.

## 9. Unanswerable questions and abstention

Ten questions ask for something the documents do not hold. Abstention needs a score that separates them from answerable questions. The fused RRF score cannot (about 0.03 for both, in hybrid), so the thresholds use the per-leg `best_dense_score` and `best_keyword_score` of the first retrieval round. Median and range over questions:

| run | group | n | best dense: min / median / max | best keyword: min / max |
|---|---|---:|---:|---:|
| `final_layout_dense` | unanswerable | 10 | 0.573 / 0.679 / 0.732 | n/a |
| `final_layout_dense` | answerable | 100 | 0.542 / 0.755 / 0.870 | n/a |
| `final_layout_hybrid` | unanswerable | 10 | 0.573 / 0.679 / 0.732 | 10.63 / 54.39 |
| `final_layout_hybrid` | answerable | 100 | 0.542 / 0.755 / 0.870 | 7.18 / 96.15 |
| `final_layout_hybrid_scoped` | unanswerable | 10 | 0.573 / 0.679 / 0.732 | 2.83 / 54.39 |
| `final_layout_hybrid_scoped` | answerable | 100 | 0.542 / 0.755 / 0.870 | 3.17 / 96.15 |
| `final_layout_hybrid_merged` | unanswerable | 10 | 0.573 / 0.679 / 0.732 | 10.63 / 54.39 |
| `final_layout_hybrid_merged` | answerable | 100 | 0.542 / 0.755 / 0.870 | 7.18 / 96.15 |
| `final_layout_hybrid_scoped_merged` | unanswerable | 10 | 0.573 / 0.679 / 0.732 | 2.83 / 54.39 |
| `final_layout_hybrid_scoped_merged` | answerable | 100 | 0.542 / 0.755 / 0.870 | 3.17 / 96.15 |

The dense scores overlap: the lowest answerable best dense score (0.542) is below the highest unanswerable one (0.732) in `final_layout_hybrid_scoped`. That is why the filter is weak.

Output of `scripts/calibrate_abstention.py final_layout_hybrid_scoped`, unedited:

```
run final_layout_hybrid_scoped: params {'mode': 'hybrid', 'scope': 'dataset'}, 110 questions (100 answerable, 10 unanswerable)

unanswerable questions (best scores of the first retrieval round):
  id         corpus    best_dense_score  best_keyword_score
  rv2_101    sample              0.6806             32.0557
  rv2_102    sample              0.6210             21.2517
  rv2_103    sample              0.7319             54.3931
  rv2_104    sample              0.7035             40.7521
  rv2_105    sample              0.6302             44.0221
  rv2_106    upload              0.6769              8.7994
  rv2_107    upload              0.7079              8.8500
  rv2_108    upload              0.7156              2.8290
  rv2_109    upload              0.6703              6.2662
  rv2_110    upload              0.5729              8.7583

answerable distribution:
  best_dense_score    min 0.5423  p10 0.6617  p25 0.7049  median 0.7549   (n=100 of 100)
  best_keyword_score  min 3.1744  p10 8.3730  p25 9.1466  median 18.9032   (n=99 of 100)

chosen (budget: at most 2 answerable abstained):
  min_dense 0.5767  min_keyword 8.7812   (exact: 0.5767178798791651, 8.781194941742484)
  unanswerable abstained: 1 of 10
  answerable abstained:   0 of 100

In-sample: these thresholds were chosen on this frozen test set and are not a held-out estimate.
```

The thresholds in `config/statesync.toml` are `abstain_min_dense = 0.5767178798791651` and `abstain_min_keyword = 8.781194941742484`. The recalibration on the fresh run reproduces them exactly. The fresh run's per-leg scores are identical to `layout_hybrid_scoped` to six places, which is why. Both thresholds were chosen on the frozen test set itself, so the counts are an in-sample fit, not an estimate for new questions.

Those thresholds applied to `final_layout_hybrid_scoped_merged` (D5 does not change the per-leg scores):

```
thresholds applied: min_dense 0.5767  min_keyword 8.7812
  unanswerable abstained: 1 of 10
  answerable abstained:   0 of 100
```

## 10. What did not improve

- **Paraphrase hit@1** is worse in every hybrid run than in dense mode (60.0% to 40.0% in `final_layout_dense` to `final_layout_hybrid`). In the saved passages of `final_layout_hybrid`, several paraphrase questions lose it to other documents that have both a dense and a keyword score (rv2_057, rv2_058, rv2_063 and rv2_064 each have such a passage at rank 1 while the gold is lower). Paraphrase questions were written to avoid the gold's own words, so a keyword match on a few shared words can outrank the gold. This is a reading of the saved passages, not a tested cause. D4 and D5 do not change it.
- **ID questions at rank 1.** 30.0% in `final_layout_hybrid_merged` (dense 13.3%). Recall is fixed (100.0% at hit@10), rank 1 is not: Tie 1 above.
- **Definition questions** in plain hybrid: hit@1 85.7% dense to 71.4% hybrid (`final_layout_dense` to `final_layout_hybrid`). Merged brings it to 85.7% (`final_layout_hybrid_merged`), which is the dense value.
- **Dataset scope**: no gain on this set (section 5, and the summary). It also loses paraphrase hit@5.
- **Table questions** are 84.8% hit@1 in dense mode and 84.8% in `final_layout_hybrid_merged`: unchanged. Their hit@5 goes from 93.9% to 97.0%.
- **Abstention** catches 1 of 10 unanswerable questions (in-sample).
- **Latency**: median search time is 153 ms in dense mode (`final_layout_dense`) and 230 to 279 ms in the four hybrid runs (section 3).

## 11. Decision log

In the order the decisions were made. **Pre-registered** means it was written in the spec before any result on this set. **Post-hoc** means it was made after seeing results on this same set.

| # | Decision | Status | Evidence / reason | Commit |
|---|---|---|---|---|
| 1 | Freeze the 110-question test set; record its SHA-256 before any retrieval code changes | pre-registered | `652f11dd...` unchanged since | `28bdabe` |
| 2 | A harness that calls the production MCP path; baseline on today's code | pre-registered | `baseline` | `28bdabe` |
| 3 | Phase B/C: layout-aware parsing (pdfplumber) and structure-aware chunking, shipped off | pre-registered | upload hit@1 80.0% to 92.0%, MRR 0.887 to 0.960 (`baseline` to `layout_dense`) | `de5e88a` |
| 4 | Phase D: hybrid retrieval, BM25 in SQL plus dense, RRF with k = 60, 50 candidates per list, k1 = 1.2, b = 0.75, shipped off | pre-registered | `paragraph_hybrid`: hit@5 77.0% to 81.0%, ID hit@10 30.0% to 46.7%; paraphrase hit@1 60.0% to 40.0%; p50 607 ms | `b270bfa` |
| 5 | D4: a BM25 leg drops query terms found in more than half of its corpus (`bm25_max_df_ratio = 0.5`) | **post-hoc** (motivated by the Phase D failure analysis); the 0.5 cut was fixed by theory (the Robertson-Sparck Jones weight reaches 0 at half the collection) before its own run, and no other value was tried | `paragraph_hybrid` to `paragraph_hybrid_d4`: hit@5 81.0% to 98.0%, MRR 0.689 to 0.793, p50 607 to 294 ms | `40a7d5e` |
| 6 | Phase E: scope sample documents by dataset (prefix lists, verified against the real document ids), shipped off | pre-registered (mechanism and prefixes) | `layout_hybrid_d4` to `layout_hybrid_scoped`: MRR 0.791 to 0.788; no gain | `b6d1de2` |
| 7 | Phase E: model-free abstention, shipped off | mechanism pre-registered; **the two thresholds are post-hoc**, fitted on this set by `scripts/calibrate_abstention.py` | 1 of 10 unanswerable caught, 0 of 100 answerable lost; in-sample | `b6d1de2` |
| 8 | F0 / D5: `hybrid_dense_lists = "merged"`, one dense list in hybrid, shipped `"separate"` | **post-hoc** (made after the Phase E results on this set; motivated by the three upload table questions scope cost) | `final_layout_hybrid` to `final_layout_hybrid_merged`: upload hit@1 88.0% to 96.0%, overall MRR 0.791 to 0.803; `final_layout_hybrid_scoped` to `final_layout_hybrid_scoped_merged`: upload hit@1 84.0% to 96.0% | Phase F commit (with this report) |
| 9 | Every new switch ships at today's behaviour (D-v3-16) and is guarded by `scripts/shipped_flags.py`; the lead flips defaults after the audit | pre-registered | `shipped_flags.py` exits 0 after the runs | |

## 12. Recommendation

The owner flips shipped defaults. This report only recommends. Every row rests on the frozen set that also guided D4, D5 and the thresholds, and on retrieval metrics only; the effect on answers needs model calls and was not measured.

| flag | recommended value | evidence |
|---|---|---|
| `[pipeline] retrieval_mode` | `"hybrid"`, with `hybrid_dense_lists = "merged"` | `final_layout_dense` to `final_layout_hybrid_merged`: hit@5 77.0% to 98.0%, hit@10 79.0% to 100.0%, MRR 0.688 to 0.803, ID hit@10 30.0% to 100.0%. Costs: p50 153 to 257 ms; paraphrase hit@1 60.0% to 40.0%. Weigh the paraphrase loss against ID recall, ideally on a held-out set. |
| `[retrieval] hybrid_dense_lists` | `"merged"` if hybrid is turned on | `final_layout_hybrid` to `final_layout_hybrid_merged`: upload hit@1 88.0% to 96.0%, MRR 0.791 to 0.803; sample unchanged; same SQL; nothing got worse in rank (0 questions worse). Post-hoc. |
| `[library] chunker` | `"layout"` | `final_paragraph_dense` to `final_layout_dense`: upload hit@1 80.0% to 92.0%, upload MRR 0.887 to 0.960; sample corpus unchanged. Affects uploads made after the change; existing uploads need a re-ingest. One synthetic upload set (3 PDFs). |
| `[pipeline] doc_scope` | keep `"all"` | No retrieval gain: `final_layout_hybrid` to `final_layout_hybrid_scoped` hit@1 65.0% to 64.0%, MRR 0.791 to 0.788; `final_layout_hybrid_merged` to `final_layout_hybrid_scoped_merged` hit@1 67.0% to 67.0%, paraphrase hit@5 100.0% to 90.0%. Turn it on only if keeping another dataset's documents out of the context is wanted for its own sake. |
| `[pipeline] doc_abstain` | keep `"off"` | 1 of 10 unanswerable caught, 0 of 100 answerable lost, thresholds fitted on the test set; the score ranges overlap. Not worth a behaviour change on this evidence. |
| `[retrieval] bm25_max_df_ratio` | keep `0.5` | `paragraph_hybrid` to `paragraph_hybrid_d4`: hit@5 81.0% to 98.0%, MRR 0.689 to 0.793, p50 607 to 294 ms. Post-hoc, value fixed by theory before the run. Only matters when hybrid is on. |

Before any of this goes live, write a fresh held-out set after the decisions above are frozen, and run it once.

## 13. Limitations

- 110 questions, 10 of them unanswerable. One question moves hit@1 by 1 point, and a category of 7 or 10 questions by 10 to 14 points.
- One synthetic upload set: three demo PDFs, with 30 questions about them.
- No held-out set. Every number is on the set that guided D4, D5 and the abstention thresholds. A fresh held-out set, written after the decisions are frozen, is the next step.
- No re-ranker, and no new embedding model.
- Latency was measured on one Mac, with the local Ollama and Postgres, one run per configuration. It varies by tens of milliseconds between runs.
- Multi-column PDFs: the layout parser has no column detection and reads a page top to bottom across its full width, so a two-column page interleaves its columns (`parser/server.py`, known limitations). Scanned PDFs have no text layer and are not OCR'd. A table drawn without ruling lines may stay narrative text.
- The sample corpus (20,245 chunks) was not re-chunked or re-embedded; only uploads use the layout chunker.
- Retrieval metrics only. Which passages the answer model sees changes with hybrid, and that end-to-end effect on answers is not measured here; it needs model calls.
- Gold is a document plus evidence text. A passage that answers the question from another document counts as a miss.

## 14. Reproduction checks

A stdlib script compared per-question `first_hit_rank` between fresh and earlier runs. Upload chunk ids differ per run (the harness re-uploads), so passages are compared by excerpt and scores rounded to six places, not by id.

```
final_paragraph_dense vs baseline: 110 questions; first_hit_rank differs: 0 []; passages (excerpt+scores, 6dp) differ: 0 []; best_* scores differ: 110 ['rv2_001', 'rv2_002', 'rv2_003', 'rv2_004', 'rv2_005', 'rv2_006', 'rv2_007', 'rv2_008', 'rv2_009', 'rv2_010']
final_layout_dense vs layout_dense_d4: 110 questions; first_hit_rank differs: 0 []
final_layout_hybrid vs layout_hybrid_d4: 110 questions; first_hit_rank differs: 0 []
final_layout_hybrid_scoped vs layout_hybrid_scoped: 110 questions; first_hit_rank differs: 0 []
--- extra: same pairs, passages and scores as well
final_layout_dense vs layout_dense_d4: 110 questions; first_hit_rank differs: 0 []; passages (excerpt+scores, 6dp) differ: 0 []; best_* scores differ: 0 []
final_layout_hybrid vs layout_hybrid_d4: 110 questions; first_hit_rank differs: 0 []; passages (excerpt+scores, 6dp) differ: 0 []; best_* scores differ: 0 []
final_layout_hybrid_scoped vs layout_hybrid_scoped: 110 questions; first_hit_rank differs: 0 []; passages (excerpt+scores, 6dp) differ: 0 []; best_* scores differ: 0 []
```

`best_*` scores differ against `baseline` for all 110 questions because `baseline` was saved before the harness recorded them (the field is absent there); it is not a retrieval difference. Passages (excerpt, fused score, dense score, keyword score) and ranks match.

**Lead audit (independent reproduction).** The lead re-ran the recommended configuration (`chunker = "layout"`, `--mode hybrid`, `hybrid_dense_lists = "merged"`) as a separate run and compared it with `final_layout_hybrid_merged`: first-hit rank differs on 0 of 110 questions, and passages (excerpt and fused score, 6 places) differ on 0. Earlier in the work the lead had also reproduced `paragraph_hybrid_d4` exactly, and the shipped dense path twice against `baseline` (all 110 questions, passage text and score).

Shipped state after the runs: `[library] chunker = "paragraph"`, `[retrieval] hybrid_dense_lists = "separate"`, `scripts/shipped_flags.py` exits 0, and the `mcp` and `orchestrator` containers were recreated and read those values.
