# StateSync and Paper B: a plain-language guide

Written 9 October 2026 for the mentor meeting. The goal is to understand the project well enough to answer
any question about it. It covers what the system is, what changed since the last demo, how the new
documents engine works, how to show it, where everything lives, and what the paper claims and why.

You do not need to read code to follow this. File paths are given so you can point at things if asked.

---

## 1. The project in 30 seconds

**StateSync** is an assistant that answers business questions in plain English from two kinds of source:

- a **database** (tables of numbers, queried with SQL), and
- a **library of documents** (PDFs such as company annual reports).

You ask "What was Netflix's revenue in 2021?" and it writes a safe, read-only SQL query, runs it and
answers. Ask "What does Boeing's 2022 annual report say about its main risks?" and it searches the PDFs,
finds the right pages and answers with citations. Ask something that needs both, and it does both.

Every question involves a handful of small **decisions**:

1. Which source should answer it: database, documents, both, or neither?
2. Which pages of a 300-page filing actually hold the answer?
3. Can this database answer the question at all?
4. Is the SQL the model just wrote wrong?

Normally each decision is one more call to a large language model (LLM), which is slow and costly. **Paper B**
asks whether a new, cheap kind of model, a *typed decision model* called **Jev**, can make these decisions
instead. It answers yes/no or multiple-choice questions with probabilities, and nothing else.

**The answer, briefly:**

- **Page finding and routing:** Jev is as good as the LLM or better. It improved page finding significantly.
- **Answerability:** Jev is very good at it.
- **Choosing between candidate SQL queries:** Jev is poor at this.

The paper explains where the line falls.

---

## 2. What changed since the last demo (30 Sept → 9 Oct)

At the last demo (30 Sept, guide in `docs/DEMO.md`) StateSync was a **product demo**:

- workspaces, a document library, connecting your own Postgres, dashboards that build themselves;
- e-commerce data (Olist) and three small made-up policy PDFs.

The documents part was simple: embed the question, take the 8 closest passages, and write an answer.

Since then the work moved in three steps.

| When | What happened | Why |
|---|---|---|
| 1 Oct | **RAG v2.** Built a frozen 110-question retrieval test. Tried layout-aware chunking, keyword (BM25) search fused with vector search, scoping and abstention. All built, measured, and shipped switched off. | You asked for a state-of-the-art RAG pipeline after the mentor review. |
| 2–3 Oct | **Real-world data.** Downloaded FinanceBench (150 questions over 84 real SEC annual reports) and built an SEC figures database for the same companies. The product initially couldn't even ingest most real filings, so that was fixed. | Our own demo data was too small and too easy; results had to hold on real documents. |
| 3 Oct | **RAG v3: a re-ranker.** A cross-encoder (bge-reranker-v2-m3) re-scores the search results, running on the Mac's GPU. **Page hit@5 rose from 43.3% to 59.3% on FinanceBench** (p = 0.0002). Shipped on. | The biggest single retrieval gain we measured. |
| 4–5 Oct | **Paper A finished** (a study of warning signals for wrong SQL on the BIRD benchmark). | Separate paper. Paper B cites it. |
| 5–6 Oct | **The documents engine rebuilt ("Part D v3").** End-to-end test on FinanceBench: the old system answered **20.0%** correctly. The rebuilt one answered **52.7%** without the re-ranker and **63.3%** with it. | Running real questions end to end showed where answers were lost: routing, which passages the writer reads, and wrong-year filings. |
| 6 Oct | **Jev enters.** The router can be Jev instead of an LLM. Added multi-turn follow-ups with no extra model call, and narrowing a two-source question to the companies the database returned. | Paper B's idea: replace LLM decision calls with a cheap decision model. |
| 7–8 Oct | **Audit and clean re-runs.** Found two unfair tests, threw them out and ran four fresh **pre-registered** experiments (R1–R4) on new data. Paper B was rewritten around them. | A credible paper needs fair baselines and hypotheses fixed before the data exists. |
| 8 Oct | **Demo of the evaluated architecture**, plus Jev passage re-ranking live in the product, plus an interactive results page (the "Decision Lab"). | For this meeting. |

ONDC and e-commerce material is deliberately kept out of Paper B's system, experiments and text. It still
exists in the product, but it is not what this paper is about.

---

## 3. Words you will need

