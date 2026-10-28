# StateSync

StateSync answers business questions from a company's database and its documents. It decides which source
each question needs, writes safe read-only SQL or finds the right pages, cites where every figure came from,
and shows each step it took.

Ask "Which company had the highest revenue in fiscal 2022, and how does it describe its e-commerce strategy
in its 10-K?" and StateSync:

1. routes it to **both** sources (one typed question to the Jev decision model);
2. writes and runs a read-only SQL query on the database, which answers "Walmart";
3. searches only Walmart's filings, lets Jev re-rank the passages, and reads the best 8;
4. writes one answer citing the SQL and the filing pages, checks every figure in it against the data, and
   shows the whole path in the **Pipeline Inspector**.

| Read this | For |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How it works, step by step, why each part exists, and where it is in the code |
| [docs/EVALUATION.md](docs/EVALUATION.md) | The experiments that shaped it, with their numbers |
| [docs/DEMO.md](docs/DEMO.md) | The demo, click by click, with what to say |
| [docs/FINAL_SYSTEM.md](docs/FINAL_SYSTEM.md) | What was kept, what was removed, and why (the clean-up decision) |
| [research/README.md](research/README.md) | The two papers and the studies behind them (separate from the product) |

## Run it (this machine: the database, models and demo data already exist)

```bash
docker compose up -d                  # six services; about a minute until all are healthy
docker compose ps                     # every line should say (healthy)
docker start statesync-sec-db         # the demo's SEC database (a separate container, see demo/sec_db.sh)
python3 scripts/check_demo.py         # optional: asks the demo questions end to end (about 3 minutes)
pbcopy < secrets/api_token_dev        # copies your sign-in token (macOS); prints nothing
open http://localhost:3000            # paste the token, Sign in
```

Use `http://localhost:3000`, not `127.0.0.1`: the sign-in cookie belongs to `localhost`.

Stop with `docker compose stop` (all data kept). **Never** run `docker compose down -v` or
`docker volume prune`: they delete the database, the documents' vectors and the downloaded embedding model.
After a code change, rebuild only that service: `docker compose build orchestrator && docker compose up -d orchestrator`.

### The six services

| Service | What it does | Reaches |
|---|---|---|
| `frontend` | the web app (React), served by nginx on localhost:3000 | the orchestrator, from the browser |
| `orchestrator` | the pipeline; holds the model keys, **no database password** | the MCP, the parser, the model and Jev APIs |
| `mcp` | the tool server; the **only** service with database passwords | Postgres, Ollama, connected databases |
| `postgres` | StateSync's own data: workspaces, conversations, uploaded documents and their vectors (pgvector) | nothing |
| `ollama` | the embedding model, mxbai-embed-large | nothing |
| `parser` | turns an uploaded file into text, isolated (no network, no secrets, limited memory) | nothing |

### Secrets (`secrets/`, never committed)

| File | What |
|---|---|
| `pg_migrator_password`, `pg_reader_password`, `pg_history_password` | Postgres logins (random strings) |
| `connection_key` | encrypts the passwords of connected databases: `python3 scripts/make_connection_key.py` (never regenerate once databases are connected) |
| `mcp_token` | the orchestrator–MCP shared secret: `openssl rand -hex 32 > secrets/mcp_token` |
| `api_principals.json`, `api_token_dev` | sign-in: `python3 scripts/gen_api_principal.py` |
| `cheaperinference_api_key` | the language models (an OpenAI-compatible endpoint, `config/flags.toml`) |
| `opencode_api_key` | Jev (OpenCode Zen) |

**A clean machine** also needs the database schema: `bash scripts/migrate.sh` (migrations 001, 002, 004),
then the remaining files in `migrations/` applied in order with
`docker exec -i postgres psql -U statesync_migrator -d statesync < migrations/NNN.sql`, and the embedding
model: `docker compose exec ollama ollama pull mxbai-embed-large`.

## Use it with your own data

1. **Library → Databases → Add database**: a Postgres host, database and a **read-only** login. StateSync
   checks the login cannot write (no admin role, no write privilege on any table) and stores the password
   encrypted. Choose the schema to use.
2. **Library → Documents**: create a folder and upload PDFs, text or Markdown. Each file is parsed, cut into
   page-bound passages and embedded; it is searchable when its status says `ready`.
3. **Workspaces → New workspace** on that database. Optionally describe the business in a few lines, press
   **Draft with AI** for table and column notes, edit, **Approve**, and pick the document folders.
4. **Ask**. `python3 scripts/check_new_workspace.py` does all of this through the API, on a database and a
   document StateSync has never seen (`./demo/local_shop_db.sh` starts the database).

## Repository map

| Folder | Holds |
|---|---|
| `orchestrator/` | the pipeline: router (`front_door.py`), SQL agent, documents engine, answer writer (`nodes/`), the web API (`main.py`, `*_api.py`) |
| `mcp/` | the tool server: the SQL safety gate (`tools/data_tools.py`), vector search, workspaces, library, history, connected databases |
| `parser/` | the isolated file parser |
| `web/` | the web app (React, TypeScript, Vite, Tailwind) and its tests |
| `config/` | `flags.toml` (models, the Jev switch), `statesync.toml` (every number and limit), the error rules, the demo's suggested questions |
| `migrations/` | the Postgres schema, in order |
| `tests/` | the Python tests (`pytest tests/`); the web tests are in `web/` |
| `scripts/` | operator tools: sign-in tokens, the connection key, migrations, pruning, and the live checks |
| `demo/` | the demo databases: the SEC figures (`sec_db.sh`) and a small shop (`local_shop_db.sh`) |
| `research/` | the papers, experiments and their results, run against the tagged versions (not the product) |

## Tests

```bash
python -m pytest tests/ -q                        # the Python suite (CI installs both services' dependencies)
./scripts/lint-no-literals.sh                     # no limits written into the code
cd web && npm run lint && npm run typecheck && npm run test:unit && npx playwright test   # web: lint, types, unit, browser (mocked API)
python3 scripts/check_demo.py                     # live: the demo questions through the running stack
python3 scripts/check_new_workspace.py            # live: a new database and document, end to end
```

## Limitations

- Postgres databases only; PDF, plain text and Markdown documents only (no OCR for scanned PDFs).
- The router's database card is capped at 4,000 characters and a scan reads at most 60 tables, so a very wide
  database is summarised. Vector search is an exact scan: fine for tens of thousands of passages (the demo has
  44,922), not millions.
- Two helpers assume documents titled `<COMPANY> <YEAR> ...` (as the demo's filings are): narrowing a
  two-source question to the companies in the SQL result, and carrying a company into a follow-up. With other
  titles they simply do not fire. The Jev routing wording was tuned on financial filings.
- English only. Sign-in tokens do not expire; the rate limiter forgets on restart.
- The demo library holds Walmart's 10-Ks only up to fiscal 2020, so "2022" questions about Walmart's strategy
  are answered from older filings (the answer cites them).
