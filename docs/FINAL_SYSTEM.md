# StateSync: the final system

*Decision document, 28 October 2026. This defines the one system we clean up, demo, report and present.
Everything not listed under "The final system" is removed from the working tree. It stays recoverable from the
tag `pre-cleanup-2026-10-26`. The clean-up is done; the system is described in [ARCHITECTURE.md](ARCHITECTURE.md),
its measurements in [EVALUATION.md](EVALUATION.md) and the demo in [DEMO.md](DEMO.md).*

---

## 1. What StateSync is, in one sentence

> **StateSync answers business questions from a company's database and its documents. It decides which source
> each question needs, writes safe read-only SQL or finds the right pages, cites where every figure came from,
> and shows each step it took.**

All the components below serve that sentence, and each was kept because an experiment showed it helps. Dashboards,
the live shop simulator, memory, templates and the other side features don't serve it, so they go.

---

## 2. Redesign or not?

**No new architecture. One pipeline, made the default, with a few small fixes.**

The reasons:

- The current architecture is the one we measured. Paper B and the end-to-end FinanceBench test both used it.
  Redesigning it a week before submission would leave us presenting a system whose numbers we don't have.
- The teachers' problem is not the architecture. It is that the repository holds **two** pipelines: the app's
  defaults still run the old one, and only a separate demo copy runs the new one. It also carries about
  5,500 lines of switched-off code. Removing those *is* the redesign the project needs.
- The small fixes are listed in section 7. Each is a few lines and each closes a known weakness.

---

## 3. The final pipeline

```
                     question (in a workspace)
                               │
             ① Follow-up check (no model call)
                               │
             ② Router: one Jev choice question
                sql │ docs │ both │ neither
         ┌──────────┴───┬─────────┴──────┐
   ③ SQL agent      ④ Document search   (both: SQL first, then the
   write → check →   find files → find   documents of the companies
   run → retry       passages → re-rank  in the SQL result)
         └──────────┬───┴────────────────┘
             ⑤ Answer writer: cites pages and rows, checks every figure
                               │
             ⑥ Pipeline Inspector: every step above, visible
```

