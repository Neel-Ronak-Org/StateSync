# How StateSync works

This guide follows one question through the system, step by step: what happens, why it is built that way,
what happens when a part fails, and where it is in the code. The numbers behind each choice are in
[EVALUATION.md](EVALUATION.md).

## The shape of it

```
 browser ──► orchestrator (FastAPI + LangGraph) ──JSON-RPC──► MCP tool server ──► Postgres + pgvector
             holds the model keys, no DB password             the only DB passwords    connected databases
                     │                                                 │
                     ├──► language models (OpenAI-compatible API)      └──► Ollama (embeddings)
                     ├──► Jev (typed decisions)
                     └──► parser (uploaded files to text, isolated)
```

The orchestrator is where the language models run, so it is treated as untrusted: it is on a network with no
route to any database, and it can reach data only by asking the MCP. The MCP runs a model's SQL only through
its three read-only layers. A model that is tricked into writing `DROP TABLE` produces a refused query, not a
dropped table.

## A question's path

```
 question ─► ① follow-up check ─► ② router ──┬─ sql ─────► ③ SQL agent ⟲ retries ──────────────┐
                                             ├─ docs ────► ④ documents engine ─────────────────┤
                                             ├─ both ────► split ─► ③ ─► ④ (narrowed) ──────────┼─► ⑤ answer writer ─► ⑥ Inspector
                                             └─ neither ───────────────────────────────────────┘
```

The order of steps is `orchestrator/graph.py`, a LangGraph state machine. Each step reads one typed state
object (`orchestrator/state.py`) and returns only what it changed. The SQL retry loop is a visible edge in the
graph, not a hidden loop.

### ① Follow-up check (`orchestrator/context_carry.py`, no model call)

"And in fiscal 2022?" cannot be answered alone. A question is a follow-up when it uses a reference word
("it", "its", "that company") or names no company while the previous turn named one. The company and year it
leaves out are taken from the previous turn (its answer first, then its question). The SQL writer and the
answer writer get one line of context after the question, and the document search gets the carried words.

*Why not a model?* The earlier design spent one model call on every turn to rewrite the question. This is
plain code, costs nothing, and its decision is shown in the Inspector.

### ② Router (`orchestrator/front_door.py`)

The router reads three things, gathered with no model call:

- **the database card**: every table with its columns, the owner's description of the business, and a
  **coverage line**: for each short text column, every value it holds (company names, tickers, metric
  names), and for each date or year column, its range (`orchestrator/card_coverage.py`);
- **the best-matching documents**: one quick search, the top 5 documents with the start of their best
  passage;
- **the last 3 turns** of the conversation.

Then it asks **Jev** one typed choice question: which sources are needed, `sql`, `docs`, `both` or `neither`.
Jev is a decision model: instead of writing text it returns a probability for each option, and the most
likely option is the route. A second yes/no question asks whether the documents part needs the database's
answer first ("which company?"), which turns on narrowing in step ④.

*Why Jev, and why one question?* In Paper B's routing test, one four-way Jev question routed 88.3% of
questions exactly right against 84.4% for an LLM router, and the same decision asked as two yes/no questions
managed only 75.6%. *Why the coverage line?* Without it, every router sent questions about companies the
database does not hold to SQL; with it, Jev declined most of them correctly.

*If Jev fails:* the LLM router (`decide_llm`, one model call with a JSON reply) decides instead, and the trace
says so. An unreadable LLM reply is treated as "both sources", never as a silent default to SQL.
`[pipeline] jev = "off"` in `config/flags.toml` uses the LLM router always.

A `neither` question goes straight to the writer, which replies with a fixed message and no model call.

### ③ SQL agent (`orchestrator/nodes/sql_agent.py`)

First attempt:

1. Read the database's tables (`schema_retriever.py`) and the workspace's **approved** knowledge
   (`workspace_context.py`): the owner's business description, table and column notes, definitions, and up
   to 3 similar approved examples.
2. **Fast path:** if the question exactly matches an approved example, run its SQL as is. No model call;
   the answer is labelled **Verified**.
3. Otherwise **draft** a query, read which tables the draft uses (with the PostgreSQL parser, pglast), and
   **sample a few rows** of those tables, so the model sees how values are really stored (FY2022 or 2022?
   "Walmart Inc." or "WALMART INC."?).
4. Write the **final query** with the schema, the knowledge and the samples.

Every query runs through the MCP's **three independent read-only layers** (`mcp/tools/data_tools.py`):

| Layer | What | Stops |
|---|---|---|
| 1. Statement gate | pglast parses the SQL: exactly one SELECT, no data-changing CTE, no dangerous function, no internal schema, must read a table | anything that is not a plain read |
| 2. Session | a READ ONLY transaction with a 15-second limit, always rolled back | a write that got past layer 1; runaway queries |
| 3. Login | the database login may only SELECT (checked when a database is connected) | a write that got past both |

