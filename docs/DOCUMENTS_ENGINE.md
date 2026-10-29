# The documents engine

Everything about how StateSync answers from documents (the unstructured half of the system):
- how a file becomes searchable;
- what the embedding model and the vector database do;
- how a question is searched and re-ranked;
- every retrieval experiment, its metrics and what it decided.

For the whole system see [ARCHITECTURE.md](ARCHITECTURE.md); for the statistics vocabulary see
[RESULTS_EXPLAINED.md](RESULTS_EXPLAINED.md) §1.

Contents:

1. [The engine in one picture](#1-the-engine-in-one-picture)
2. [Indexing: from an uploaded file to searchable passages](#2-indexing-from-an-uploaded-file-to-searchable-passages)
3. [The embedding model](#3-the-embedding-model)
4. [The vector database](#4-the-vector-database)
5. [Searching: two rounds](#5-searching-two-rounds)
6. [Re-ranking](#6-re-ranking), then [a worked example with real values](#6b-a-worked-example-with-real-values)
7. [The four designs compared](#7-the-four-designs-compared)
8. [The metrics](#8-the-metrics)
9. [The experiments, in order](#9-the-experiments-in-order)
10. [Every choice, and why](#10-every-choice-and-why)
11. [What has not been measured](#11-what-has-not-been-measured)
12. [Where it is in the code](#12-where-it-is-in-the-code)

---

## 1. The engine in one picture

```
 INDEXING (once per uploaded file, in the background)
 ──────────────────────────────────────────────────────────────────────────────────────────────
  PDF / text / Markdown ─▶ parser (isolated) ─▶ text per page ─▶ chunker ─▶ passages (≤1,400 chars,
                                                                            one page each, 200 overlap)
                                                       ─▶ mxbai-embed-large (Ollama, local) ─▶ 1,024 numbers per passage
                                                       ─▶ Postgres + pgvector (workspace_docs.chunks)

 ANSWERING (per question)
 ──────────────────────────────────────────────────────────────────────────────────────────────
  question ─▶ embed the question (same model)
           ─▶ ROUND 1: closest 8 passages across all the workspace's documents  ─▶ which FILES they come from
           ─▶ ROUND 2: closest 25 passages inside only those files              ─▶ the candidates
           ─▶ RE-RANK: Jev asks of each of the 25 "does this passage contain what the question needs?"
                       and sorts by that probability
           ─▶ the answer writer reads the top 8, cites file and page (citations built by code)
```

On the demo data:

| | |
|---|---|
| Documents | 84 SEC filings (FinanceBench's 10-Ks and others) |
| Pages | 11,948 |
| Passages | 44,918; average 1,033 characters (median 1,119, maximum 1,400) |
| Stored | 325 MB (text and vectors) |
| Indexing time | 7.9 hours for all 84 filings on the laptop's CPU (about 0.6 s per passage) |
| One search | about 0.3–0.5 s for both rounds |
| Re-ranking | 1–3 s live; median 4.4 s in the test (25 Jev calls, 8 at a time); about $0.0009 per question |

---

## 2. Indexing: from an uploaded file to searchable passages

**Upload** (Data sources → Documents → a folder → upload).
- Accepted: PDF, plain text, Markdown; up to 20 MB and 600 pages.
- The file's SHA-256 is stored, and the document is marked *processing*.

**Parsing** (`parser/server.py`). A separate container turns the file into text, page by page.
- It uses **pypdf**'s text extraction.
- It has no network, no secrets, a 60-second CPU limit and 768 MB of memory: an uploaded file is untrusted, so
  it is opened where it can do no harm.
- Limits: scanned pages have no text layer and come out empty (no OCR); multi-column pages come out in pypdf's
  reading order.

**Chunking** (`orchestrator/library_ingest.py`, `chunk_pages`). Each page is cut into *passages* (chunks):

| Rule | Setting | Why |
|---|---|---|
| A passage never spans two pages | — | every citation must name one page |
| Paragraphs (blank-line separated) are packed together up to a size | `chunk_chars` = 1,400 characters (about 350 tokens) | inside the embedding model's 512-token window, with room to spare; long enough to carry a full statement or a small table |
| An over-long paragraph is cut at a sentence end, else a line break, else a space | — | never cut mid-word if avoidable |
| Consecutive passages of a page overlap | `chunk_overlap_chars` = 200 | a sentence cut at a boundary is whole in one of the two |
| Fragments shorter than a minimum are dropped | `chunk_min_chars` = 40 | page numbers and running headers are noise |

The chunker is pure and deterministic (same input, same passages), so it is unit-tested without a model or a
database.

**Embedding.** The passages go to the MCP tool server in batches of 8. It sends each batch to the embedding
model (section 3) in one request and stores each passage with its vector. Each embedding call has a 30 s limit
and 2 retries. A document ends *ready* or *failed*, never stuck: one still processing after 30 minutes is shown
as failed.

**What is stored** (`workspace_docs.chunks`, one row per passage):

| Column | |
|---|---|
| `chunk_id`, `document_id`, `ordinal` | identity and reading order |
| `page` | the page number, for citations |
| `content_md` | the passage text |
| `embedding` | `vector(1024)`: the passage's meaning as 1,024 numbers |
| `tenant_id` | whose it is; row-level security keeps tenants apart |
| `section`, `kind`, `tsv` | written by earlier experiments (the layout chunker and keyword search); unused now |

A **folder** is attached to one or more workspaces (`workspace_docs.workspace_folders`). A search covers only
the ready documents in the folders of the workspace the question is asked in.

---

## 3. The embedding model

**What an embedding is.** A model reads a piece of text and outputs a fixed-length list of numbers (a vector)
so that texts with similar meaning get nearby vectors. "Net revenue rose 6%" and "sales increased six percent"
land close together even though they share almost no words. Search then becomes geometry: embed the question,
and find the passages whose vectors point in the most similar direction.

**Similarity.** *Cosine similarity*: the cosine of the angle between two vectors; 1 = same direction, 0 =
unrelated. pgvector computes the cosine *distance* `<=>`, and the score shown is `1 − distance`.

**The model: `mxbai-embed-large` (mixedbread.ai, v1).**

| | |
|---|---|
| Architecture | a BERT-large-sized transformer encoder, about 335 million parameters |
| Output | 1,024 numbers per text |
| Input limit | 512 tokens (about 2,000 characters of English), so passages are capped at 1,400 characters |
| Training | contrastive: pairs of texts that belong together are pulled close and others pushed apart, so distance tracks relevance |
| Runs on | Ollama, inside Docker on the laptop's CPU, kept in memory (`OLLAMA_KEEP_ALIVE=-1`) |
| Used for | every passage at indexing time, and every question at search time; both must use the same model, or the distances mean nothing (a vector of the wrong size is an error, not a silent mismatch) |

**Why this model.**
- **Local and open.** Uploaded documents never leave the machine to be indexed, and there is no per-call cost.
- **Strong for its size.** It was among the best open models of its size on the public MTEB retrieval
  benchmark when the project started, and it runs on a CPU.
- **Context matches the chunk size.** Its 512-token window fits the 1,400-character passage.

**What it does not do well (measured).** A dense vector captures topic, not exact identity. In the earlier
document set, questions naming an ID ("seller S-1042") found the right passage in the top 10 only 30% of the
time. On filings, near-duplicate passages from different years' 10-Ks look alike to it. Both problems motivated
the two-round search and the re-ranker.

**Not tested in this project.**
- Other embedding models: MiniLM was tested only as a re-ranker.
- The query instruction mxbai recommends adding in front of a question.

See section 11.

---

## 4. The vector database

**pgvector in Postgres.** The passages and their vectors live in StateSync's own Postgres (16.4) with the
pgvector extension (0.8.5). Why not a separate vector database:
- **One store.** Documents, folders, workspaces and passages are joined in one SQL query, with one backup and
  one transaction.
- **Security comes free.** Postgres row-level security and the tenant predicate apply to passages exactly as
  to everything else. The earlier separate vector store (Qdrant, used by the memory feature) was removed with
  that feature.
- **Fast enough.** At 44,918 passages an exact scan takes about half a second.

**Exact search, not an approximate index.** pgvector can build an HNSW index: a graph that finds *nearly* the
closest vectors much faster. StateSync switches index scans off for every search (`exact_vector_search = true`),
so every passage is compared. The reason was measured:
- On 26 September 2026, after the HNSW index was rebuilt, a question whose answer was in one document
  ("What transit time has FastShip 3 committed to for Metro Mumbai?") got the wrong top 5 from the index. The
  index's best matches scored about 0.55; the exact best passage scored 0.77. The answer said the documents held
  no such information.
- Raising HNSW's search breadth (`ef_search`) found it at 100 and 200 and lost it again at 400. Recall did not
  improve steadily, so no setting could be trusted.
- Exact search over 20,245 passages took 71 ms against 2.6 ms: negligible next to a model call.

Exact search grows linearly with the number of passages. It is right for tens or hundreds of thousands; at
millions, an approximate index would have to come back, measured first.

**The query** (`mcp/tools/vector_tools.py`):

```sql
SELECT c.chunk_id, c.document_id, c.content_md, c.page,
       1 - (c.embedding <=> :question_vector) AS score, d.title
FROM workspace_docs.chunks c
JOIN workspace_docs.documents d          ON d.document_id = c.document_id
JOIN workspace_docs.workspace_folders wf ON wf.folder_id  = d.folder_id
WHERE wf.workspace_id = :workspace AND c.tenant_id = :tenant AND d.status = 'ready'
  [AND c.document_id = ANY(:documents)]        -- round 2 only
ORDER BY c.embedding <=> :question_vector
LIMIT :top_k
```

The workspace comes from the session header that the orchestrator checked, never from the caller's arguments.

---

## 5. Searching: two rounds

**Round 1 finds the files.** The question is embedded, and the 8 closest passages across all the workspace's
documents are fetched (`semantic_top_k` = 8). Only the *documents* they come from matter here.

**Round 2 finds the passages.** The same search runs again, restricted to those documents, returning up to 25
passages (`semantic_top_k_max` = 25), merged with round 1's own passages.

**Why two rounds.** In 84 filings, the same company's 10-Ks for different years, and different companies'
risk sections, say very similar things. A flat search lets near-duplicates from the wrong filing crowd out the
right page. Deciding the files first and then searching inside them keeps the candidates on target. Section 9,
experiment 4 measures what this did.

**Why 8 and 25.**
- **8 is the writer's window.** The answer writer reads 8 passages: more costs tokens and dilutes the evidence,
  fewer misses answers that span two passages.
- **25 is the re-ranker's candidates.** It is enough for the right page to be among them 70% of the time
  (section 9); re-ranking 25 costs 25 Jev calls.

These values were chosen, not tuned in a sweep.

**Two-source questions.** For "Which company had the highest revenue in 2022, and how does it describe its
e-commerce strategy?", SQL runs first. The search is then limited to the documents whose titles name a company
in the SQL answer ("Walmart" → Walmart's filings), and round 1 is skipped. This relies on titles like
`WALMART 2020 10K`; with other titles, nothing matches and the search covers all the workspace's documents.

**Fallback order.** If re-ranking is off or fails, the writer reads the first 8 by similarity, grouped by
document in reading order, so a table stays next to its text.

---

## 6. Re-ranking

**What a re-ranker does.** The embedding compares a question and a passage *separately* (each becomes a
vector), which is fast but coarse. A re-ranker reads the question and one passage *together* and judges
whether the passage answers it: much slower, much more precise. So it is used only on the 25 candidates.

**Two kinds were compared.**

| | Cross-encoder (bge-reranker-v2-m3) | Jev (the final system) |
|---|---|---|
| What it is | a model trained for search re-ranking: question + passage in, relevance score out | a *typed decision model*: given a situation and a fixed question, it returns a probability for each answer |
| The question asked | (built in) "how relevant is this passage?" | "Does this passage contain the information needed to answer the question?" (yes/no) |
| What it reads | question + passage | the question, then "Passage from {title}, page {n}:" and the first 3,000 characters |
| Where it runs | locally: a separate process on the Mac's GPU (31 s per search on Docker's CPU, 4.5 s on the GPU) | a hosted API, 8 requests at a time |
| Time per question | 12.1 s median for the two-round search with re-ranking, on the GPU, in the page test | 4.4 s median for 25 passages in the test; 1–3 s live |
| Cost | none per call, but a GPU process to keep running | about $0.0009 per question |

**Jev's ordering.** Passages are sorted by Jev's probability of "yes", highest first; ties keep their search
order. A passage whose request failed has no score and goes after the scored ones in its search order, so a Jev
outage leaves the plain search order rather than an error.

---

## 6b. A worked example, with real values

Every number below came from the running system on 29 October 2026.

**1. What an embedding looks like.** The question *"What was Walmart's net revenue growth?"* through
mxbai-embed-large:

```
1,024 numbers:  [-0.0323, 0.0566, -0.0292, 0.0309, -0.0258, -0.0265, -0.0353, 0.0279, …]
each between about -0.10 and 0.16;   length (norm) = 1.0
```

The numbers have no individual meaning; only the direction of the whole vector does.

**2. How similarity is calculated.** For two vectors *a* and *b*,
`cosine(a, b) = (a₁b₁ + a₂b₂ + … + a₁₀₂₄b₁₀₂₄) / (|a| · |b|)`. Because these vectors have length 1, it is
simply the sum of the 1,024 products. pgvector's `<=>` returns the distance `1 − cosine`, and StateSync shows
`1 − distance`. The same question against four sentences:

| Sentence | Cosine |
|---|---|
| "Walmart's total revenues rose 6% year over year." | **0.768** |
| "Net sales increased 6.7% compared with the previous fiscal year." | 0.593 |
| "Our stores in Mexico sell groceries and general merchandise." | 0.379 |
| "The board of directors met in May to approve the dividend." | 0.328 |

Same meaning in other words scores high; unrelated text scores low. Scores are relative: on this model,
unrelated text rarely goes below about 0.3.

**3. A real dense search.** *"What was Walmart's total revenue in fiscal 2020?"* over all 44,918 passages:

| Rank | Cosine | Passage |
|---|---|---|
| 1 | 0.808 | WALMART 2020 10K p.37 (diluted earnings per share) |
| 2 | 0.800 | WALMART 2020 10K p.82 (disaggregated revenues) |
| 3 | 0.783 | **WALMART 2020 10K p.51: the income statement, "Total revenues 523,964"** (the answer) |
| 4 | 0.782 | WALMART 2020 10K p.38 |
| 5 | 0.773 | WALMART 2019 10K p.35 (the wrong year) |
| 6–8 | 0.760–0.766 | WALMART 2018 and 2019 10Ks |

The answer is third, and older filings with near-identical scores fill the list: the problem the two rounds and
the re-ranker exist for.

**4. A real Jev re-ranking call.** One request per passage:

```json
{ "model": "jev-1.13",
  "state": "Question: What was Walmart's total revenue in fiscal 2020?\n\nPassage from WALMART 2020 10K, page 51:\nWalmart Inc. Consolidated Statements of Income … Total revenues 523,964 …",
  "questions": { "contains": { "type": "noul",
      "instructions": "Does this passage contain the information needed to answer the question?" } } }
```

The answer is a probability, not text: `{"answers": {"contains": {"noul": 0.99}}}`, about 800 tokens in and 20
out, about 1–1.5 s.

| Passage | Dense cosine (rank) | Jev P(yes) |
|---|---|---|
| 2020 10K p.51, income statement | 0.783 (3rd) | **0.99** → first |
| 2020 10K p.37, earnings per share | 0.808 (1st) | 0.25 |
| 2019 10K p.35, the previous year | 0.773 (5th) | 0.06 |

**5. What a cross-encoder outputs** (bge-reranker-v2-m3; saved from the FinanceBench runs, as the service is no
longer part of the system). Each (question, passage) pair gives one unbounded relevance score, a logit, not a
probability. In one question's list:
- a passage with dense cosine 0.692 scored **3.06** and moved to first;
- the dense list's top passage (0.746, from another filing) scored **0.72** and fell to eleventh.

## 7. The four designs compared

These are the four orders of the 25 candidates tested in Paper B's pre-registered page-finding test (300 new
questions; section 9, experiment 6). All four use the same index, embedding and two-round search; only what
orders the candidates differs.

```
 A. DENSE ONLY                 question ─▶ embed ─▶ 2-round search (25) ──────────────────────────────▶ writer reads top 8
 B. DENSE + CROSS-ENCODER      question ─▶ embed ─▶ 2-round search ─▶ bge cross-encoder orders (GPU) ─▶ writer reads top 8
 C. DENSE + JEV  (final)       question ─▶ embed ─▶ 2-round search (25) ─▶ Jev orders (API) ──────────▶ writer reads top 8
 D. DENSE + CROSS-ENCODER + JEV question ─▶ embed ─▶ 2-round search ─▶ bge orders ─▶ Jev re-orders ───▶ writer reads top 8
```

| Design | Right page first (page@1) | In the top 3 (page@3) | In the top 8 (page@8) | Right document first | Time to order | Needs |
|---|---|---|---|---|---|---|
| A. Dense only | 20.3% | 38.7% | 51.0% | 62.0% | 0 (search ≈ 0.5 s) | nothing extra |
| B. + cross-encoder | 43.7% | 61.7% | 67.7% | 90.7% | 12.1 s for both rounds on the Mac GPU (31 s per search on CPU) | a GPU process |
| **C. + Jev (final)** | **49.3%** | **63.3%** | **67.0%** | **88.0%** | 4.4 s median (1–3 s live) | the Jev API |
| D. + cross-encoder + Jev | 53.3% | 66.0% | 69.7% | 92.0% | both | a GPU process and the Jev API |
| Ceiling (right page among the 25 at all) | dense list 70.0%; cross-encoder list 72.3% | | | | | |

**Why C.**
- It beats dense alone by 29 points on page@1 (p < 10⁻¹⁵).
- It is at least as good as the cross-encoder (+5.7, p = 0.053) without a GPU process.
- D is the most accurate, but it adds 4 points on page@1 (2.7 in the top 8) for a second model and a GPU process
  that must be running for every question. We chose one re-ranker that is simpler to run and to explain.

---

## 8. The metrics

| Metric | Meaning | Used in |
|---|---|---|
| **page@k** (also *page hit@k*) | share of questions whose right page is among the first k passages | page tests, FinanceBench retrieval |
| **page@1** | the strictest: the right page is the very first passage | the primary measure of the page tests |
| **page@8** | the right page is among the 8 the writer reads: the answer *can* use it | window experiment, page tests |
| **document@1 / doc hit@k** | the right *file* is first / among the first k (a weaker test than page) | page tests, FinanceBench retrieval |
| **hit@k** (earlier document set) | a passage among the first k contains the gold evidence text from the gold document | RAG v2 |
| **MRR@10** (mean reciprocal rank) | for each question, 1 ÷ the rank of the first right passage (1 at rank 1, 0.5 at rank 2, … 0 if not in the top 10), averaged | RAG v2, FinanceBench retrieval |
| **window** | the right page is in the writer's window of 8 | window experiment |
| **anywhere / ceiling** | the right page is among all the candidates (25 or 30); no re-ordering can beat it | window experiment, page tests |
| **p50 / p90 latency** | median / 90th-percentile time of one search | all retrieval runs |
| **correct (end to end)** | the full answer graded against FinanceBench's reference by an LLM grader with fixed rules | FinanceBench end to end |
| **wins / losses, p** | paired comparison on the same questions; exact McNemar test | everywhere a comparison is claimed |

Retrieval metrics (page@k, MRR) need no model to grade, so they are cheap and exact. End-to-end correctness
needs a grader and measures the whole chain: search, re-ranking and the writer.

---

## 9. The experiments, in order

| # | Date | Set | Kind | Question | Decision |
|---|---|---|---|---|---|
| 1 | 26 Sep | earlier document set | diagnosis | Can the approximate HNSW index be trusted? | exact search |
| 2 | 1 Oct | earlier set, 110 questions | pre-specified, in-house | Do keyword (hybrid) search, a layout chunker, dataset scope or abstention help? | none adopted |
| 3 | 2–3 Oct | FinanceBench, 150 questions | pre-registered (RAG v3) | Do keyword search or a cross-encoder improve retrieval on filings? | keyword search dropped; re-ranking adopted |
| 4 | 5–8 Oct | FinanceBench, 150 | development | Does relevance order and a re-ranker change what the writer sees? | relevance window, titles |
| 5 | 5–6 Oct | FinanceBench, 150 | exploratory, end to end | How many answers become correct? | the rebuilt engine |
| 6 | 7–10 Oct (Paper B) | 300 new questions | pre-registered | Does Jev find the right page better than dense search or a cross-encoder? | Jev re-ranks |

### Experiment 1: the approximate index (diagnosis)

Described in section 4. One failing question was traced to HNSW returning the wrong neighbours after a rebuild,
with no reliable setting. **Decision:** exact search by default (71 ms vs 2.6 ms on 20,245 passages). Every
retrieval number after 26 September is under exact search.

### Experiment 2: RAG v2 on the earlier document set

**Set.** 110 questions written in advance (100 answerable, 10 not) over the earlier product's document set:
- 75 questions on a sample corpus of 20,245 passages;
- 25 on uploaded documents.

Gold is evidence *text* in a gold document, so it survives re-chunking. Categories: ID lookups (30), tables (33),
facts (16), definitions (7), paraphrases (10), cross-section (4).

**What was tried.**
- **Layout chunker:** passages follow the page's layout (headings, tables) instead of paragraphs.
- **Hybrid search:** a keyword (BM25-style) search fused with the dense one by reciprocal rank fusion (RRF).
- **Dataset scope:** search only documents tagged with the question's dataset.
- **Abstention:** answer "not in the documents" below a score threshold.

| Run | hit@1 | hit@5 | hit@10 | MRR@10 | p50 |
|---|---|---|---|---|---|
| shipped: paragraph chunker, dense | 60.0 | 77.0 | 79.0 | 0.670 | 149 ms |
| layout chunker, dense | 63.0 | 77.0 | 79.0 | 0.688 | 153 ms |
| layout, hybrid | 65.0 | 98.0 | 100.0 | 0.791 | 279 ms |
| layout, hybrid, one merged dense list | 67.0 | 98.0 | 100.0 | 0.803 | 257 ms |
| layout, hybrid, merged, dataset scope | 67.0 | 97.0 | 100.0 | 0.803 | 230 ms |

**What it showed.**
- **Keyword search fixed IDs:** hit@10 on ID questions went from 30% to 100%, which dense vectors cannot do.
- **It hurt paraphrases:** hit@1 fell from 60% to 40%.
- **The layout chunker helped uploads:** hit@1 80% → 92%.
- **Dataset scope gave nothing.**
- **Abstention caught 1 of 10** unanswerable questions, with thresholds fitted on the same set.

**Decision.** None was turned on; each was kept behind a switch, to be tested on the filings (experiment 3).
Later:
- keyword search was dropped because it failed there;
- the layout chunker was never re-tested on filings (an 8-hour ingest) and was removed in the clean-up;
- scope and abstention were removed.

### Experiment 3: retrieval on FinanceBench (pre-registered as RAG v3)

**Set.** FinanceBench's 150 analyst questions over its 84 filings; each question's gold is the evidence page.
The decision rule was fixed before any result: recommend a re-ranker if it raises page hit@5 by at least 5
points; latency does not veto it.

| Run | page@1 | page@5 | page@10 | doc@1 | doc@5 | MRR@10 | p50 per search |
|---|---|---|---|---|---|---|---|
| dense (shipped) | 19.3% | 43.3% | 58.7% | 46.7% | 84.0% | 0.303 | 0.23 s |
| hybrid (dense + keyword, RRF) ¹ | 14.7% | 30.7% | 42.0% | 44.7% | 72.7% | 0.211 | 2.6 s |
| dense + MiniLM cross-encoder | 13.3% | 41.3% | 54.7% | 39.3% | 86.0% | 0.246 | 1.5 s |
| **dense + bge-reranker-v2-m3** | **32.0%** | **59.3%** | **69.3%** | **68.0%** | **95.3%** | **0.433** | 30.9 s (CPU) / 4.5 s (Mac GPU) |

¹ Measured on 2 October against its own dense run, while 140 of the 150 questions' filings were indexed: page@5 42.0% dense vs 30.7% hybrid, page@1 19.3% vs 14.7%, MRR 0.296 vs 0.211.

**What it showed.**
- **bge passes the rule by far:** page@5 +16.0 points (32 questions won, 8 lost, p = 0.0002); page@1 26 won
  vs 7 lost (p = 0.001).
- **MiniLM**, a small cross-encoder trained on web search, does not help on filings.
- **Keyword search made filings worse:** financial text is full of the same words in every filing, so word
  overlap favours the wrong passages.
- **bge's cost:** 31 s per search on Docker's CPU. On the Mac GPU it gave identical rankings in 4.5 s.

**Decision.**
- Keyword search was dropped.
- A re-ranker was adopted: bge then, Jev after experiment 6.

### Experiment 4: what the writer sees (the relevance window)

**Set.** The same 150 FinanceBench questions. The test: is the gold page among the **8 passages the writer
reads**?

| Order of the candidates | In the writer's 8 | Right document in the 8 | Gold page among all candidates |
|---|---|---|---|
| Document order (the old engine: passages grouped by file, in page order) | 38.7% | 54.7% | 73.3% |
| **Relevance order** (best score first) | **53.3%** | 91.3% | 73.3% |
| Document order, bge scores | 48.7% | 78.0% | 79.3% |
| Relevance order, bge | 68.7% | 97.3% | 79.3% |
| Relevance order, bge reading document titles | **74.7%** | 99.3% | 82.0% |

- Relevance order alone gained 33 questions and lost 11 (p = 0.0013).
- With bge reading titles, it gained 57 and lost 3.

The old engine found the right page often enough (73% "anywhere") but showed the writer the wrong 8.

**Decision.**
- The writer reads the 8 most relevant passages, not the first passages of each file.
- Every passage carries its document title.

### Experiment 5: answers end to end (FinanceBench, exploratory)

The whole pipeline answered the 150 questions. An LLM grader (Gemini 3.8 Flash) compared each answer with the
reference: correct within 1% or the reference's rounding; partly correct; incorrect; refused.

| Engine | Correct | Partly | Incorrect | Refused |
|---|---|---|---|---|
| Old: router saw 20 titles; document order | **20.0%** (30) | 2 | 15 | 103 |
| Rebuilt: two-round search, relevance window, titles (dense) | **52.7%** (79) | 9 | 23 | 39 |
| Rebuilt + bge re-ranker | **63.3%** (95) | 5 | 20 | 30 |

- Old to rebuilt: 51 won, 2 lost (p < 10⁻¹²).
- Rebuilt + bge against the old engine: 68 won, 3 lost (p < 10⁻¹⁶).

These questions guided the fixes, so the numbers are exploratory. The final design (Jev instead of bge) has not
been run end to end (section 11).

### Experiment 6: does Jev find the right page? (Paper B)

**Earlier, superseded tests.**
- **First test (200 held-out questions).** Jev put the right page first 51.0% of the time, against 10.5% for
  the product's display order. The audit of 7 October found it had compared Jev with the product's *display*
  order (top 8 grouped by file) rather than the cross-encoder's own ranking. That made the baseline too weak.
- **Re-analysis with fair orders.**
  - Same 200 questions: dense 22.0%, bge 52.0%, Jev over dense 52.0%, Jev over bge 56.5%.
  - FinanceBench's 150: 19.3%, 27.3%, 44.7%, 47.3%.

  This was development evidence that led to the pre-registered rerun.

**R1, pre-registered (the result to cite).**

*Set.* 300 pages sampled at random (seed fixed in advance) from the 84 filings, at most 4 per filing, avoiding
FinanceBench's evidence pages and every page of the earlier sets:
- one model (glm-5.3-flash) wrote a question each page answers;
- a second (gpt-oss-120b) confirmed it;
- gold = that page.

Labels were checked by a model, not by hand.

*Arms.* The four designs of section 7. Two-round search ran twice per question: dense, and bge reading titles.

| Measure | A. Dense | B. Cross-encoder | **C. Jev over dense** | D. Jev over cross-encoder |
|---|---|---|---|---|
| page@1 | 20.3% | 43.7% | **49.3%** | 53.3% |
| page@3 | 38.7% | 61.7% | **63.3%** | 66.0% |
| page@8 | 51.0% | 67.7% | **67.0%** | 69.7% |
| document@1 | 62.0% | 90.7% | **88.0%** | 92.0% |
| gold page among the 25 | 70.0% | 72.3% | 70.0% | 72.3% |

| Test (page@1 unless named) | Difference | Wins / losses | 95% interval | p |
|---|---|---|---|---|
| H1 (primary): D vs B | +9.7 | 41 / 12 | +5.0 to +14.3 | < 0.0001 |
| H2: C vs B, Jev with no GPU against the GPU cross-encoder | +5.7 | 43 / 26 | 0.0 to +10.7 | 0.053 |
| H3: C vs A, Jev against dense alone | +29.0 | 102 / 15 | +22.7 to +35.3 | < 10⁻¹⁵ |
| H4: D vs B on page@3 | +4.3 | 19 / 6 | +1.3 to +7.7 | 0.029 |

- H1 resampled by filing instead of by question gives the same interval (+5.1 to +14.4).
- No search or score failed.
- Timings:
  - two-round dense search: 0.5 s median;
  - two-round search with bge: 12.1 s;
  - Jev over 25 passages: 4.4 s median (4.6 s p90), 8 at a time, no rate-limit retries;
  - Jev cost: $0.00087 per question, $0.26 for all 300.

**Decision.** Jev re-ranks the dense list (design C). The cross-encoder was removed.

---

## 10. Every choice, and why

| Part | Chosen | Alternatives considered | Why | Evidence |
|---|---|---|---|---|
| Parser | pypdf text per page, in an isolated container | layout parsing (pdfplumber) | simple, fast, no network; layout parsing only helped the earlier uploads and was never tested on filings | exp. 2 |
| Chunking | paragraphs packed to 1,400 chars, one page each, 200 overlap | layout chunker | page-exact citations; fits the embedding window | exp. 2 (layout +12 hit@1 on uploads only) |
| Embedding model | mxbai-embed-large, 1,024 dims, local | hosted embeddings; smaller models | local, free per call, strong open model, 512-token window | not compared (section 11) |
| Vector store | pgvector in Postgres | Qdrant (removed), a separate vector DB | one store, row-level security, joins | design choice |
| Index | exact scan | HNSW | HNSW missed the right passage with no reliable setting | exp. 1 |
| Search | dense, two rounds (8 → 25) | flat search; hybrid keyword + dense | wrong-year near-duplicates; keyword search hurt filings | exp. 3, 4, 5 |
| Candidates | 25 | 30 (RAG v3) | ceiling 70–72% at 25; cost of 25 Jev calls | exp. 6 |
| Re-ranker | Jev, "does this passage contain…?" | bge cross-encoder, MiniLM, both | +29 page@1 over dense; ≥ cross-encoder without a GPU | exp. 3, 6 |
| Writer window | top 8 by relevance, with titles | document order; more passages | 38.7% → 53.3% gold page seen | exp. 4 |
| Scope / abstention | none | dataset scope; score threshold | no gain; abstention caught 1 of 10 | exp. 2 |

---

## 11. What has not been measured

- **The final design end to end.** Answers with Jev re-ranking (design C) were not graded on FinanceBench's 150
  questions. Experiment 5's 63.3% used bge. Re-running it needs approval because it costs API credits.
- **Other embedding models.** mxbai-embed-large was never compared with alternatives (for example bge-m3,
  nomic-embed or a hosted model). Nor was the query instruction mxbai recommends ("Represent this sentence for
  searching relevant passages: "), which StateSync does not add.
- **Chunk size.** 1,400 characters / 200 overlap was chosen from the model's window, not tuned.
- **The layout chunker on filings** (needs an 8-hour re-ingest).
- **Scanned PDFs** (no OCR), non-English text, and multi-column layouts.
- **Scale.** Exact search is linear in the number of passages: measured at 20k and 45k, not at millions.
- **Questions that need many pages** (comparisons across a whole filing). The writer reads 8 passages.

---

## 12. Where it is in the code

| File | What |
|---|---|
| `parser/server.py` | the isolated parser: file → text per page (pypdf), with limits |
| `orchestrator/library_api.py` | upload, folders, documents API |
| `orchestrator/library_ingest.py` | the chunker (`chunk_pages`) and the background ingest |
| `mcp/embeddings.py` | calls to Ollama: one text, or a batch |
| `mcp/tools/library_tools.py` | stores documents and passages with their vectors |
| `mcp/tools/vector_tools.py` | `vector.semantic_search`: the exact cosine search |
| `orchestrator/nodes/documents.py` | the two-round search, narrowing for two-source questions, the retrieval trace |
| `orchestrator/passage_jev.py` | the Jev re-ranker |
| `orchestrator/jev_client.py` | the Jev API client |
| `orchestrator/nodes/synthesizer.py` | the answer writer: reads the top 8, citations by code |
| `config/statesync.toml` `[retrieval]`, `[library]` | every number above |
| `migrations/021_workspaces_rls.sql`, `024_library.sql` | the tables |
| `research/paper_b/scripts/` | `eval_writer_window.py`, `build_page_set.py`, `eval_jev_pages.py`, `eval_financebench.py` |
| `research/paper_b/results/` | `financebench_*`, `partd_writer_window.json`, `jev_pages_r1.json`, `paperb_docs_grades_*` |
| `research/archive/results/` | `retrieval_v2_report.md`, `rag_v3_report.md` |
