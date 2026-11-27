# StateSync

Ask a business question in plain English. StateSync answers it from your company's database, its documents,
or both, cites where every figure came from, and shows each step it took.

**Example:** "Which company had the highest revenue in fiscal 2022, and how does it describe its e-commerce
strategy in its 10-K?"

1. **Route:** a typed decision model (Jev) decides the question needs both sources.
2. **Database:** it writes and runs a read-only SQL query, which answers "Walmart".
3. **Documents:** it searches Walmart's filings, lets Jev re-rank the passages and reads the best 8.
4. **Answer:** it writes one answer citing the SQL and the filing pages, checks every figure against the data,
   and shows the whole path in the **Pipeline Inspector**.

## How it is built

| Part | What it does |
|---|---|
| Web app | React + TypeScript: chat, charts, dashboards, the Pipeline Inspector |
| Orchestrator | Python + LangGraph: routing, the SQL agent, document search, the answer writer. Holds the model keys but **no database password** |
| MCP tool server | The **only** service that can reach a database, and only through read-only logins and a SQL safety gate |
| Postgres + pgvector | StateSync's own data: workspaces, conversations, documents and their vectors |
| Ollama | The local embedding model (mxbai-embed-large) |
| Parser | Turns uploaded files into text, isolated with no network and no secrets |

All six run as Docker services. The design, step by step, is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Run it

```bash
docker compose up -d          # about a minute until every service is healthy
docker compose ps             # every line should say (healthy)
open http://localhost:3000    # sign in with the token in secrets/api_token_dev
```

First time on a new machine? Create the files in `secrets/` (see [secrets/README.md](secrets/README.md)),
run `bash scripts/migrate.sh`, then `docker compose exec ollama ollama pull mxbai-embed-large`.

Stop with `docker compose stop`. Never run `docker compose down -v`: it deletes the database and the
document vectors.

## Use it with your own data

1. **Data sources → Databases:** add a Postgres database with a read-only login. StateSync checks that the
   login cannot write.
2. **Data sources → Documents:** upload PDFs, text or Markdown. Each file is searchable once it says `ready`.
3. **Workspaces → New workspace:** pick the database and document folders, optionally let the AI draft
   table notes, then approve.
4. **Ask.**

## Research paper

StateSync is the test bed for:

> N. P. Jaiswal, R. D. Ramanathan, E. J. Joy, H. D. Vankayalapati. *Evaluating a Typed Decision Model on the
> Decisions of a Database-and-Documents AI Assistant.* Accepted, to appear.

The paper tests whether a typed decision model (Jev) can replace extra LLM calls for four of the assistant's
decisions, on held-out data with baselines fixed in advance. Main findings:

- **Finding the right page:** re-ranking a cross-encoder's top 25 passages with Jev put the right page first
  53.3% of the time, against 43.7%.
- **Routing:** one four-way Jev question routed 88.3% of questions exactly right, against 84.4% for an LLM
  router (not a significant difference).
- **Missing data:** Jev told intact schemas from ones missing the needed columns with AUROC 0.969.
- **Wrong SQL:** Jev flagged wrong answers about as well as existing methods, but not better.

The paper and its LaTeX source are in [paper/](paper/main.pdf). The experiment scripts and every result
file are kept at the [`research-archive`](https://github.com/Neel-Ronak-Org/StateSync/tree/research-archive/research)
tag.

## More

| Document | For |
|---|---|
| [docs/USER_GUIDE.md](docs/USER_GUIDE.md) | Using the app |
| [docs/DEMO.md](docs/DEMO.md) | The demo, click by click |
| [docs/DOCUMENTS_ENGINE.md](docs/DOCUMENTS_ENGINE.md) | Document search in depth |
| [docs/EVALUATION.md](docs/EVALUATION.md) | The experiments that shaped the design |
| [docs/RESULTS_EXPLAINED.md](docs/RESULTS_EXPLAINED.md) | Every number and how it was measured |

**Tests:** `python -m pytest tests/ -q` for Python, and `cd web && npm run test:unit` for the web app.

**Limits:** Postgres databases only. PDF, text and Markdown documents only, with no OCR. English only.
Vector search suits tens of thousands of passages, not millions.