| Term | Plain meaning |
|---|---|
| **LLM** | A large language model that writes text (here: gpt-oss-120b, Qwen 3.8 27B, GLM-5.3-Flash). |
| **RAG** | Retrieval-Augmented Generation: find relevant passages first, then let the LLM write an answer from them. |
| **Embedding / dense search** | Turn text into a list of numbers (a vector) so that similar meanings land close together. Search compares the question's vector with every passage's vector. We use `mxbai-embed-large`. |
| **BM25 / keyword search** | Classic search engine scoring by shared words, weighting rare words more. |
| **Cross-encoder / re-ranker** | A model that reads the question and a passage *together* and scores how well they match. More accurate than embeddings but slower, so it re-scores only the top ~30 results. We use `bge-reranker-v2-m3`. |
| **Router / front door** | The step that decides where a question goes: `sql`, `docs`, `hybrid` (both) or `neither`. |
| **Typed decision model** | A model that doesn't write text. You give it a "state" (some text) and declared questions, and it returns probabilities (e.g. P(yes) = 0.83, or {sql: 0.7, docs: 0.2, …}). |
| **Jev** | TypeSafe's typed decision model (`jev-1.13`). We call it through OpenCode Zen at $0.042 per million input tokens, about 100× cheaper than a typical LLM call. |
| **Database card** | A short text description of the database (its tables and columns) that the router reads. |
| **Coverage line** | A new line on the card listing what the rows actually cover (which companies, which years). |
| **page@1** | The share of questions where the correct page was ranked first. |
| **AUROC** | How well a score separates good from bad. 0.5 is a coin flip; 1.0 is perfect. |
| **Pre-registration** | Writing down the hypotheses, data, tests and pass/fail thresholds in git **before** the data exists, so we can't move the goalposts. |
| **McNemar test** | The standard test for "is system A significantly better than system B on the *same* questions?" |
| **Held-out** | Data never looked at while building or tuning the system. |

---

## 4. The big picture: what runs where

StateSync is a set of Docker containers on your Mac. Each has one job, and only one of them can touch the
database.

```
  Browser (React web app)
      │  sign-in token
      ▼
  orchestrator  ── the "brain": the question pipeline (LangGraph), the router, calls to the LLMs and to Jev.
      │             Holds the AI keys but NO database password.
      │  JSON-RPC
      ▼
  mcp           ── the only service that holds database credentials. Runs every SQL query through a
      │             three-layer read-only gate and runs document search.
      ▼
  postgres (+ pgvector for embeddings)    ollama (embeddings)    reranker (bge, on the Mac GPU)
  parser (turns uploaded PDFs into text; isolated, no network)
```

The security story is unchanged from the last demo, and the mentor may still ask about it. The AI side never
holds a database password. Every query passes three layers:

1. A real PostgreSQL parser rejects anything but a single SELECT.
2. The query runs in a read-only transaction that is always rolled back.
3. The database login has SELECT permission only.

For Paper B there are also two extra databases running in their own containers:

- **SEC figures database** (`statesync-sec-db`): 23 standard annual figures (revenue, net income,
  dividends …) for 31 US companies, taken from the companies' official SEC XBRL filings.
- **BIRD databases** (`statesync-bird-db`): the public text-to-SQL benchmark, used for the SQL experiments.

The **test workspace** is `partd-routing-sec-filings`. It joins the SEC database with the **84 FinanceBench
filings**: about 44,918 passages from real 10-K/10-Q reports.

---

## 5. The new documents engine (RAG), step by step

This is the main thing to explain. Follow one question: *"What does Boeing's 2022 10-K say about the main
risks it faces?"*

### Step 0. Ingestion (done once, when the PDFs are uploaded)
- The **parser** extracts text page by page (pypdf).
- The text is split into **passages** of about 1,400 characters, with 200 characters of overlap. A passage
  never crosses a page boundary, so every passage has a page number to cite.
- Each passage is embedded with `mxbai-embed-large` (1,024 numbers, computed locally by Ollama) and stored in
  Postgres with **pgvector**.
- *Why this way:* everything stays on your machine, and one database holds both data and vectors (one
  security model, no extra vector database).

### Step 1. The front door decides the route
- *Old:* three LLM calls (language check, follow-up rewriter, router), and the router saw only the 20 newest
  document titles and nothing about the database. With 84 filings, 45% of document questions were sent
  to SQL by mistake.
