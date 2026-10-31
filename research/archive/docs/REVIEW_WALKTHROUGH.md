# StateSync review walkthrough

For the review on 15 October 2026. Read it top to bottom once tonight. Tomorrow, keep it open next to the
demo and follow it in order.

The guide is in eight parts:

1. The two-minute pitch (memorise it).
2. The checklist for the morning.
3. The demo, step by step, with what to click, what to say, and the question you will probably get.
4. Follow one question through the code: which file does what, with line numbers.
5. The concepts, explained from zero.
6. "Why this number?": every setting a teacher might ask about.
7. What we tried that did not work, and what is switched off.
8. Hard questions and short answers.
9. Everything else in the system: the parts outside the question pipeline.
10. Where the models are called, the scripts folder, and what is (and is not) new in the architecture.

Background reading, if you want more: [docs/paperb/PROJECT_GUIDE.md](paperb/PROJECT_GUIDE.md) (project
history, the paper, the experiments).

---

## 1. The two-minute pitch

> StateSync is an assistant that answers business questions in plain English from two kinds of source: a
> **database** (numbers, answered by writing SQL) and a **library of documents** (PDF reports, answered by
> searching and reading the right pages). Our test data is real: an SEC database of 23 standard yearly
> figures for 31 US companies, and 84 of those companies' annual and quarterly reports (about 45,000 passages).
>
> Every question goes through a pipeline:
> 1. A **router** decides which source can answer it.
> 2. For the database, an **SQL agent** writes a query, checks it, runs it read-only and fixes it if it fails.
> 3. For documents, a **two-round search** finds the right reports and then the right passages. A
>    **re-ranker** puts the best passages first.
> 4. An **answer writer** writes the answer and cites its sources.
>
> Our research question was whether a cheap new kind of model, a *typed decision model* called **Jev**, can
> make the small decisions in this pipeline instead of a large language model. It routes about as well as
> the LLM router, and it finds the right page more often than the re-ranker alone (53.3% against 43.7% on
> 300 new questions). That is the paper we submitted.
>
> We built a **Pipeline Inspector** so you can see every step of any answer.

---

## 2. Morning checklist (15 minutes before)

Run these in a terminal, in order.

1. **Docker Desktop is open**, and the containers are up:

   ```bash
   docker ps --format '{{.Names}} {{.Status}}'
   ```

   You need `statesync-sec-db`, `postgres`, `statesync-ollama-1` and `statesync-mcp-1`. If
   `statesync-sec-db` is missing:

   ```bash
   docker start statesync-sec-db
   ```

2. **Nothing heavy is running.** On 12 October the demo failed because evaluation scripts were using every
   CPU core.

   ```bash
   uptime
   ```

   The load average should be under about 10. If it isn't, find the heavy processes with:

   ```bash
   ps -axo pid,pcpu,etime,command | sort -k2 -nr | head -8
   ```

   Close the jobs you recognise. Don't close Docker.