**Retries:** a failed query is classified by code, not by a model (`classifiers/error_classifier.py`,
rules in `config/error_taxonomy.json`): unknown column, syntax, timeout, empty result, and so on, each with a
fixed hint. The agent rewrites the query with every earlier failure and its hint, up to 3 times. Some
categories never retry: `permission_denied` (that would be probing a security control), `unclassified`, and
`no_data_source` (the query read no table because the database does not hold the answer). A first empty
result retries once; a second is accepted as "no records match".

**Retry caution:** an answer whose query needed a correction carries a caution under it. In Paper A (393 BIRD
questions), "the agent had to retry" was the best warning sign of a wrong answer that costs nothing extra.

**Route recovery:** a database question that ends in `no_data_source`, in a workspace with documents, runs
the documents route once instead.

### ④ Documents engine (`orchestrator/nodes/documents.py`)

**When a document is uploaded** (`orchestrator/library_api.py`, `library_ingest.py`, `parser/server.py`):
the isolated parser extracts the text page by page; the text is cut into passages of about 1,400 characters
that never span two pages (so a citation names one page), with a 200-character overlap so a sentence cut at
a boundary is whole in one of them; each passage is embedded with **mxbai-embed-large** (1,024 numbers) and
stored in Postgres with **pgvector** (`mcp/tools/library_tools.py`, `mcp/embeddings.py`).

**When a question is searched:**