- *New:* one **front door** gathers evidence with no model call:
  - the **database card** (tables and columns), now with a **coverage line** saying which companies and
    years the rows hold;
  - the **5 best-matching documents** for the question;
  - the **last few turns** of the conversation.
- It then asks **Jev one multiple-choice question**: which sources are needed: *only the database*, *only the
  documents*, *both*, or *neither*? Each option has a one-sentence description (wording "v2", in
  `research/paperb/statesync_jev_choice_v2.toml`).
- No LLM call is made for routing. If Jev fails, it falls back to the LLM front door.
- *Code:* `orchestrator/front_door.py`, `orchestrator/jev_client.py`, `orchestrator/card_coverage.py`.
- *Why one choice and not two yes/no questions:* we tested both. As two questions ("needs the database?",
  "needs the documents?") Jev routed 75.6% correctly; as one four-way choice, 88.3%. That is one of the
  paper's findings (section 8).
- *Why the coverage line:* without it, a router can't tell "Tesla's revenue" (Tesla isn't in our
  database) from "Netflix's revenue" (it is). Both look like database questions. With the line, the routers
  correctly declined 26/30 (Jev) and 29/30 (LLM) questions about unknown companies, up from 0/30.

### Step 2. Two-round search
- **Round 1:** dense search over all passages finds which **documents** are relevant (top 8 passages, then
  their documents).
- **Round 2:** search again **inside only those documents**, up to 25 passages.
- *Why:* a table passage ("Revenue | 2022 | 2021 …") doesn't look like a question in vector space, but it
  sits in the right document. Searching inside the right documents finds it.
- *Code:* `orchestrator/nodes/unstructured_knowledge.py` (the "Unstructured Knowledge Engine"); the search
  itself is `mcp/tools/vector_tools.py`.

### Step 3. Re-ranking with the cross-encoder (bge)
- The search's top ~30 candidates go to the **re-ranker**, `bge-reranker-v2-m3` (from BAAI, the "BGE M3"
  family). It reads the question together with each passage **and its filing title** (e.g. "BOEING_2022_10K"),
  so it also knows the company and year.