| Step | What it does | Why it is there (the evidence) | If it fails |
|---|---|---|---|
| ① **Follow-up check** | "And in 2022?" or "Does it mention lawsuits?" carries the company and year from the previous turn. Plain code, no model. | The old system spent a model call rewriting every question. | Nothing to fail: it adds a line or doesn't. |
| ② **Router** | Jev reads the question, a *database card* (tables, columns, the owner's approved notes, and a **coverage line** listing which companies and years the rows hold) and the titles of the closest documents. It then answers one four-way question: SQL, documents, both or neither. | Paper B, routing (180 new questions): one four-way Jev question 88.3% vs. an LLM router 84.4% (not significant); two yes/no questions only 75.6%. With the coverage line: 98.9% vs. 95.6%. On questions about companies the database doesn't hold, the coverage line raised correct "neither" answers from 0/45 to 28/45 (Jev). | The LLM router takes over. |
| ③ **SQL agent** | Reads the schema and the owner's notes, drafts SQL, looks at sample rows of the tables it used, writes the final SQL, then runs it through **three independent read-only layers** (parser check: one SELECT only; read-only transaction with a 15 s limit; a database login that can only read). On an error it classifies the error and retries, up to 3 times. | Paper A (393 BIRD questions): extra answer checks applied to only 4% of answers and caught no more errors than an LLM judge. The agent's own retry was the best free warning (79% precision). So we kept the retry loop and dropped the check layer. | Plain-language failure message. If the database lacks the data and the workspace has documents, the documents route runs instead. |
| ④ **Document search** | Two-round dense search: find the right files first, then the 25 closest passages inside them. Jev re-ranks those 25 by "does this passage contain what the question needs?". The writer reads the top 8. | FinanceBench end to end (150 questions, exploratory): old engine 20.0% correct → rebuilt 52.7%; re-ranking raised it further (63.3% with a cross-encoder). Paper B, page finding (300 new questions): Jev re-ranking the dense list puts the right page first 49.3% of the time vs. 20.3% for dense order (+29.0 points, p < 10⁻¹⁵), and above the GPU cross-encoder's 43.7% (+5.7, p = 0.053). | Jev down: the dense order. |
| ⑤ **Answer writer** | Writes the answer. Citations (file, page, rows) are attached by code, not by the model. Every number in the answer must appear in the rows or passages, otherwise it is flagged. Includes "How I got this" in plain words. | The safety and trust design from Phase 3; it costs no extra model call. | — |
| ⑥ **Pipeline Inspector** | One click per answer: router probabilities, the database card, the schema diagram with the tables used, the SQL and its safety checks, the search rounds, the re-ranking table with the cut line, cost and timings. | Built for explainability: every claim above can be shown live. | — |

**Models:** two hosted models through one OpenAI-compatible endpoint. A small one (qwen-3-8-27b) handles quick
text jobs; a large one (gpt-oss-120b) writes SQL and answers. Jev makes the typed decisions. The embedding model
(mxbai-embed-large, run by Ollama) runs locally.

**Services (6):** web app, orchestrator (the pipeline; holds model keys, no database password), MCP tool server
(the only service with a database password), Postgres with pgvector, Ollama, and the isolated file parser.
The cross-encoder service and the evaluator are gone.

---

## 4. Workspaces: yes, still a thing, and they are how the flexibility works

A **workspace** is "one business": one connected database, the document folders it may search, and what the
owner has told StateSync about it. The demo workspace (`partd-routing-sec-filings`) uses all of these:

- **Business description:** a few lines in the owner's words. The router and the SQL writer read them as
  background, never as instructions.
- **65 approved table and column notes:** an AI drafts them, the owner edits and approves, and only approved
  text reaches the model.
- **6 definitions.**

**Kept (five tabs):** Business, Knowledge (table notes and definitions), Approved (the approved examples),
Documents (which folders), Schema (the diagram).

**Approved examples (thumbs-up → "Verified"):** kept. It is small, easy to explain, and the only evidence label
that means something once the check layer is gone.

**Removed:**
- the Test tab and the timer that re-checks approved examples. They were built for the BIRD workspaces, aren't
  used by the demo, and add a background job;
- definitions proposed by AI from uploaded documents. The demo's 6 definitions were written in the Knowledge
  tab, so nothing the demo uses depends on it.

### What happens when you add something new

| You do | StateSync does | Ready when |
|---|---|---|
| **Upload a document** (Library → folder → upload) | The isolated parser extracts text page by page; the text is cut into page-bound passages, embedded and stored in pgvector; the folder is attached to the workspace | Seconds to a few minutes (status changes from `processing` to `ready`). No retraining, no per-dataset tuning. |
| **Connect a database** (Library → Databases) | Checks the login can only read (no superuser or admin role, no write privilege on any table), stores the password encrypted, scans the tables, drafts notes for approval, and builds the coverage line from the data | After the scan; the notes improve answers but are optional |

**Where it is general, and where it is tuned to filings:**

- **General:** routing, SQL, search, re-ranking, citations, grounding and safety work on any Postgres database
  and any PDF or text document.
- **Tuned to filings:** two small helpers need names in document titles, like `COCACOLA_2022_10K`:
  - narrowing a two-source question to the companies in the SQL result;
  - carrying the company into a follow-up.

  With other titles they don't fire, and the search simply covers all the workspace's documents.
- **Limits to state:**
  - Postgres only.
  - The router's card is capped at 4,000 characters and the scan at 60 tables, so a very large database is
    summarised.
  - Vector search is an exact scan, fine for tens of thousands of passages (the demo has 44,922) but not millions.

**To prove it in the demo:** create a fresh workspace live, upload one new PDF and connect one small database
(section 8).

---

## 5. What is removed

| Removed | Why |
|---|---|
| The old router path (normaliser → rewriter → LLM router) and the two experimental Jev router variants | Replaced by ② |
| Memory (AgentSM + Qdrant) | Measured in August: lowered accuracy (10/10 → 7/10) |
| Answer checks, repair loop, self-consistency | Paper A: they rarely fire and catch no more than a judge |
| Templates, query rewrite, column profiles, linked table selection, vision scorer, the evaluator service | Off and unused by the measured pipeline |
| Keyword (BM25) hybrid search, layout chunker, dataset scope, abstention | Measured in the earlier retrieval work on e-commerce documents, never part of the pipeline we evaluated end to end |
| ONDC profile, DIGIPIN map codes, the `public`, `olist` and `live` datasets | From the earlier e-commerce product |
| Multilingual questions | Only existed in the old router |
| Dashboards: the AI dashboard builder ("Build with AI"), the live shop simulator and its data | Secondary. Pinning an answer to a dashboard stays (section 9) |
| The bge cross-encoder re-ranker service | Jev alone re-ranks the dense list (section 9) |
| Unused model adapters (Anthropic, Azure, Gemini, Groq, local) | One endpoint is used |
| About 100 experiment and old phase scripts, 330 old documents | Research moves to `research/`; old documents leave the tree |

Everything removed stays in git history and in the tag.

---

## 6. The experiments that led here (the only ones the report needs)

| # | When | Question we asked | Result | What we did |
|---|---|---|---|---|
| 1 | Aug | Does remembering past answers (AgentSM memory) help? | Accuracy fell from 10/10 to 7/10 | Removed memory |
| 2 | Oct, Paper A | Do automatic answer checks catch wrong SQL? (393 BIRD questions, pre-registered) | They fire on 4% of answers and catch no more than an LLM judge; the agent's own retry is the best free warning (79% precision) | No check layer; keep the retry loop |
| 3 | 5–6 Oct | Where does the documents engine lose answers? (FinanceBench, 150 questions, end to end) | Old 20.0% → rebuilt 52.7% → with a re-ranker 63.3% | Two-round search, relevance order, titles, a re-ranking step |
| 4 | Oct, Paper B | Can one Jev question route better than an LLM? (180 new questions) | 88.3% vs. 84.4% (n.s.); with the coverage line 98.9% vs. 95.6%; two yes/no questions 75.6% | Jev choice router with coverage line; LLM as fallback |
| 5 | Oct, Paper B | Does Jev find the right page better? (300 new questions) | Over the dense list: right page first 49.3% vs. 20.3% (+29.0, p < 10⁻¹⁵), and above the GPU cross-encoder's 43.7% (+5.7, p = 0.053). Over the cross-encoder's list: 53.3% | Jev re-ranks the dense list; the cross-encoder is dropped |
| 6 | Oct, Paper B | Can Jev judge SQL or spot unanswerable questions? (393 and 298 questions) | SQL: AUROC 0.760, between an LLM judge (0.813) and a fine-tuned verifier (0.727), poor at choosing between queries. Unanswerable: AUROC 0.969, but only about half caught at a 1% false-refusal budget | Not adopted: the router's "neither" and the coverage line handle missing data |

Experiment 6 is as important as 4 and 5: it shows we kept only what helped and dropped what didn't.

---

## 7. Small fixes in the clean-up (all done)

1. **One pipeline:** the demo settings become the app's defaults. The separate demo copy disappears, and
   `localhost:3000` *is* the demo.
2. **Ready means ready:** `docker compose up -d` starts the six services, each with a health check; Ollama keeps
   the embedding model loaded (`OLLAMA_KEEP_ALIVE=-1`); `scripts/check_demo.py` asks every demo question end to
   end before the demo. This fixes the "service unavailable" error we hit when the Mac was busy.
3. **Two-source narrowing:** for a "which company…" question, narrow the document search to the *answer's* company,
   not every company in the result (the dividends defect).
4. **Evidence label:** with the check layer gone, show "Verified" (approved example) or "New query", plus a caution
   when the SQL needed a retry. That is Paper A's finding, applied.
5. **Security:** drop the debug field that echoes rejected SQL (the one failing security check, 43/44).
6. **Settings:** about 5 real options instead of 39 switches; comments in plain words, with no internal IDs.

---

## 8. The demo (in full, click by click: [DEMO.md](DEMO.md))

1. **Workspace and data** (Schema tab): the SEC database, the 87 filings, the approved notes.
2. **Database question** → Inspector: router, card, SQL, safety layers, rows.
3. **Documents question** → Inspector: the two search rounds, the re-ranking table, the cut at 8.
4. **Two-source question** (Walmart revenue + e-commerce strategy) → Inspector: both lanes.
5. **Follow-up** ("And in 2022?") → the carried context.
6. **A question neither source can answer** → "neither", with the coverage line as the reason.
7. **Flexibility, live:** a new workspace, upload one PDF, connect one small database, ask a question of each.

---

## 9. Decisions (answered by the owner)

- **Dashboards:** keep simple pinning. An answer can be pinned to a dashboard and refreshed. Build with AI, the
  live simulator and the `live` data go.
- **Workspace extras:** the Test tab, the re-check timer and document-proposed definitions go (section 4).
- **Re-ranking: Jev only.** One re-ranker to explain and no GPU process to start. The paper measured this
  order: right page first 49.3%, against 53.3% with the cross-encoder in front, and 67.0% vs. 69.7% for the right
  page among the 8 passages the writer reads. **The end-to-end answer test was not run this way.** After the
  clean-up, re-run FinanceBench's 150 questions on the final system, so the report has one end-to-end number
  for exactly what we demo (exploratory, as before; it needs the owner's go-ahead because it spends API
  credits).

Decided by default, following the audit's recommendations: remove `olist`, remove multilingual, research in a
`research/` folder in this repository, submit `main` after the clean-up with the paper tags, and use the one
OpenAI-compatible model endpoint.