3. **The re-ranker is running** (it runs on the Mac's GPU, outside Docker):

   ```bash
   curl -s 127.0.0.1:8096/health
   ```

   It should answer `{"ok": true, ...}`.

4. **Start the demo copy:**

   ```bash
   research/paperb/demo/demo_stack.sh up
   ```

5. **Warm it up.** Ask one throwaway question first, such as "What was Netflix's revenue in fiscal 2021?". The
   first question after a pause loads the models and can take a minute.

6. **Copy the sign-in token** (it is never printed):

   ```bash
   tr -d '\n' < /Users/neeljaiswal/StateSync/secrets/api_token_dev | pbcopy
   ```

7. Open **http://127.0.0.1:3090**, paste the token and sign in. Open the workspace
   **partd-routing-sec-filings**.

**Plan B.** If the live demo fails, open `docs/paperb/demo/index.html` in a browser (the Decision Lab). It
needs no server and shows the real experiment data.

After the review, stop the demo with:

```bash
research/paperb/demo/demo_stack.sh down
```

---

## 3. The demo, step by step (about 15 minutes)

The plan for each question is the same: ask it, read the answer, then click the **workflow icon** under the
answer ("Inspect: how this answer was produced") and walk through the cards.

### Step 0: the workspace and the database (2 min)

**Click:** workspace → **Schema** tab.

**Say:**
- "This is the database the assistant can use: 6 tables, read straight from the database's own catalog."
- "`annual_metrics` has one row per company, metric and year. `companies` lists the 31 companies. `facts` is
  the raw SEC data, 880,000 rows."
- "The key icon marks a column that points to another table."

**Then click:** the **Documents** tab.

**Say:** "These are the 84 annual and quarterly reports. Each was cut into passages of about 1,400
characters and turned into vectors for search."

**Likely question:** *How does the assistant know what is in the database?*
It reads the table and column names through the MCP service, and the router also gets a short "database
card": the tables and columns, plus a coverage line listing which companies and years the rows cover.

### Step 1: a database question (3 min)

**Ask:** "What was Netflix's revenue in fiscal 2021?" (expected about $29.7 billion)

**Open the Inspector and walk the cards:**

1. **Router card:** "Jev was asked one multiple-choice question: database only, documents only, both, or
   neither. It gives a probability for each; the highest one wins. No language model was called to route."
   Open the **database card** to show what it read.
2. **Database card:**
   - The schema diagram lights up the table the query read.
   - "The model first looked at a few sample rows of that table, so it writes the query against the real
     data."
   - "Every query passes three safety checks: it must parse as a single SELECT, it runs in a read-only
     transaction that is always rolled back, and the database login can only read."
   - Show **In plain words**: a reading of the SQL produced by code, not by a model.
3. **Answer writer card:** what the writer was given, what it cited, and whether every figure in the answer
   traces back to the result.
4. **Model steps card:** every model call, its tokens and its time.

**Likely question:** *What if the SQL is wrong?*
If the database returns an error, the agent gets the error, classified into a category such as an unknown
column or a type mismatch, plus a hint. It tries again, up to 3 more times. These attempts would appear as
red boxes in the database card.

### Step 2: a documents question (4 min). This is the main RAG demo.

**Ask:** "What does Boeing's 2022 10-K say about the main risks it faces?"

**Open the Inspector and walk the cards:**

1. **Router:** "Documents only" wins. Point at the best-matching documents it was shown.
2. **Documents search card:**
   - "**Round 1** searches the whole library and returns the best 8 passages. Their only job is to tell us
     *which reports* are relevant. Here, all of them are from Boeing's 2022 10-K."
   - "**Round 2** searches again *inside only those reports* and keeps up to 25 passages. That way, tables
     and figures that don't sound like the question are still found."
3. **Re-ranking card (the most important one):**
   - "Each row is one candidate passage, with its rank at each stage."
   - "**Dense search** compares meanings as vectors. It is fast but rough."
   - "The **re-ranker (bge)** reads the question and the passage together, with the report's title. It is
     slower but more accurate."
   - "**Jev** answers, for each passage: does it contain the information needed? We order by that
     probability."
   - "The **dashed line** is the cut. The answer writer only reads the top 8."
   - "The **Moved** column shows how far Jev moved each passage. Here it moved 3 passages into the top 8."
   - Click a row to show the passage text.
4. **Answer writer:** the passages it read, and the pages it cited.

**Likely question:** *Why not just give all 25 passages to the model?*
It costs about three times as many tokens, it is slower, and it adds noise. A model answers better from
8 good passages than from 25 mixed ones. What matters most is that the right page is in the top 8, which is
what the re-ranking does.

### Step 3: a question that needs both sources (3 min)

**Ask:** "Which company had the highest revenue in fiscal 2022, and how does that company describe its
e-commerce strategy in its 10-K?"

**Open the Inspector:**
- **Router:** "Both sources" wins.
- **Database card:** the query finds Walmart ($572.8 billion). The tables it read light up on the diagram.
- **Documents search card:** "The database result named Walmart, so the search went straight to Walmart's
  filings and skipped round 1. We call this *entity narrowing*."
- **Answer writer:** one answer with both kinds of citation, the query and the pages.

**Be honest if asked:** the library holds Walmart's 10-Ks for 2018–2020 only, so the strategy part is cited
from those, not from a 2022 filing.

**Avoid the dividends question** ("Among Coca-Cola, PepsiCo and Kraft Heinz…"). The database part is right:
Coca-Cola paid the most, $7.62 billion, against $6.17 billion for PepsiCo. But the result names all three
companies, so the search is narrowed to all three, and 7 of the 8 passages the writer read were PepsiCo's.
The answer then talks about Coca-Cola while citing PepsiCo pages. If a teacher asks about it, that is the
honest explanation: narrowing uses every company in the result, not only the top one. (An earlier note in
our guides said PepsiCo paid the most; that was wrong.)

### Step 4: a question nobody can answer (1 min)

**Ask:** "What was Tesla's revenue in fiscal 2022?"

**Say:**
- "Tesla is in neither source. The database card lists which companies the rows cover, so the router chose
  *neither*, and the assistant says so instead of inventing an answer."
- "Before we added that coverage line, every router, Jev and the LLM alike, sent such questions to the
  database. That was one of our findings."

### Step 5: a follow-up (1 min, optional)

**Ask:** "What was Netflix's revenue in fiscal 2021?", then "And in 2022?"

**Say:** "The second question has no company in it. Simple rules carry 'Netflix' over from the previous
turn. There is no extra model call for this."

---

## 4. Follow one question through the code

Read this as a map: question in at the top, answer out at the bottom. Line numbers are as of 14 October.

```
Browser (web/)                              React app: chat, Inspector, Schema tab
  │  POST /query
  ▼
orchestrator/main.py                        the HTTP API: sign-in check, runs the graph, builds the response
  │
orchestrator/graph.py:340   build_graph     the pipeline as a graph (LangGraph): nodes and the edges between them
  │
  ├─ orchestrator/front_door.py             ROUTER
  │     :376  gather_evidence               database card + best-matching documents + recent turns (no model)
  │     :264  format_card                   writes the database card
  │     orchestrator/card_coverage.py:86    the coverage line (which companies and years the rows hold)
  │     :723  decide_jev_choice             one Jev choice question → sql / docs / both / neither
  │     :455  decide_llm                    the LLM router (used if Jev fails)
  │     orchestrator/jev_client.py          the one HTTP call to Jev
  │     orchestrator/context_carry.py       follow-up questions without a model call
  │
  ├─ orchestrator/nodes/sql_agent.py:626    DATABASE route: the SQL agent
  │     reads the schema → drafts SQL → samples rows from the tables the draft uses → writes the final SQL
  │     → runs it → on failure classifies the error and loops back (graph.py:141 decides retry or stop)
  │     orchestrator/classifiers/error_classifier.py:72   error categories and hints
  │     mcp/tools/data_tools.py:393 run_select            runs the SQL; :128 _check_layer1 = the SELECT-only gate
  │
  ├─ orchestrator/nodes/unstructured_knowledge.py   DOCUMENTS route
  │     :118  _semantic_search_expanded     round 1 (which reports) + round 2 (passages inside them)
  │     :82   order_passages                which 8 the writer reads (by relevance)
  │     orchestrator/passage_jev.py:42      Jev scores each passage and re-orders them
  │     mcp/tools/vector_tools.py:256       the vector search itself (pgvector) + calls the re-ranker
  │     mcp/embeddings.py:46                turns text into a vector (Ollama, mxbai-embed-large)
  │     reranker/server.py                  the bge cross-encoder (runs on the Mac GPU)
  │
  ├─ unstructured_knowledge.py:518 / :546   BOTH: split the question; SQL half first; documents half narrowed
  │
  └─ orchestrator/nodes/synthesizer.py:561  ANSWER WRITER: one LLM call; citations built in code
        orchestrator/number_grounding.py:176   checks every figure in the answer against the data
        orchestrator/evidence.py:24             how much to trust the answer (verified / checked / unchecked)
```

**Where the documents come in** (once, at upload time):
- `parser/`: an isolated service turns the PDF into text, page by page.
- `orchestrator/library_ingest.py:149` `chunk_pages`: cuts the text into passages.
- `mcp/embeddings.py`: makes one vector per passage, stored in Postgres with pgvector.

**Where the switches are:**
- `config/flags.toml`: on/off for every feature.
- `config/statesync.toml`: numbers and limits.
- The demo uses its own copies in `research/paperb/demo/`, so the shared app is untouched.

**The Inspector:** `web/src/features/inspect/` for the page, `orchestrator/inspect_api.py` for the schema
endpoint.

---

## 5. The concepts, from zero

**RAG (retrieval-augmented generation).** A language model does not know your documents. RAG means:
1. *Find* the passages that matter.
2. *Give* them to the model.
3. Let it *write* the answer from them.

The answer is then based on your documents, and you can cite the pages.

**Embedding.** A model turns a piece of text into a list of numbers (for us, 1,024 numbers) so that texts with
similar meaning get similar lists. "Revenue fell" and "sales declined" end up close together even though they
share no words.

**Vector search (dense search).** Embed the question, then find the passages whose vectors are closest to it.
"Closest" is measured by **cosine similarity**, the angle between two vectors: 1 means the same direction, 0
means unrelated. It is fast, because every passage was embedded in advance.

**Chunking.** A 300-page report is too long to embed or to give to a model, so we cut it into passages
("chunks") of about 1,400 characters. Each chunk overlaps the next by 200 characters, so a sentence on the
boundary appears whole in at least one of them. A chunk never crosses a page, so every chunk has a page
number to cite.

**Top-k.** How many results to keep: the "top 8", the "top 25".

**Two-round search.** One search over 45,000 passages often brings back the right *report* but not the right
*page*. This is especially true for tables, which don't read like a question. So:
- Round 1 finds the reports.
- Round 2 searches again inside only those reports.

**Bi-encoder vs cross-encoder (why re-rank at all).**
- An embedding model is a *bi-encoder*: it encodes the question and the passage *separately* and compares the
  results. It is fast, because passages are embedded once, but it never sees them side by side.
- A *cross-encoder* (our re-ranker, bge-reranker-v2-m3) reads the question and the passage *together* and
  scores how well they match. It is much more accurate, but too slow to run on 45,000 passages.

So: search fast with the bi-encoder, then re-score the top 30 with the cross-encoder. On FinanceBench this
raised the share of questions with the right page in the top 5 from 43.3% to 59.3%.

**Jev (typed decision model).** A model that does not write text. You give it some text (the "state") and a
declared question (yes/no, or a choice among named options), and it returns probabilities. We use it twice:
- **Routing:** "which sources are needed: database, documents, both, neither?"
- **Re-ranking:** "does this passage contain the information needed to answer the question?"

It costs about $0.0009 to score 25 passages, and it takes 1–3 seconds.

**Text-to-SQL.** Turning a question into an SQL query. Our agent does it in steps:
1. Read the schema.
2. Draft a query.
3. Look at a few real rows of the tables the draft uses. This is called *interrogation*, an idea from the
   APEX-SQL paper; it catches wrong value formats like "PepsiCo" vs "PEP".
4. Write the final query.
5. Run it.
6. If it fails: classify the error, add a hint, and try again (from the SQL-of-Thought paper's idea of
   guided correction).

**The three-layer safety gate.** A model writes our SQL, so we never trust it:
1. A real PostgreSQL parser (pglast) rejects anything that is not a single SELECT.
2. The query runs in a read-only transaction that is always rolled back, with a 15-second timeout.
3. The database login itself can only SELECT, so even a write that slipped through would fail.

The service that writes SQL (the orchestrator) has no database password at all. Only the MCP service does.

**Router.** Decides the route before any expensive work, so a document question doesn't waste an SQL attempt
and vice versa. "Neither" lets the assistant decline instead of making something up.

**LangGraph.** A library for building the pipeline as a graph: steps are nodes, arrows are edges, and some
arrows depend on the result (for example "SQL failed → go back and retry"). All steps share one typed record
of the question (the "state").

**MCP (Model Context Protocol) pattern.** One service, `mcp/`, holds the database access and offers fixed
"tools" (run_select, semantic_search, describe_table and so on). The AI side can only call those tools.

**Number grounding.** After the answer is written, code checks every number in the text against the query
result or a cited passage. Any number it can't trace is flagged.

**Evidence tiers.** A label on each answer:
- *Verified:* an approved query was run as is.
- *Checked:* the query ran and passed the checks.
- *Unchecked:* anything else.

---

## 6. "Why this number?"

All values are in `config/statesync.toml` unless marked otherwise.

| Setting | Value | Why (and how honest to be) |
|---|---|---|
| Embedding model | `mxbai-embed-large`, 1,024 dimensions | Runs locally in Ollama: free, and documents never leave the machine. Strong for its size. We tested adding a query instruction; it made results worse (hit@1 93% → 80%), so we don't use one. |
| Chunk size | 1,400 characters, 200 overlap | About 300–350 tokens, comfortably under the embedding model's ~512-token limit, so nothing is cut off when it is embedded. Big enough for a paragraph or a small table. The overlap keeps boundary sentences whole. Never across pages, so every chunk has a page to cite. |
| Round 1 top-k | 8 | Enough passages to identify the right reports. Round 1's only job is "which reports". |
| Round 2 top-k | up to 25 | Enough candidates for the re-ranker to choose from. 25 is the search tool's upper bound. |
| Re-ranker candidates | 30 | The cross-encoder takes about 2–6 seconds for 30 on the Mac GPU. Fixed before measuring, not tuned. |
| Passages the writer reads | 8 | A context budget: about 3,000 tokens of passages. Set in the original design, not tuned. Our tests showed that *which* 8 matters more than how many: the right page is among the 25 for about 72% of questions, and in our best ordering among the top 8 for about 70%. |
| Exact search instead of an index | `exact_vector_search = true` | The fast approximate index (HNSW) silently missed the best match in a test. An exact scan over all passages still takes well under a second at our size, so we chose correct over fast. |
| SQL retries | 3 (4 attempts total) | Enough to fix typical errors (wrong column name, type mismatch); more rarely helps and costs calls. |
| Statement timeout | 15 seconds | Stops a runaway query; our queries normally take milliseconds. |
| Row cap | 1,000 rows (hard limit 5,000) | The writer only needs a summary; large results are slow and cost tokens. |
| Sample rows before writing SQL | up to 4 tables, 5 rows each | Enough to see real values and formats without a big prompt. |
| Database card | up to 4,000 characters | Room for the tables, columns and coverage line without a huge prompt. |
| Model temperature | 0.0 | Same question → same SQL, which makes results reproducible. |
| Jev requests at once | 8 | The API refused 25 at once (HTTP 429, "too many requests"). |
| Jev passage text | first 3,000 characters | Long enough for a full passage; the same setting as our experiment. |

---

## 7. What we tried that did not work, and what is switched off

Saying these out loud shows you know the project. The switches are in `config/flags.toml`.

| Idea | Status | Why |
|---|---|---|
| Keyword search (BM25) fused with vector search | Built, **off** | It helped on ID-style questions in our small test set, but hurt paraphrased questions (hit@1 60% → 40%), and the decision was made on the same set we tuned on. It needs a fresh test before turning on. |
| Layout-aware chunking (headings, whole tables) | Built, **off** | A small gain on our own set; not tested on the real filings yet. |
| "Abstain if nothing relevant found" | Built, **off** | It caught only 1 of 10 unanswerable questions. |
| Memory of past queries | Built, **off** | It saved tokens but lowered accuracy (10/10 → 7/10). |
| A bigger local model (14B instead of 7B) | Rejected | No accuracy gain, three times slower. |
| Jev choosing between SQL queries | Tested, **not used** | Jev is good at spotting a wrong-looking result but poor at choosing between two plausible queries. |
| Routing as two yes/no questions to Jev | Tested, **not used** | 75.6% correct against 88.3% for one multiple-choice question. |

---

## 8. Hard questions and short answers

**"What exactly did *you* build, and what did you reuse?"**
- **Built:** the whole pipeline and its code, the safety gate, the router logic, the two-round search, entity
  narrowing, follow-up handling, the coverage line, the Jev integration, the web app and the Inspector, the
  SEC database, every test set and experiment.
- **Reused:** the models (embedding, re-ranker, LLMs, Jev), the libraries (LangGraph, FastAPI, Postgres,
  pgvector), and the public data (SEC filings, FinanceBench, BIRD).

**"Why two rounds of search instead of one?"** One round over 45,000 passages tends to find the right report
but the wrong page, especially for tables. Round 2 searches inside the right reports.

**"Why re-rank if you already have vector search?"** Vector search never reads the question and passage
together. The cross-encoder does, which is more accurate. Measured: right page in the top 5 went from 43% to
59%.

**"Why add Jev on top of the re-ranker?"** In our test on 300 new questions it put the right page first 53.3%
of the time against 43.7% for the re-ranker alone, a significant difference. It is cheap and fast.

**"How do you know those numbers are real?"** The test questions were built after the system was frozen, and
the hypotheses and pass/fail rules were written down in git before the data existed (pre-registration). The
statistics test whether a difference could be chance (McNemar test, confidence intervals).

**"Is Jev better than the LLM router?"** 88.3% against 84.4%, but the difference is not statistically
significant, so we say comparable. And the LLM router more often reaches every source a question needs
(94.4% against 88.9%), so it's a trade-off.

**"What stops the model from deleting data?"** Three layers: the parser allows only a single SELECT, the
transaction is read-only and rolled back, and the database login can only read. The AI side has no database
password.

**"What happens if a service is down?"**
- If the re-ranker is down, search keeps the plain order.
- If Jev fails, the LLM router decides.
- If a Jev passage score fails, that passage keeps its place.
- If the embedding service is too slow, the question fails with "service unavailable". That is what happened
  on 12 October, when the Mac was overloaded.

**"Why is it slow?"** The re-ranker and the models run on a laptop. A documents answer takes about 20–60
seconds, mostly re-ranking and the answer writer. The Inspector shows the time of each step.

**"What would you improve next?"**
- Test whether better passage order gives better final answers (not measured yet).
- Re-test keyword search on fresh questions.
- State the coverage line's year range from the table actually used.
- Have a person check a sample of the generated test questions.

**"Where is X in the code?"** Use the map in part 4.

---

## 9. Everything else in the system

Parts 3–8 follow one question. These are the other parts, which a teacher may see in the app or ask about.

### Which model does what

The roles are set in `config/flags.toml` under `[llm.models.openai_compat]`. The demo reaches the models
through an OpenAI-compatible API provider.

| Role | Model | Used for |
|---|---|---|
| Router | Jev (`jev-1.13`) | The route, one multiple-choice question. The LLM router (Qwen 3.8 27B) is used only if Jev fails. |
| SQL writer | gpt-oss-120b | Drafting and writing the SQL, and fixing it after an error |
| Question split | Qwen 3.8 27B | Splitting a two-source question into a database part and a documents part |
| Answer writer | gpt-oss-120b | The final answer, and a suggested chart |
| Passage scoring | Jev | "Does this passage contain what is needed?" |
| Embeddings | mxbai-embed-large (local, Ollama) | Vectors for search |
| Re-ranker | bge-reranker-v2-m3 (local, Mac GPU) | Re-scoring the top 30 passages |

**Why two LLM sizes:** the parts the user reads (SQL, answer) get the strongest model; short classification
jobs get a smaller, faster one.

**Why the provider is swappable:** `orchestrator/llm/` is a "provider seam". Groq, OpenRouter, Gemini, Azure,
Anthropic or a local Ollama model can be switched in the settings without changing code. Every call records
which model actually answered, and a different one is refused.

### The services (Docker containers)

| Container | Job |
|---|---|
| `frontend` | The React web app, served by nginx with strict security headers |
| `orchestrator` | The pipeline and the API (FastAPI + LangGraph). Holds the AI keys, **no** database password. |
| `mcp` | The only service with database access: the SQL gate, vector search, schema reading, history |
| `postgres` | The database, with **pgvector** (vectors) and **PostGIS** (maps) |
| `ollama` | Runs the embedding model locally |
| `reranker` | The cross-encoder. The demo uses the copy running natively on the Mac GPU, because Docker on a Mac can't use the GPU. |
| `parser` | Turns uploaded PDFs into text. It has no network and no passwords, and each file is parsed in its own limited process, so a hostile PDF can only crash itself. |
| `simulator` | Keeps the `live` sample dataset changing (new orders every few seconds) for the live dashboards |
| `qdrant` | A vector store for the "memory" feature, which is switched off |
| `evaluator` | Re-runs a test set every hour. Kept off, because it spends model quota. |

The containers sit on separate internal networks, so the orchestrator cannot reach the database directly even
if someone tried.

### Workspaces (the setup a business owner does)

Code: `web/src/features/workspaces/`, `orchestrator/workspaces_api.py`, `mcp/tools/workspace_tools.py`.

- **Business tab:** the owner describes the business in a few lines. The SQL writer reads it as background,
  marked as the owner's words, never as instructions.
- **Knowledge tab:**
  - An AI drafts a description of each table and column. The owner edits and approves it, and only approved
    text is used.
  - **Definitions** ("active seller", "late order") can be proposed from uploaded documents. A definition is
    kept only if its quoted sentence really is in the document; invented ones are dropped.
- **Approved examples:** a thumbs-up turns an answer into an approved example. Asking the same question
  again runs the approved SQL directly, and the answer is marked **Verified**.
- **Test tab:** runs a set of questions and lets the owner mark each answer right or wrong. Approved examples
  are re-checked against the database regularly, so a change in the data is noticed.
- **Documents tab:** which library folders this workspace searches.
- **Schema tab:** the database diagram (new).

### The library and your own database

Code: `web/src/features/library/`, `orchestrator/library_api.py`, `mcp/connections.py`.

- **Documents:** folders of PDFs. Uploads are limited to 20 MiB and 600 pages, checked in the orchestrator
  and again in the parser. A document goes "processing", then "ready".
- **Connect your own Postgres:** StateSync only saves a login it has proved is **read-only**; a superuser
  login is refused. The password is encrypted (Fernet) and only the MCP service can decrypt it.

### Dashboards

Code: `web/src/features/dashboard/`, `orchestrator/dashboard_builder.py`, `orchestrator/tiles_api.py`.

- **Pin** any answer as a dashboard card ("tile"). A tile stores the **SQL**, not the question, so refreshing
  it re-runs one SELECT with **no model call**. Tiles on the `live` dataset refresh every 30 seconds; others
  hourly by default.
- **Build with AI** plans a whole dashboard: it picks 4–8 insights, asks each through the same checked
  pipeline, and pins them.
- **Charts** are Vega-Lite specifications (a declarative chart format). The model only *proposes* the chart
  shape; the real rows are attached in code, and the result is checked against a fixed schema before it
  is shown. No model-written code ever runs in the browser.

### Trust features on every SQL answer

- **"How I got this":** a plain-English reading of the SQL, generated by code from the parsed query (not by a
  model).
- **Figure grounding:** hover a number in the answer to see which row and column it came from.
- **Evidence tier:** Verified, Checked or Unchecked (see part 5).
- **Plain failures:** a failed query or an empty result is explained in plain words instead of an error code.
- **Route recovery:** if the database can't answer and the workspace has documents, the documents route runs
  once instead.

### Security, in one list

- Sign-in uses a token. The server stores only a hash of it, and the browser keeps an HttpOnly session cookie.
- A rate limit of 30 model requests per minute per user.
- An audit log of every request, including refusals.
- Row-level security: each account sees only its own workspaces, conversations and documents.
- Strict browser security headers (CSP): only the app's own scripts run.
- The three-layer SQL gate, and no database password on the AI side.

### Other built-in features

- **Multilingual questions:** a language check step can translate a non-English question first, keeping
  names exactly as written. In the demo the router handles English directly.
- **Built-in sample datasets:** `live` (a simulated online shop), `olist` (real Brazilian e-commerce data,
  1.5 million rows) and `public` (a small synthetic one). They come from the earlier product phase; the
  review demo uses the SEC workspace.
- **Conversation history:** past conversations are listed on the Ask page and can be resumed.

### What only exists for the research

These are not part of the app a user sees:
- The ModernBERT and Laya checkers and the LLM judge.
- Self-consistency (re-asking the SQL writer 5 times and comparing results).
- The question-set builders and experiment scripts in `scripts/`.
- The Decision Lab page in `docs/paperb/demo/`.

### Tests

About 1,280 Python tests (`tests/`) and 74 web tests (`web/src/**/*.test.ts`) run on every change. There
are also security tests (`scripts/verify_security.sh`) that try to break the SQL gate, for example with a
hidden write inside a query or a statement that reads another account's data.

---

## 10. Where the models are called, the scripts folder, and what is new

### How Jev and the re-ranker work together

Jev does not need the re-ranker; they are two separate switches in `config/flags.toml`:

- `pipeline.rerank = "bge"` turns the cross-encoder on.
- `pipeline.passage_rerank = "jev"` turns Jev's ordering on.

**In the demo both are on, in this order:**
1. Round 2 fetches 30 candidates by dense search.
2. bge re-scores them and keeps the top 25.
3. Jev scores each of those 25 on its own and re-sorts them by its probability.

So bge decides *which* 25 passages Jev sees, and breaks ties; Jev decides the final order. Jev never sees
bge's scores.

**With bge off,** Jev re-orders the plain dense list instead. In our test this alone put the right page first
for 49.3% of questions, against 20.3% for dense search and 43.7% for bge, and 53.3% with both. The gap
between Jev alone and bge alone was not significant after correction (p = 0.053). We used both because
together they did best.

### Every place a model is called

The LLMs are always reached through one function, `get_agent_llm(role)` in `orchestrator/llm/provider.py:62`.
It picks the model for that role from the settings; `orchestrator/llm/adapters/` has one adapter per
provider; `orchestrator/llm/recording.py` records every call (tokens, time, served model).

| Where | Role | What it asks |
|---|---|---|
| `orchestrator/front_door.py:457` | supervisor | The LLM router (when Jev is off or fails) |
| `orchestrator/nodes/sql_agent.py:770` | sql | The draft query |
| `orchestrator/nodes/sql_agent.py:918` | sql | The final query, and each fix after an error |
| `orchestrator/nodes/sql_agent.py:604` | decomposer | Fill an approved query template (switch `templates`, off) |
| `orchestrator/nodes/sql_agent.py:1108` | sql | Self-consistency re-samples (research only, off) |
| `orchestrator/nodes/sql_agent.py:1207` | memory_summarizer | Memory (off) |
| `orchestrator/nodes/unstructured_knowledge.py:415` | decomposer | Split a two-source question |
| `orchestrator/nodes/synthesizer.py:699` | synthesizer | Write the answer |
| `orchestrator/nodes/supervisor.py:152` | supervisor | The old router (used only when the front door is off) |
| `orchestrator/nodes/rewrite.py:227` | rewriter | The old follow-up rewriter (front door off) |
| `orchestrator/language_normalizer.py:129` | normalizer | Translate a non-English question |
| `orchestrator/query_rewrite.py:43` | normalizer | Rewrite the search query (switch `query_rewrite`, off) |
| `orchestrator/workspaces_api.py:289` | decomposer | Draft table and column descriptions (Knowledge tab) |
| `orchestrator/builder_api.py:124` | decomposer | Plan a dashboard (Build with AI) |

**Jev** is called only through `orchestrator/jev_client.py`:
- `orchestrator/front_door.py:731`: routing in the demo (one choice question).
- `orchestrator/front_door.py:545`, `:569`, `:662`: the other Jev router variants we tested.
- `orchestrator/passage_jev.py:51`: passage scoring.

**The embedding model** is called in `mcp/embeddings.py:46`, from the search in `mcp/tools/vector_tools.py:330`
and at upload time.

**The re-ranker** is called in `mcp/tools/vector_tools.py:307` (an HTTP call to `reranker/server.py`).

**In the demo, one documents question makes:**
- 1 Jev routing call;
- 2 embedding calls (the question, once per round);
- 2 re-ranker calls;
- 25 Jev passage calls;
- 1 LLM call for the answer.

### The `scripts/` folder (116 files)

None of these run inside the app. They are tools we run by hand.

| Prefix | What they are | Examples |
|---|---|---|
| `eval_` | Run an evaluation against the running system and save the results | `eval_routing.py`, `eval_jev_pages.py`, `eval_bird.py`, `eval_financebench.py` |
| `build_` | Build a test question set, once, before the evaluation runs | `build_routing_set_r2.py`, `build_page_set.py` |
| `paperb_` | The paper's analysis: candidates, checkers, statistics, and `paperb_numbers.py`, which writes every number in the paper | `paperb_analyze.py`, `paperb_numbers.py` |
| `verify_` | Check that a running deployment behaves (security, tenancy, workspaces) | `verify_security.sh`, `verify_workspaces.py` |
| `bench_`, `check_` | Older benchmarks and checks from the product phases | `bench_multiturn.py` |
| `gen_`, `fetch_` | Generate fixtures or download public data | `fetch_external_eval.sh` |
| Others | Utilities | `run_reranker_host.sh` (starts the GPU re-ranker), `shipped_flags.py` (checks the switches are at their shipped values), `migrate.sh` (database migrations), `gen_api_principal.py` (creates a sign-in token) |

### Is the architecture standard, or new?

**Mostly standard, by design.** Each building block is a well-known pattern, and saying so honestly is
stronger than overclaiming:
- RAG with dense search plus a cross-encoder re-ranker;
- a text-to-SQL agent that samples data and corrects itself;
- an LLM router in front of specialised routes;
- a graph-based pipeline (LangGraph).

**Two-round search** is a known idea too: find the document first, then the passages inside it
(hierarchical, or "parent-document", retrieval).

**What we did differently**, described as engineering choices and small findings rather than new
inventions:

1. **The model never touches the database directly.** The model-facing service holds no database password.
   Every query goes through one tool service with three independent read-only layers, on separate
   networks. Standard security thinking, applied more strictly than most LLM demos.
2. **Checks done by code, not by another model:**
   - "How I got this" is generated from the parsed SQL.
   - Every figure in the answer is traced to a result cell.
   - Definitions are kept only if their quote is really in the document.
   - Evidence tiers label how much to trust each answer.
3. **Entity narrowing between the two sources.** In a two-source question, the database result decides
   which documents are searched (the company names in the result are matched to report titles). Related to
   Text2VectorSQL's idea of using SQL to narrow a vector search, applied across a database and a document
   library.
4. **The coverage line.** The router is told not only the tables and columns but which companies and years
   the rows hold, built with read-only queries and no model call. It fixed unknown-company routing for both
   the LLM and Jev routers; this is one of the paper's findings.
5. **Follow-ups without a model call:** simple rules carry the company and year from the previous turn.
6. **Dashboards store SQL, not questions:** a tile refreshes with no model call, so it is cheap and always
   gives the same kind of answer.
7. **Every feature is a switch,** measured before it was turned on. Several were measured, found not to
   help, and left off (part 7).

**The honest one-line answer:** "The architecture uses standard components. Our contribution is how we
combined them safely, a few code-based checks and links between the sources, and above all that we measured
each decision on fresh data. The research part is the typed decision model."