- It runs natively on the Mac GPU (Docker on a Mac can't use the GPU), on 127.0.0.1:8096, as a macOS login
  service. If it is down, search silently keeps the plain order.
- *Code:* `reranker/server.py`, started by `scripts/run_reranker_host.sh`.
- *Why:* +16 points page hit@5 on FinanceBench, the largest gain we measured. Reading titles fixes
  "right company, wrong year".

### Step 4. Jev re-orders the passages (new, live in the demo)
- Each of the 25 passages is sent to Jev with the question:
  **"Does this passage contain the information needed to answer the question?"**
- Jev returns P(yes) for each passage, and the passages are re-sorted by it. Eight requests run at a time;
  the API refuses 25 at once.
- Any passage whose Jev call fails stays after the scored ones in its original order. A Jev outage just
  leaves the search order.
- Cost is about **$0.0009 per question**, and it takes about 2 seconds.
- *Code:* `orchestrator/passage_jev.py`, switched on by the flag `pipeline.passage_rerank = "jev"`. It is
  **off by default in the shared app** and on only in the demo.
- *Why:* in the pre-registered test R1 (300 new questions), Jev's order put the correct page first
  **53.3%** of the time, against **43.7%** for the cross-encoder alone (+9.7 points, p = 0.00008).

### Step 5. The writer reads the best 8 passages and answers
- The answer-writing LLM (gpt-oss-120b) gets the **top 8** passages, each labelled with its filing title and
  page. It writes the answer, and citations (title + page) are built **in code**, not by the model.
- *Old:* the 8 passages were the first 8 by *document order*, not relevance. The gold page was among the 25
  found for 60 of 82 questions but among the 8 read for only 31. This was the single biggest cause of wrong
  answers before the rebuild.

### Hybrid questions (database + documents)
*Example:* "Which company had the highest revenue in fiscal 2022, and how does that company describe its
e-commerce strategy in its 10-K?"

1. Jev says **both**. The LLM splits the question into a SQL half and a documents half.
2. The SQL half runs through the normal SQL agent, which finds Walmart, $572.8B.
3. **Entity narrowing:** the documents half then searches **only Walmart's filings**, because the SQL
   result named Walmart (`pipeline.hybrid_entities`). When the result names several companies, the search
   covers all of them.
4. One answer comes back with both a SQL citation and page citations.

### Follow-up questions with no extra model call
"What was Netflix's revenue in 2021?" → "And in 2022?"

- `orchestrator/context_carry.py` detects a follow-up with simple rules: a pronoun like "it", or no company
  named when the previous turn named one.
- It carries the company and year forward from the previous question *and answer*, so "it" can mean the
  company named in the previous answer.
- No LLM call is needed (`pipeline.context_carry`).

### What the whole thing costs per documents question
- 0 LLM calls to route (Jev instead);
- 2 searches with bge;
- 25 Jev calls (well under a cent in total);
- 1 LLM call to write the answer.

---

## 6. Where Jev came from, and what we know about it from other papers

- **Jev** is made by TypeSafe AI ("a System One model for typed decisions"): fast, intuitive yes/no and choice
  answers with probabilities. A **state** (any text) plus **questions** go in; probabilities come out.
- Many papers appeared about it between mid-September and early October 2026, about 80 on arXiv. The ones we
  rely on:
  - **Deußer et al.:** the general benchmark of Jev.
  - **Li et al., "JEV-as-a-Judge":** Jev judges as well as a frontier LLM when the verdict can be *read off*
    the text, and worse when it must be *worked out*. This is the frame our results fit.
  - **Li (Fast Models, Slow Evidence):** accuracy moves by about 5 points with the wording of the question,
    so we report our wording band.
  - **Li, He, Li (probability axioms)** and **Joy (coherence):** separate yes/no answers about the same thing
    don't always add up. That explains why two yes/no routing questions did worse than one choice.
  - **Sun et al.** and **Azizi et al.:** typed models follow the option *name* more than its description.
  - **Zhong et al.:** Jev struggles when the right answer requires arithmetic.
- **What is new in our paper:** the general claim "Jev can replace LLM decision calls" is already taken.
  Nobody had tested it on **text-to-SQL**, **database-vs-documents routing**, or **finding pages in long
  filings**. Those are our niches (literature check in `docs/paperb/LITERATURE_2026-10-07.md`).

---

## 7. How the experiments were run (methodology)

This is what makes the results believable. If the mentor asks "how do you know?", this is the answer.

1. **Fresh data for every claim.** FinanceBench's own 150 questions were used only for development. Every
   result in the paper comes from questions **built after the systems were frozen**.
2. **Pre-registered.** For R1–R4 the hypotheses, measures, tests and thresholds were committed to git before
   the questions existed: `docs/research/paperb_reruns_prereg.md`. Anything changed later is in its numbered
   change log (9 entries), each recorded *before* the affected run.
3. **Fair baselines.** We compare Jev against the best existing component it would replace (the
   cross-encoder's own ranking, an LLM router, an LLM judge, a trained verifier), not against a weak
   version.
   - Our first page-finding test accidentally compared against the product's *display* order. The audit
     found this, and we threw the test out and re-ran it properly as R1.
4. **Statistics:**
   - Exact McNemar tests for paired comparisons.
   - 95% bootstrap intervals (2,000 resamples).
   - Holm correction for secondary hypotheses.
   - "Comparable" only if the whole 95% interval of the difference lies within ±8 points.
5. **Question generation.** Questions were built by code from templates or written by an LLM from a sampled
   page, then checked by a second model. Fixed random seeds and the builder scripts are in `scripts/`.
6. **Checks.** Routing labels are fixed by how the questions were built, and a second model confirmed every page
   question. No sample was labelled by hand; the paper says so in its limitations. Model checks are never
   reported as human checks.
7. **Logging.** Every model call records the model name the API actually served, so a silently swapped
   model would be caught.

### The experiments and results

| Test | Data | Result |
|---|---|---|
| **SQL: detect a wrong answer** | 393 held-out BIRD questions with corrected labels; 21 candidate queries each from 5 LLMs | Jev zero-shot AUROC **0.760** vs LLM judge **0.813** vs trained ModernBERT verifier **0.727**. Neither gap is significant, so Jev is about as good at *spotting* a wrong query. |
| **SQL: choose among candidates** | same | Jev is a **poor selector**: picking between two plausible queries needs working out what each computes. |
| **R3: can this database answer it?** | BIRD questions paired with the real schema vs a schema with the needed columns/tables removed | AUROC **0.969** (columns removed) and **0.977** (tables removed). A word-overlap baseline reached 0.787 / 0.909. Without BIRD's hints: 0.899 / 0.945. |
| **R2: routing** | 180 new questions over the SEC database + filings, 30 per class | Jev one choice **88.3%**; LLM router **84.4%** (difference not significant); Jev as two yes/no questions **75.6%**; embedding-nearest-neighbour router **62.8%**. |
| **R4: the coverage line** | 90 questions identical in form, differing only in whether the company/year is in the database | Questions about uncovered data declined: Jev **0/45 → 28/45**, LLM **0/45 → 34/45**. Unknown companies were almost all fixed (26/30, 29/30). Years outside the range mostly weren't (2/15, 5/15) because the card showed a wider raw table's range. |
| **R1: page finding** | 300 new questions from FinanceBench filing pages | Correct page first: dense search **20.3%**, cross-encoder **43.7%**, cross-encoder + Jev **53.3%** (p = 0.00008). |
| **End to end (exploratory)** | FinanceBench 150, graded by an LLM judge | Old system 20.0% → rebuilt 52.7% (no re-ranker) → 63.3% (bge). Labelled exploratory because these questions guided the fixes. |

**Cost:** scoring all 6,056 SQL candidates with Jev cost $0.11. Re-ranking 25 passages costs $0.0009 per
question. The practical limit was the API's rate limit, not price.

### The three lessons the paper argues
1. **Read off vs work out.** Use a decision model to *trigger* work (fall back, re-rank, decline). Leave
   *choosing* among generated answers to execution, voting or a trained verifier.
2. **Ask one question, not two.** One four-way choice beat two yes/no questions by 12.8 points.
3. **What the router cannot see.** A router shown only column names can't know which entities the rows
   cover. A coverage line costs a few read-only queries and no model call.

---

## 8. The paper itself

- **Title:** *Evaluating a Typed Decision Model on the Decisions of a Database-and-Documents AI Assistant*
- **Venue:** ICDEC 2026, special session SS02 (Springer LNNS). 8–12 pages; ours is 12. Deadline
  **10 October**, submitted through CMT.
- **Where:** `docs/paperb/latex/main.tex`, which builds `main.pdf`. Every number in the text is a macro
  generated from the result files by `scripts/paperb_numbers.py` into `numbers.tex`, so no number is
  typed by hand.
- **Companion paper (Paper A):** a pre-registered study of warning signals for wrong text-to-SQL answers
  (`docs/paper/`). Paper B reuses its 393 BIRD questions and cites it as under review.
- **Authors:** Neel Pradeep Jaiswal, Ronak Daniel Ramanathan, Prof. Elizabeth Jish Joy and Dr. Hima
  Deepthi Vankayalapati (Mukesh Patel School of Technology, Management and Engineering, NMIMS).
- **Figure 1** shows the assistant with the four decision points shaded.
- **Limitations the paper admits (be ready to repeat them):**
  - Questions were generated, so they may be cleaner than real users' questions.
  - There is one benchmark per role, in English only.
  - Jev's training data is unknown, and it may have seen BIRD.
  - Our second routing wording was written after seeing an earlier test's errors (both wordings were frozen
    before the new set existed).
  - Wording moves accuracy by a few points.
  - The earlier unfair page-finding and routing tests were replaced, and their files are kept in the repo.

---

### What we built, what we reused

If asked "what is your own work here?":

- **Built by us:** the whole assistant (pipeline, SQL gate, router, documents engine, web app); the Jev
  integration (router, passage re-ranking, coverage line, follow-ups without a model call); the SEC figures
  database; every question set for R1-R4 and the scripts that build them; the experiment designs,
  pre-registrations and analyses.
- **Reused as they are:** the models (Jev, the LLMs, the bge re-ranker, the embedding model, ModernBERT as a base
  to fine-tune); the libraries (LangGraph, FastAPI, Postgres/pgvector, pglast); the public data (BIRD and its
  corrected labels, FinanceBench's 84 filings, SEC XBRL figures).
- **Not measured, and the paper says so:** whether the better decisions give better final answers; no sample of
  the generated questions was labelled by hand.

---

## 9. How to demonstrate it

### A. The live system (about 10 minutes)

**Before the meeting**, with Docker Desktop running. The shared stack, the SEC database container and the
re-ranker must be up. After a Docker restart the SEC database container stays stopped, so start it if
`docker ps` doesn't show `statesync-sec-db`.

```bash
research/paperb/demo/demo_stack.sh up
```

(Run `research/paperb/demo/demo_stack.sh build` first only if the code changed since the last build.)

Copy the sign-in token to your clipboard (it is never printed):

```bash
tr -d '\n' < secrets/api_token_dev | pbcopy
```

1. Open **http://127.0.0.1:3090**, paste the token and sign in.
2. Open the workspace **partd-routing-sec-filings**.
3. The six sample questions appear on the empty chat screen. Click them in this order:

| # | Question | Route | What to point at |
|---|---|---|---|
| 1 | "What was Netflix's revenue in fiscal 2021?" | sql | A plain database answer ($29.7B). Show "How I got this" and the SQL. |
| 2 | "Which company had the highest revenue in fiscal 2022?" | sql | Walmart, $572.8B. Comparison across companies. |
| 3 | "What does Boeing's 2022 10-K say about the main risks it faces?" | docs | **The new RAG engine.** Citations show the filing title and page. Explain steps 2–5: two-round search → bge → Jev order → the writer reads the top 8. |
| 4 | "How did Walmart describe its e-commerce strategy in its 2020 10-K?" | docs | The right *year's* filing is chosen (titles help the re-ranker). |
| 5 | "Which company had the highest revenue in fiscal 2022, and how does that company describe its e-commerce strategy in its 10-K?" | hybrid | SQL finds Walmart ($572.8B), then the documents search only Walmart's filings. Both citation kinds appear. |
| 6 | "What was Tesla's revenue in fiscal 2022?" | neither | Tesla isn't in our sources. The coverage line lets the router decline in about 2 seconds instead of making something up. |

**Also try a follow-up** after question 1: "And in 2022?" It works with no extra model call.

**What to say while it runs:**
- "The router here isn't an LLM; it's one Jev choice question."
- "The writer reads the 8 passages Jev ranked highest."

**Honest notes if asked:**
- Two of the dividend answer's citations come from the 2021 10-K, not 2022. Retrieval still sometimes
  mixes years.
- A documents answer takes several seconds (bge plus 25 Jev calls).

**Afterwards:**

```bash
research/paperb/demo/demo_stack.sh down
```

The demo is a second copy that never touches the shared app on localhost:3000 or its settings. It uses
`research/paperb/demo/flags_demo.toml` and `statesync_demo.toml`.

**If something goes wrong:**

| Symptom | Fix |
|---|---|
| Sign-in fails | Copy the token again; check the demo is up: `curl -s 127.0.0.1:8091/health`. |
| Every question errors on SQL | `docker start statesync-sec-db` (it stays stopped after a Docker restart). |
| Document answers are slow or the order looks plain | The re-ranker is down. Check `~/Library/Logs/statesync-reranker.log`. |
| "Rate limited" | Jev or the LLM API is busy. Wait a minute. |

### B. The results page (no setup, works anywhere)
**Decision Lab**: `docs/paperb/demo/index.html` (open it in any browser; it needs no server). It has:

- a **documents-engine visualizer**: 22 real R1 questions, each with its 25 passages. Switch between orders
  (display / dense / cross-encoder / Jev) and watch where the correct page (highlighted) lands;
- a **routing explorer** with R2 and R4 examples and Jev's probabilities for each option;
- charts for the SQL and answerability results.

It is a good fallback if the live demo misbehaves, and a good way to *show* why Jev's ordering helps.

---

## 10. Where things live (file map)

```
StateSync/
├── orchestrator/                 the brain (Python, FastAPI + LangGraph)
│   ├── graph.py                  the question pipeline as a graph
│   ├── front_door.py             NEW  router: gathers evidence, asks Jev or the LLM
│   ├── jev_client.py             NEW  the one HTTP call to Jev
│   ├── card_coverage.py          NEW  the coverage line on the database card
│   ├── context_carry.py          NEW  follow-up questions without a model call
│   ├── passage_jev.py            NEW  Jev re-orders the passages
│   └── nodes/
│       ├── unstructured_knowledge.py   the documents engine (search, order, hybrid)
│       ├── sql_agent.py                writes, runs and corrects SQL
│       └── synthesizer.py              writes the final answer and builds citations
├── mcp/                          the only service with database access (SQL gate, vector search)
├── reranker/server.py            the bge cross-encoder service
├── parser/                       isolated PDF-to-text service
├── web/                          the React web app
├── config/
│   ├── flags.toml                on/off switches for every feature (shared; never change the defaults)
│   └── statesync.toml            settings (models, limits, retrieval sizes)
├── research/paperb/
│   ├── demo/                     the live demo: demo_stack.sh, demo flags/settings, sample questions
│   ├── partd_stack.sh            the separate test stack used for the experiments
│   └── RESUME.md                 notes on how to re-run things
├── scripts/                      experiment builders and runners (build_*_set*.py, eval_*.py), analysis
│                                 (paperb_analyze.py, paperb_numbers.py), the re-ranker launcher
├── docs/
│   ├── paperb/latex/             Paper B (main.tex, refs.bib, numbers.tex, main.pdf)
│   ├── paperb/demo/              the Decision Lab page
│   ├── paperb/AUDIT_2026-10-07.md, LITERATURE_2026-10-07.md   audit and literature check
│   ├── research/paperb_reruns_prereg.md                        the pre-registration (R1–R4)
│   ├── research/detect_then_fallback_study.md                  the SQL study's protocol
│   ├── specs/partd_tasks/        design specs for each rebuild step (T1–T5)
│   ├── eval/                     every question set and every result file (JSON)
│   ├── STATESYNC_REFERENCE.md    the complete technical reference (the product side)
│   └── DEMO.md                   the previous (product) demo guide
└── tests/                        about 1,280 automated tests
```

**Feature flags matter.** Almost every new piece is a switch in `config/flags.toml`. The shared app keeps
safe defaults, and experiments and the demo run with their own copy of the flags. If someone asks "is this
on in production?", the answer is in that file. For example, `passage_rerank` is `off` in the shared app and
`jev` in the demo.

---

## 11. Tech stack: what and why

| Piece | What it is | Why we chose it |
|---|---|---|
| **Python 3.12, FastAPI** | The back-end language and web framework | Standard, async, good AI libraries. |
| **LangGraph** | Builds the pipeline as a graph of steps with shared typed state and loops | We needed a correction loop and conditional routes. Hand-writing that state machine was a side quest. |
| **PostgreSQL 16 + pgvector** | The database, which also stores embeddings | One database, one security model; exact vector search is fast enough at our size. |
| **pglast** | PostgreSQL's real parser, in Python | The SQL gate needs a real parser; a regex misses tricks like a write hidden in a CTE. |
| **Ollama + mxbai-embed-large** | Local embedding model | Documents never leave the machine; free. |
| **bge-reranker-v2-m3** (PyTorch, on the Mac GPU) | Cross-encoder re-ranker | The biggest measured retrieval gain; multilingual; open weights. |
| **Jev (jev-1.13) via OpenCode Zen** | Typed decision model | The subject of the paper: cheap, gives probabilities, writes no text. |
| **gpt-oss-120b, Qwen 3.8 27B, GLM-5.3-Flash** | LLMs that write SQL, answers and test questions | Strong open-weight models, reached through OpenAI-compatible third-party APIs. The served model name is checked on every call. |
| **pypdf** | PDF text extraction | Light. Docling needed ~2 GB of models; PyMuPDF has a restrictive licence (AGPL). |
| **Docker Compose** | Runs every service in isolation | Separate networks enforce the security boundaries. |
| **React 19, Vite, TypeScript, Tailwind, TanStack Query, Vega-Lite** | The web app | A designed UI. Vega-Lite charts are declarative, so no model-written code ever runs in the browser. |
| **LaTeX (Springer llncs)** | The paper | The venue's required template. |
| **pytest, vitest** | Tests | 1,279 Python tests pass; 63 web tests pass. |

---

## 12. Papers and sources we took ideas from

**For the documents engine and routing**

| Idea in StateSync | Source |
|---|---|
| Cross-encoder re-ranking of search results | Nogueira et al. 2020 (monoT5; re-ranking with a model that reads query and passage together); the model itself is from the BGE M3 family (Chen et al. 2024) |
| Asking a model for a relevance probability rather than a yes/no | Zhuang et al. 2023, "Beyond Yes and No" |
| Routing between a database and documents | Bai et al. 2025 (rule-driven hybrid-source routing); Dai et al. 2026 (XGBoost routing with refusal); Adaptive-RAG (Jeong et al. 2024) |
| Deciding whether retrieved material is good enough | Corrective RAG (Yan et al. 2024) |
| Keyword search and fusion (RAG v2) | BM25 (Robertson & Zaragoza 2009); Reciprocal Rank Fusion (Cormack et al. 2009) |
| Vector search inside SQL for hybrid questions | Text2VectorSQL (Wang et al. 2025) |
| Tables kept whole as one chunk | TableRAG (Yu et al. 2025) |
| Benchmark of real filings | FinanceBench (Islam et al. 2023) |

**For SQL checking**

| Idea | Source |
|---|---|
| Verifying generated SQL by its execution result | LEVER (Ni et al. 2023) |
| Choosing among candidate queries | CHASE-SQL (Pourreza et al. 2025), GradeSQL (Tritto et al. 2025) |
| Benchmark and corrected labels | BIRD (Li et al. 2023); Jin et al. 2026 (more than half of BIRD Mini-Dev labels had errors) |
| What predicts a correct SQL answer | Richardson 2026 |
| Trained verifier model | ModernBERT (Warner et al. 2024); Laya (an open typed decision model) |
| Using cheap models first and escalating | FrugalGPT (Chen et al. 2023) |

**About Jev itself:** TypeSafe 2026; Deußer et al.; Li et al. (JEV-as-a-Judge); Li (Fast Models, Slow
Evidence); Li, He, Li (probability axioms); Joy (coherence); Sun et al.; Azizi et al.; Zhong et al.

**Earlier, still in the product** (see `docs/STATESYNC_REFERENCE.md` §17):

- APEX-SQL: sample the tables before writing SQL.
- SQL-of-Thought: an error taxonomy plus a correction loop.
- COLA: normalise the language first.
- AgentSM: trajectory memory, built and then switched off because it hurt accuracy.

---

## 13. Questions you might get, with short answers

**"What's actually novel here?"**
Testing typed decision models on three decisions nobody had tested them on:

- text-to-SQL checking;
- database-vs-documents routing;
- page finding in long filings.

Plus three design lessons:

- trigger, don't choose;
- one choice question beats two yes/no questions;
- a coverage line fixes unknown-entity routing with no model call.

**"Why not just use the LLM for everything?"**
Cost and latency. Each decision is another LLM call. Jev's decisions cost fractions of a cent, and for
routing and page finding it matched or beat the LLM.

**"Is the Jev routing better than the LLM?"**
88.3% vs 84.4%, but the difference is **not significant**. We say "comparable", not "better". The
significant win is page finding.

**"How do you know you didn't tune on the test data?"**

- The tests were pre-registered in git before the questions existed.
- The question sets were built once by scripts with fixed seeds.
- Development used FinanceBench's own questions, which are never in a test.
- Changes are in a dated change log.

**"Why did Jev do badly at choosing SQL?"**
Choosing between two plausible queries means working out what each computes. Spotting an obviously wrong
result can be read off the output. This matches Li et al.'s "read off vs work out" finding.

**"What were the old system's problems?"**

- The router saw only 20 document titles.
- The writer read passages in document order, not relevance.
- Wrong-year filings came first.

Fixing these took end-to-end accuracy from 20% to 53–63% on FinanceBench. That was development work and is
reported as exploratory. The paper is about the decisions, not those fixes.

**"Why FinanceBench and an SEC database?"**
Real, long documents (10-Ks run to hundreds of pages), and a public benchmark. The SEC figures database
covers the same companies, so some questions are answerable both ways. That is exactly what makes routing
hard.

**"Who checked the generated questions?"**

- A second model checked every question when the sets were built.
- A model not used elsewhere in the study (GPT-6.1 Sol) labelled a sample blind: it agreed with 37 of 40 routing
  labels and accepted all 30 page questions. Nobody labelled a sample by hand; the paper says so.

**"What would you do next?"**

- Repeat the routers to measure run-to-run variance.
- Run an external hybrid benchmark.
- Make the coverage line state the range of the data a question would actually be answered from.
- Test other typed decision models.

**"Is it secure?"**

- The AI side never has a database password.
- Every query passes three layers: a parser gate, a read-only rolled-back transaction, and a SELECT-only
  login.
- The PDF parser and the re-ranker each sit on an isolated network with no credentials.

**"How much did it all cost?"**
Jev costs were cents. All of Jev's SQL scoring was $0.11. The LLM runs used small paid credits on
OpenAI-compatible APIs.