1. **Round 1 finds the files.** A dense search (cosine similarity between the question's vector and every
   passage's, an exact scan) over all the workspace's documents takes the best 8 passages and notes which
   documents they come from (`mcp/tools/vector_tools.py`).
2. **Round 2 finds the passages** inside only those documents: up to 25. Searching inside the right files
   first matters for long filings: a flat search lets near-identical passages from the wrong year's filing
   crowd out the right one.
3. **Jev re-ranks the 25.** For each passage Jev answers "Does this passage contain the information needed
   to answer the question?" with a probability (`orchestrator/passage_jev.py`, 8 requests at a time), and the
   passages are sorted by it.
4. The writer reads **the top 8** (the relevance window).

*Why re-rank?* Dense similarity finds passages that are *about* the question; it is weak at telling which one
*contains the answer*. In Paper B's page-finding test, Jev's order put the right page first for 49.3% of
questions against 20.3% for the dense order.

*If Jev fails:* the passages keep the dense order.

**Two-source questions** (`split_question_node`, `documents_after_sql_node`): the question is split into a
database part and a documents part (one short model call, unless the LLM router already split it). The SQL
agent answers the database part first. When the router said the documents part depends on it, the search is
**narrowed** to the documents whose titles name a company in the SQL result (for a "which ... highest"
question, only the top row's company), and round 1 is skipped because the titles already named the files.

### ⑤ Answer writer (`orchestrator/nodes/synthesizer.py`)

The model is given the question, the SQL result (or a plain-words reason the query failed), the 8 passages
labelled with document title and page, and any caveats. It writes the answer and, optionally, the *shape* of
a chart. Code, not the model, does the rest:

- **Citations** come from what actually ran: the executed SQL and the passages' documents and pages.
- **Chart data** is attached from the real rows; a chart naming a column that is not in the result is
  dropped. The chart is checked against a strict schema twice, on the server
  (`orchestrator/schemas/synthesizer_output.py`) and in the browser (`web/src/charts/a7.ts`), because a chart
  spec could otherwise carry executable expressions.
- **Number grounding** (`orchestrator/number_grounding.py`): every figure in the answer is traced to a cell, a
  column total, the question or a passage; a figure found nowhere becomes a caveat ("check it against the
  table").
- **Evidence label** (`orchestrator/evidence.py`): *Verified* (an approved example ran) or *New query*.
- **"How I got this"** (`orchestrator/sql_explain.py`): the SQL read back in plain words, with no model.
- Program code in the answer text is removed.

### ⑥ Pipeline Inspector (`web/src/features/inspect/`)

One click on an answer opens every step above: Jev's probabilities for each route, the database card it read,
the schema diagram with the tables the query used, the samples, the SQL and its safety layers, the search's
two rounds, the re-ranking table (each passage's place before and after Jev, and the cut at 8), the writer, and
the time and cost of each step. The backend records it in the answer's trace (`state_trace`).

## Workspaces: how it adapts to new data

A **workspace** is one business: one connected database, the document folders it searches, and what the
owner has taught StateSync about it (`orchestrator/workspaces_api.py`, `workspace_setup.py`,
`mcp/tools/workspace_tools.py`).

| Tab | What |
|---|---|
| Business | a few lines describing the business; the router and the SQL writer read them as the owner's words, never as instructions |
| Knowledge | an AI drafts one sentence per table and column from the catalog and sample values; the owner edits and approves; definitions of business terms. Only approved text reaches a prompt |
| Approved | answers the owner marked correct (thumbs-up in Ask): the same question runs the approved SQL directly |
| Documents | which library folders the workspace searches |
| Schema | the database diagram (`orchestrator/inspect_api.py`, `schema_graph.py`) |

Connecting a database (`mcp/connections.py`): the login is checked to be read-only (no admin role, no write
privilege on any table), the password is encrypted with a key only the MCP holds, and the owner chooses one
schema. Nothing in the pipeline is tuned to the demo data: `scripts/check_new_workspace.py` proves it on a
database and a document StateSync has never seen.

## Models

| Role | Model | Used for |
|---|---|---|
| `sql` | gpt-oss-120b | draft and final SQL, corrections |
| `synthesizer` | gpt-oss-120b | the answer |
| `decomposer` | qwen-3-8-27b | splitting a two-source question; drafting table notes |
| `supervisor` | qwen-3-8-27b | the LLM router, only when Jev cannot be reached |
| Jev (jev-1.13) | a typed decision model | the route; whether documents depend on the database; passage re-ranking |
| mxbai-embed-large | embedding model (local, Ollama) | passages and questions as 1,024-number vectors |

The language models are reached through one OpenAI-compatible endpoint (`orchestrator/llm/`); changing model
or provider is a settings change in `config/flags.toml`. The client refuses an answer from a model other than
the one asked for, waits out rate limits instead of failing, and gives reasoning models extra token budget.
A typical question takes 13–30 seconds; Jev's share is about 3 seconds (routing) and 2 seconds (re-ranking).

## Security, in one table

| Concern | How |
|---|---|
| A model writes harmful SQL | three independent read-only layers (above); the orchestrator has no database password or route |
| Prompt injection in data or documents | the owner's text and the evidence are marked as data, not instructions; the worst outcome is a wrong route or a wrong answer, never a write |
| Hostile uploads | the parser is isolated: no network, no secrets, read-only filesystem, each PDF parsed in a child process with memory, CPU and time limits |
| Another person's data | every StateSync table carries tenant and owner; every query filters on them; Postgres row-level security enforces it again |
| Sign-in | tokens stored only as hashes; an HttpOnly session cookie; rate limits per person; every request in the audit log |
| Service to service | the MCP accepts calls only with a shared secret the orchestrator holds |

## Code map

| File | Lines | What |
|---|---|---|
| `orchestrator/main.py` | 660 | the web API: `/query`, conversations, health; registers the other routes |
| `orchestrator/graph.py` | 130 | the order of steps |
| `orchestrator/state.py` | 130 | the question's state and how updates merge |
| `orchestrator/front_door.py` | 550 | the router |
| `orchestrator/card_coverage.py` | 110 | the coverage line |
| `orchestrator/context_carry.py` | 245 | follow-ups |
| `orchestrator/nodes/sql_agent.py` | 330 | the SQL agent |
| `orchestrator/nodes/documents.py` | 325 | the documents engine and two-source steps |
| `orchestrator/passage_jev.py`, `jev_client.py` | 240 | Jev re-ranking; the Jev call |
| `orchestrator/nodes/synthesizer.py` | 505 | the answer writer |
| `orchestrator/number_grounding.py`, `sql_explain.py`, `evidence.py` | 580 | figure checks, "How I got this", the label |
| `orchestrator/workspaces_api.py`, `workspace_setup.py`, `workspace_context.py` | 620 | workspaces and approved knowledge |
| `orchestrator/library_api.py`, `library_ingest.py` | 530 | uploads and passages |
| `orchestrator/tiles_api.py`, `dashboards_api.py`, `tile_render.py` | 500 | pinned answers on dashboards |
| `orchestrator/auth.py`, `session_api.py` | 370 | sign-in, sessions, rate limits |
| `orchestrator/llm/` | 550 | the model client |
| `mcp/main.py`, `session.py`, `service_auth.py` | 400 | the tool server, its session and its token check |
| `mcp/tools/data_tools.py` | 420 | the SQL safety gate and query execution |
| `mcp/tools/vector_tools.py`, `embeddings.py` | 200 | vector search and embeddings |
| `mcp/tools/schema_tools.py`, `connections.py` | 620 | catalogs; connected databases |
| `mcp/tools/workspace_tools.py`, `library_tools.py`, `history_tools.py`, `tile_tools.py`, `dashboard_tools.py` | 3,050 | the stored data, each with its ownership checks |
| `parser/server.py` | 260 | the isolated parser |
| `web/src/` | | the app: `features/chat` (Ask), `features/inspect` (Inspector, Schema), `features/workspaces`, `features/library`, `features/dashboard`, `charts/` (the browser-side chart check) |

Every number and limit is in `config/statesync.toml`, with a comment saying what it does.

## What is standard, and what is ours

Standard parts: text-to-SQL with schema context and retries, dense retrieval with pgvector, a model writing
the final answer, an MCP-style tool server.

Ours, and why they matter:

- **Typed decisions with Jev** for routing and re-ranking, measured against LLM baselines in Paper B.
- **The coverage line**, so a router can tell "a company the database holds" from one it does not.
- **Two-round search** (files, then passages), and **narrowing** a two-source question by the database's answer.
- **Draft-then-sample** before the final SQL, and **classified retries** with fixed hints.
- **Three independent read-only layers** with the model on a network that cannot reach any database.
- **Code-built citations, charts and figure checks**, so the parts of an answer a reader trusts most are not
  left to the model.
- **The Pipeline Inspector**, which shows every one of these steps for any answer.
