# StateSync v3 — plan

> **This is the direction as of 2026-09-26. It supersedes `V2_STRATEGY.md` as the plan.**
> Facts that v1 and v2 measured stay valid where they are cited here. Everything else in
> V2_STRATEGY (the paper track, the workstream sequence) is replaced by this file.

---

## 1. Goal

**A finished, hosted, full-stack AI analytics product that shows the author can build
usable, secure, measured AI infrastructure.**

The audience is a reviewer (a hiring manager or an engineer) who gives it about five
minutes. The project succeeds when that reviewer can:

1. **Try it in two minutes** from a link, with no install and no sign-up.
2. **See it work every time** on the demo path.
3. **See the engineering judgment in the first screen of the README**: evaluation,
   security, trade-offs.
4. **See that it is finished.** A smaller product that fully works beats a larger one
   that mostly works.

**What "bug-free" means here.** No real system is bug-free, and reviewers know that.
The target is:
- the demo path never breaks;
- every failure produces a clear message, never a stack trace or a bare 500;
- every core flow has a test and CI runs it;
- AI quality has an eval, with its caveats stated;
- known issues are listed in the open instead of hidden.

## 2. Parked, and why

Recorded so that none of these gets silently reopened.

| Parked | Why | Revisit when |
|---|---|---|
| The paper track, including "the generator writes its own evidence" | It competes for the same hours and adds nothing a reviewer can use | After v3 is live |
| Selling to small businesses | Not the goal. The generic product already sells for $0–30 a month (Zoho Analytics, AI for Database) | Never, as part of this plan |
| Chasing novelty | Docs + SQL + dashboards + setup already exists as a whole (DB-GPT, WrenAI, ThoughtSpot, Databricks, Snowflake). The identity is "numbers you can trust", not uniqueness | — |
| The cycle protocol (`docs/cycles/`, `scripts/cycle.py`, audits) | Replaced by: plan → delegate bulk work → verify by execution | — |

## 3. Where it stands (2026-09-26)

**Built:** seven services (postgres, qdrant, ollama, mcp, orchestrator, evaluator,
frontend); 15 migrations; 169 tests; CI running the deterministic gates; 191 commits.

- The orchestrator holds no database credentials. Every read goes through the MCP
  server, which is the sole credential holder, on an isolated network.
- Three layers of SQL defence: a parser gate (which also rejects SELECTs that read no
  table), a read-only role, and statement limits.
- Documents and SQL are chained: the hybrid route's SQL result narrows which documents
  are searched, in one fused query.
- Bearer-token auth, a per-identity audit log, per-user history, rate limits.
- Two corpora: 550 synthetic orders, and 1,551,698 real Olist rows. 18 policy/SLA/
  agreement PDFs and 2,567 review digests, in 20,245 chunks.
- Every answer cites its SQL or document source. A statement the database never ran is
  never cited.
- **LLM provider: Groq since 2026-09-26** (`openai/gpt-oss-120b` for SQL, answers and
  the judge; `qwen/qwen3.8-27b` for the short classification roles). The numbers below
  were measured on the local 7B and on Gemini, not on Groq.

**Measured (with the caveats that travel with each number):**

| What | Result | Caveat |
|---|---|---|
| Hard real-data questions (joins) | local 7B **50%** · Gemini **100%** | Small, self-written set |
| Easy real-data questions | 7B 88.9% · hosted 94.4% | Questions name their columns: this measures dictation, not schema linking |
| Multi-turn follow-ups | QM 47.2% · IM 16.7% | Every benchmark conversation is exactly 3 turns long |
| Adversarial suite | 43/44 | The one failure is kept on purpose (open_items: M8) |

**Against the goal in §1:**

| A reviewer checks | Today |
|---|---|
| Can I try it? | No. Seven services, secret files, a pasted token and a local model. No live link |
| Does it look like a product? | No. The plain-JavaScript UI looks like an engineering tool (replaced in Phase 2 by `web/`) |
| Does it work reliably? | Improving. Phase 0 fixed history silently stopping after 4 turns, and one user being able to write into another's conversation |
| Is the AI measured? | Yes, unusually well. This is the strongest signal |
| Is it secure? | Yes, unusually well |
| Can I understand it quickly? | No. 26 markdown docs and 47 JSON reports in `docs/`, plus audit and cycle logs |

The hard-to-fake parts (evals, security) exist. What is missing is the easy-to-see
part: a live demo, a designed UI, a deployment and a clear README.

## 4. The product when v3 is done

| # | A user can… |
|---|---|
| J1 | Open the link and click **Try the demo**: a read-only guest workspace on sample data, no sign-up |
| J2 | **Set up a workspace** from a sample database: describe the business, let the scan run, approve the AI's draft descriptions, run a 10-question setup test and see the score |
| J3 | **Ask questions** answered from the database, the documents, or both, with follow-ups, history and a cited source for every number |
| J4 | **Pin** a chart or metric to a dashboard. Tiles refresh from saved SQL and say when they were last updated |
| J5 | **Upload documents** (text PDFs) into a workspace |
| J6 | **Self-hosted only:** connect their own Postgres with read-only credentials |
| J7 | Switch on **developer mode**: SQL, traces, latency and cost for each answer |

## 5. Architecture

```
 Browser (React)
    │  HTTPS, session auth
    ▼
 Orchestrator (FastAPI + LangGraph)        ← fixed prompts, context assembly, no DB credentials
    │  JSON-RPC over the internal network
    ▼
 MCP server (sole credential holder)       ← SQL gate, knowledge store, tiles, history, audit
    │                         │
    ▼                         ▼
 Customer / sample DB       StateSync store (Postgres + pgvector)
 (read-only role)           knowledge store · tiles + cache · history · audit
```

The security boundaries that exist today stay as they are: the orchestrator never holds
a database credential, the MCP server is the only thing that reads a database, and the
three-layer SQL gate applies to every statement, including tile refreshes.

### 5.1 Prompts and context

Every model call receives two things.

1. **System prompt, identical for every question in a workspace**
   - The fixed rules, written in code, the same for every workspace.
   - The **business context**: what the owner wrote during setup. It is short (about 200
     words), editable by admins only, versioned and written to the audit log. It sits
     below the fixed rules, is labelled as written by the account owner, and cannot
     override them.
2. **Context looked up for this question** from the knowledge store: the relevant
   tables and columns, matching definitions, two or three similar approved examples,
   matched values, and relevant document passages. Document passages are marked as
   quoted data.

Rules:
- Anything the AI inferred from the data enters the prompt only after the owner
  approves it.
- Document text and database values never enter the system prompt.
- Only what this question needs is sent. More schema context measurably hurt the 7B
  (column comments −19 points, join graph −11); larger models cope better, but the rule
  holds.
- Keeping the identical parts first lets the providers' prompt caching cut cost and
  latency.

### 5.2 Knowledge store (per workspace, in the StateSync store, never in the customer's DB)

| Piece | Built from | Used for | Refreshed when |
|---|---|---|---|
| Schema catalog | Scan: tables, columns, types, keys, row counts, fingerprint | What exists; noticing change | The fingerprint changes (daily check) |
| Descriptions | AI draft from names, sample values and the business context; owner edits | Picking the right tables | The owner edits them |
| Definitions and glossary | The owner's answers plus AI drafts ("revenue", "RTO") | Consistent numbers | The owner edits them |
| Approved examples | The setup test and pinned tiles | Reusing SQL known to be right | A person edits them |
| Value index | Distinct values of chosen text columns, with counts | Matching spellings (low priority) | Nightly, or when a lookup finds nothing |
| Document index | Chunks, embeddings, and which entity each document concerns | Document answers; linking a contract to a seller | A document is added or removed |

**Fast paths:** a question that matches an approved one runs the approved SQL directly,
with no generation, the same number every time, and a "verified" label.

### 5.3 Dashboard tiles

- **Pinning saves** the exact SQL (already through the gate), the chart spec, the data
  source, the author, the time and the schema fingerprint. It does not save the English
  question.
- **Refresh** re-runs the saved SQL when the tile is opened and older than its interval,
  plus a manual button. It never asks the model again, which removes definition drift
  (the Olist demo reported 96,478 delivered orders in one turn and 96,470 in another).
- **Schema change:** a fingerprint mismatch marks the tile "needs review" instead of
  showing a wrong number.
- **Editing:** the AI proposes new SQL, a person approves it, and the tile keeps its
  version history.
- **Pinned tiles become approved examples**, so the chat and the dashboard agree.

### 5.4 Caching and retention

| What | Lives | Refreshed |
|---|---|---|
| Knowledge store | Long-term | See §5.2 |
| Approved queries (tiles, examples) | Permanent, versioned | Only when a person edits |
| Tile results | Until the tile's next refresh | On open past the interval, or the button |
| Repeat-question answers | Minutes to hours | When the underlying data refreshes |
| History and result rows | Retention policy (1,000 rows / 1 MiB per turn, 30 days: exists) | — |
| Audit log | 180 days, `scripts/prune_audit_log.py` run by the operator | — |

Rules:
1. Every cache key includes the workspace ID.
2. Cache results (SQL + rows + timestamp), never reasoning.
3. Cache AI-written SQL only after a person approves it. Turning memory on dropped a
   10-question check from 10/10 to 7/10.
4. No scheduler at first: refresh on open. A background worker comes later, for alerts.

### 5.5 Multi-tenancy and security rules

- Every row in the StateSync store carries a workspace ID. Postgres row-level security
  is the backstop, and a test fails if any table lacks it (CVE-2025-48757 is what
  happens without one: 170 of 1,645 scanned Lovable-built apps exposed data).
- Vector search is filtered by workspace inside the query. Memory already filters by
  tenant and schema fingerprint this way.
- Database credentials are encrypted, and only the MCP server can decrypt them.
- Uploaded files are parsed in an isolated worker with no network access and size and
  page limits.
- The public site runs on sample data only. Connecting your own database is a
  self-hosted feature.
- Paid model calls have per-user and global spending caps.

## 6. Build vs buy

**Adopt (commodity, security handled by the tool when configured correctly):**

| Part | Use | Watch out for |
|---|---|---|
| Accounts, roles, password reset, 2FA | Keycloak (self-hosted), or Clerk / Auth0 / WorkOS / Supabase Auth | Keep StateSync's bearer tokens for scripts and the API only |
| Stored credentials | Cloud KMS, or Vault / OpenBao | Never in a plain column |
| Workspace isolation | Postgres row-level security | Must be on for every table (see §5.5) |
| Other SQL dialects | sqlglot (keep pglast for Postgres) | The parser describes a query; the allow/deny policy stays ours |
| PDF parsing | Docling (MIT) or Unstructured (Apache 2.0); Tesseract for scans | PyMuPDF is AGPL: a hosted service must publish its source or buy a licence |
| Spreadsheet uploads | DuckDB | Set `enable_external_access = false`, or queries can read server files |
| Vectors | pgvector (already here) | Retire Qdrant: one database, one set of isolation rules |
| Background jobs | Procrastinate (Postgres-backed queue) | — |
| Masking personal data | Microsoft Presidio | — |
| Developer mode | Langfuse (self-hostable) + OpenTelemetry | Traces hold customer data: same isolation |
| Errors | Sentry | Scrub customer data |
| Rate limits | Cloudflare at the edge + a shared counter | Today's limiter counts in one process's memory: two servers double the limit |
| HTTPS | Caddy | — |
| Frontend | React, shadcn/ui (Radix), Tailwind, Lucide, react-grid-layout; keep Vega-Lite | Keep no-`eval` CSP and no raw HTML from data |

**Build ourselves (this is the product):** the SQL gate and fabrication blocking,
provenance on every number, the setup flow and knowledge store, context assembly, tiles,
and value matching.

**Not adopted: LangChain's SQL agent as a safety layer.** Its own security note says it
relies on the database credentials you give it and may attempt `DROP TABLE` or `INSERT`
if prompted.

**Deferred: Cube (Apache 2.0 semantic layer).** It would supply definitions, per-tenant
row rules, cached rollups and an MCP API. It costs another service and moves the SQL gate
out of the centre. Revisit if definitions or caching outgrow a few tables.

## 7. Scope of v3 and definition of done

**In:** J1–J7 from §4 · sample databases (Olist, the synthetic corpus, possibly one
more) · connecting your own Postgres in self-hosted mode · text PDFs · a hosted model as
the demo default, with the local model optional.

**Out:** MySQL and other databases · scanned PDFs · alerts sent outside the app (email, Slack) ·
billing · single sign-on · value matching and multilingual expansion beyond what exists
(low priority) · the local 7B as default · Paper A.

**v3 is done when:**
1. A reviewer goes from the link to an answered question with a cited source in under
   two minutes, without signing up.
2. A new user sets up a workspace from a sample database (business form, approved
   descriptions, 10-question setup test) and gets at least 8 of the 10 right.
3. Three pinned tiles survive a refresh with identical SQL and a current timestamp, and
   a schema change flips a tile to "needs review".
4. CI is green: unit tests, the RLS-coverage test, and an eval smoke test.
5. The README tells the story in 60 seconds: live link, 2-minute video, architecture
   diagram, key numbers with caveats, design decisions, known issues.
6. The app opens on a live dashboard: an AI-built Overview whose numbers visibly update
   within a minute (Phase 2b).

## 8. Phases

Ordered. Each phase ends with a check a machine can run.

### Phase 0 — Clean up (done)

| ID | Item | Type | Handled | Status |
|---|---|---|---|---|
| 0.1 | **History silently stops saving after the 4th turn.** `rewrite_node` sets `turn_index = len(recent_turns) + 1`, and recent turns are capped at 3 (`rewrite_history_turns`). The insert is `ON CONFLICT … DO NOTHING`, so every turn from the 5th is dropped without an error. A history-read failure also resets to turn 1 and is dropped the same way. **Confirmed by execution 2026-09-26:** the real node assigns turn 4 to a 6th question. Benchmarks missed it because every benchmark conversation is 3 turns | User-facing bug | In-house | ✅ `3d6279b` |
| 0.2 | **Two users can collide on one session** (PR #2 review). `/query` accepts a client-supplied `session_id`, and the unique key ignores `principal_id`, so another user's writes into that session are silently dropped. Fix: the server owns session IDs, a session belongs to the user who created it, and a supplied ID owned by someone else is rejected | Security | In-house | ✅ `3d6279b` |
| 0.3 | PR #2's two smaller review notes: the History button still says "local dev only"; migration 015's comment calls `sql_rows_count` the true count, but it is capped at `max_rows` | Accuracy | Delegable | ✅ `b36c803` |
| 0.4 | **A missing model was a bare 500 whose detail leaked the internal error.** The configured `qwen2.5:7b` was not on this machine's Ollama (it has `qwen2.5:14b`), so every question failed. Decided 2026-09-26: Groq is the provider (see §3). Still to do: a provider or model failure returns a clear, generic error with no internal detail, and `/health` reports whether the model provider is ready without calling it on every probe | User-facing bug + decision | In-house | ✅ Groq `c476093`; clear errors and `/health` readiness `9ac8fae` |
| 0.5 | **Database collation mismatch.** The data was created under glibc 2.36; the running image has 2.31. Text indexes, including on the Devanagari columns, can return wrong results. Fix without rebuilding postgres: `pg_dump` backup, `REINDEX`, `ALTER DATABASE … REFRESH COLLATION VERSION`, then pin the Dockerfile base to the image actually running. **Owner approval before running** | Correctness | In-house | ✅ `6959b40` |
| 0.6 | The supervisor's fallback route loops over a `frozenset`, so the chosen route depends on per-process hash order. Make the order fixed; add a test | User-facing bug | Delegable | ✅ `8da6026` |
| 0.7 | The audit log has no retention. Extend the prune script, with limits in config | Operations | Delegable | ✅ `0b79dfe` (operator script; the app role cannot delete audit rows) |
| 0.8 | A padded literal `' Lucknow '` (d_ml_033). Find where the padding enters (preserved entity or generation), fix it at the source, add a test | User-facing bug | In-house diagnosis | ✅ Closed: not reproducible on Groq (3/3 correct). Cause was the local 7B detecting Hinglish as English, so no entity was preserved and its SQL padded the literal. General defence: value matching (After v3) |
| 0.9 | Tooling: `run_e2_v1.py` reads an unregistered `--force`; the `SchemaRetriever` Protocol omits the `schema` parameter its only implementation needs | Code quality | Delegable | ✅ `06216c2` |
| 0.10 | Hygiene: drop the obsolete `version:` key from both compose files; decide whether `.claude/launch.json` is committed | Hygiene | Delegable | ✅ `06216c2` (compose); `.claude/launch.json` committed with the plan update |
| 0.11 | **Merge PR #2** once 0.1–0.3 are in | — | Owner approves | ✅ merged as `83bdea8` (merge commit, so the SHAs cited here stay valid) |
| 0.12 | **Document search silently missed the right document.** Found verifying 0.5: the approximate HNSW index returned review digests (~0.55) instead of LP003's annexure (0.77, the exact top match), so a question the documents answer came back as "not found". Known as approximate since cycle 006; the rebuild made it fail outright, and `hnsw.ef_search` recall was not monotonic (found at 100 and 200, lost at 400). Fix: exact search, 71 ms over 20,245 chunks, behind `retrieval.exact_vector_search` | User-facing bug | In-house | ✅ `2ff96ff` |

**Exit:** pytest green, including new tests for 0.1, 0.2 and 0.6 · CI green ·
`verify_deployed.py` clean on the running services · a 6-turn conversation stores 6 rows ·
a second user cannot write into another user's session · a missing model returns a
readable error · PR #2 merged.

### Phase 1 — Dashboard tiles (backend and API) — ✅ done (`0f92cf4`, `848ed0e`)

**Exit met, checked live by `scripts/verify_tiles.py` (36/36):** a refresh returns the
identical SQL with a newer timestamp; a changed fingerprint flips the tile to
`needs_review` and keeps the last good result; another principal is refused on every
route. The API Phase 2 builds on:

| Route | Does |
|---|---|
| `POST /tiles` `{session_id, turn_index, title?, refresh_interval_s?}` | Pin a stored database answer (201) |
| `GET /tiles` | All my tiles, each refreshed first if due |
| `GET /tiles/{id}` | One tile, with its version history |
| `POST /tiles/{id}/refresh` | Re-run now |
| `POST /tiles/{id}/replace` `{session_id, turn_index}` | New version from a follow-up answer (the "edit") |
| `PATCH /tiles/{id}` `{title?, refresh_interval_s?}` | Rename, change interval |
| `POST /tiles/{id}/confirm` | Accept a schema change (new version) |
| `DELETE /tiles/{id}` | Unpin |

Every tile comes back with `chart.data.values` bound to its current rows, plus `state`,
`stale`, `result.computed_at` and `last_error` for the UI to show.


- Migration: `tiles` (saved SQL, chart spec, source, author, created, schema fingerprint,
  refresh interval, version) and `tile_results` (rows, computed_at).
- MCP tools: pin, list, refresh-if-stale, force refresh, unpin, edit (propose → approve).
  Every refresh passes the SQL gate. Built as ONE method, `tiles.manage`, taking an
  action. The ceiling was full at 15 after `history.check_session` (0.2), so even one
  method needed it raised: decision D-v3-1 below.
- Edit is "replace from a turn": the user asks the change in chat ("same, last 30 days"),
  and replacing the tile with that answer is the approval. The tiles API never accepts
  a SQL string, so it cannot become a SQL console.
- Fingerprint mismatch → "needs review".
- **Exit:** tests show a refresh returns identical SQL and a new timestamp; a changed
  fingerprint flips the state; a tile cannot be read across workspaces.

### Phase 2 — New frontend — ✅ done (branch `v3-phase2-frontend`)

**Exit met:**
- Every J3 and J4 flow has a browser test: 50 against the built app with the API
  mocked (`web/e2e/mocked`: chat, dashboard, chart safety), and 7 against the real
  stack behind nginx (`web/e2e/live`: ask, chart, pin → dashboard → unpin, and the CSP).
- The CSP still blocks `eval`: the live suite shows the browser refusing `eval` and
  `new Function` on the page nginx serves. The same run against a server without the
  CSP fails, so the test would catch a regression. `verify_e2` checks the header text
  and the headers served live.
- The old frontend is removed (`aa53370`).

**Built** (design: `web/DESIGN.md`): React 19, TypeScript, Vite, Tailwind v4 and
shadcn/ui.
- Chat: sign-in, history, dataset switch, charts, citations, CSV and PNG export, a
  details panel and developer mode. Conversations keep their dataset: migration 018,
  with a 409 on a mismatch.
- A dashboard grid for Phase 1's tiles.
- Served by nginx with `script-src 'self'` only. The orchestrator URL is runtime
  config.

**The old UI's checks, retired or replaced:**

| Old check | Now |
|---|---|
| `window.__ORCHESTRATOR_URL__` sed literal and its `verify_deployed` pin | `/config.js`, written by `web/entrypoint.sh` after it validates `ORCHESTRATOR_URL`. `verify_deployed` checks the file carries the container's URL |
| `app.js?v=20260820` cache-buster test | Retired: Vite content-hashes every asset. Stale-image detection is now a source fingerprint (`/build-info.json` in the image, recomputed by `verify_deployed`) |
| `verify_e2` source greps (`interp`, `ast: true`, the eval-compiler refusal string) | `verify_e2` reads the options of VegaChart's single `embed()` call, with comments stripped: `ast: true`, `expr: expressionInterpreter`, `tooltip: false`. No other module may import vega. Proving eval cannot run is now the live browser test's job |
| Vega width on the spec, not the option | Kept. The mocked chart test fails if the chart does not span the answer (726 px vs 137 px measured) |
| No vega-tooltip | Was only half kept: the theme turned off automatic tooltips, but vega-embed still installed vega-tooltip (an `innerHTML` writer), and a spec's tooltip channel could switch it on. Now `tooltip: false` is set on `embed()`, pinned by `verify_e2` and a browser test |
| The 10 malicious specs, run by hand in the browser (E2-V3/V6, browser half) | `web/e2e/mocked/chart-safety.spec.ts`: each spec goes through the real render path, must name its violation, draw nothing and fetch nothing |

### Phase 2b — Dashboard first (owner decision, 2026-09-27) — ✅ done (PR #4)

**Exit met (2026-09-28):**
- The app opens on Dashboards. On `live`, a card's number changes on screen within a
  minute (live browser test, against the real stack).
- "Build with AI" added at least 6 working cards on each of `live`, `olist` and
  `public`: 8 of 8 on each, in the browser and in `verify_dashboards.py --build`
  (66/66).
- Every visualization kind renders in the mocked suite. Browser-built specs pass A7
  (vitest).
- Ownership of dashboards and conversation deletes is checked with a second principal.
- Suites: 66 mocked browser tests (330/330 with each run five times), 48 unit tests, 8
  live browser tests.


The owner's review of Phase 2: the app reads as a chat log with a pin button. It lands
on chat, draws only bar charts, has no motion, and its dashboard is a plain list of
pinned answers. Decided (D-v3-4): the dashboard becomes the product, and chat becomes
how you ask for new insights. This phase comes before Phase 3.

**What changes:**

- **Look and motion.** The owner's reference style is the default: dark navy with
  cyan/teal and purple accents, with light mode kept. There are page transitions, cards
  that animate in and lift on hover, numbers that count up, and animated layout changes.
  All motion is off under `prefers-reduced-motion`.
  - The home page is the dashboard.
  - A slim navigation rail holds Dashboards and Ask. Chat history moves inside Ask.
  - The dataset picker sits in the top bar, next to a connection/model status light.
- **Dashboards, several per dataset** (migration 019). They are stored server-side with
  their layout, so a dashboard the AI builds arrives already arranged.
  - An edit mode shows the handles, and each card offers size presets.
  - A tile can move between dashboards.
  - The MCP gains `dashboards.manage` (method 17 of 20, D-v3-5).
- **More visualizations, chosen from the data.** A client-side presentation layer picks
  the default from the result's shape:
  - one number → KPI;
  - a time series → trend (area), or a KPI with % change and a sparkline;
  - a few shares → donut or breakdown bars;
  - a ranking → bars sorted by value (horizontal when labels are long);
  - two categories → heatmap; two measures → scatter; anything else → table.

  Every tile and answer has "Show as". The server's chart proposal (what E2-V1
  measures) is unchanged, and it is used as a hint (D-v3-8). Every Vega spec the client
  builds still passes the A7 check before render. KPI, breakdown and sparkline cards are
  plain React, with no HTML sinks.
- **Live data** (migration 020, D-v3-6). A `live` schema (orders, deliveries, reviews,
  web sessions) is fed by a new `simulator` service. The service backfills 90 days, then
  appends events every few seconds under its own role, which can write only to `live`.
  Olist and the synthetic `public` set stay frozen for the accuracy tests.
  - Dashboards poll, and tiles on `live` refresh every 30 s (the refresh floor drops to
    15 s, D-v3-7). A changed number animates.
  - A refresh re-runs the saved SQL: no model call.
- **AI-built dashboards.** "Build with AI" asks the planner for 6–8 insights for the
  dataset (optionally for a stated goal), using the `decomposer` role (D-v3-9).
  - Each insight runs through the normal pipeline as a turn in a builder session, then
    is pinned with a title, a visualization and a layout slot.
  - There is no new SQL path: every tile still comes from a stored turn that passed the
    SQL gate.
  - The job reports progress, and cards appear as they finish. An insight that fails is
    skipped, and the reason is reported.
- **Insights strip.** Short sentences computed from the tiles' own rows, such as "Orders
  today: 1,284, up 12% on yesterday", and the largest shares. The model never writes a
  number.
- **Polish carried over from the Phase 2 review:**
  - "1 row" plurals;
  - lists in answers render as lists;
  - sources show document titles;
  - no backdrop blur;
  - conversations can be deleted (`history.delete_session`, method 18);
  - the status light;
  - developer mode shows the chart spec;
  - the details panel no longer repeats the SQL;
  - follow-up questions pin under their rewritten wording ("Bottom 5 states by
    customers", not "And bottom?");
  - readable axis and KPI labels;
  - the README's quick start and shutdown.

**Exit:**
- The app opens on the `live` Overview dashboard, and at least one number visibly changes
  within a minute.
- "Build with AI" produces at least 6 working cards on each of `live`, `olist` and
  `public`.
- Every visualization kind renders in the mocked suite, and each passes A7.
- Ownership holds on dashboards and deleted conversations: the live verify script
  checks this with a second principal.
- CI is green, and PR #4 merges.

### Phase 3 — Workspaces: setup, knowledge store, uploads

**Goal:** a person can turn a database into a working, measured workspace in a few
minutes: describe the business, let StateSync scan and draft, approve, and see a score.

**Design (decided 2026-09-28):**

- **Workspace** = a name, a data source (a sample dataset, or in self-hosted mode an
  external Postgres connection), the owner's business context (versioned), a knowledge
  store, its documents, its dashboards, and a setup score. It is owned by one principal
  in one tenant. Demo datasets (`live`, `olist`, `public`) stay usable without one.
  Migration 021 adds `workspaces`, `business_context_versions`, `kb_columns`,
  `kb_definitions`, `kb_examples`, `setup_runs`/`setup_results`, and `workspace_id` on
  dashboards.
- **Row-level security is the backstop (§5.5).** Every table in `session_state` and
  `workspace_docs` gets RLS, with a policy on `tenant_id =
  current_setting('statesync.tenant_id')`; the MCP sets it at the start of every
  transaction from the session's tenant. Child tables without their own tenant column
  are scoped through their parent. The RLS-coverage test fails if any table in those
  schemas lacks RLS or a policy: statically over the migrations, and live over
  `pg_class`/`pg_policies`.
- **Setup wizard**: describe → scan → AI drafts → approve → test.
  - The scan stores the catalog (tables, columns, types, row counts, fingerprint).
  - The drafts are table and column descriptions plus definitions of key terms,
    written from names, sample values and the business context. They are stored as
    unapproved drafts. Only approved knowledge ever enters a prompt (§5.1).
  - **The setup test is graded objectively where it can be.** For a sample dataset, the
    10 questions come from its curated reference set, each with ground-truth SQL, and
    are scored by BIRD-style execution accuracy (`scorers.ExecutionAccuracyScorer`).
    This covers `fixtures/golden/olist.json` and `phase_c2.json`, plus a new `live` set.
  - For an external database, the questions are AI-proposed, and the owner marks each
    answer right or wrong, with its SQL shown.
  - Right answers become approved examples for that workspace only. Evaluations run
    without a workspace, so the benchmark sets are not contaminated.
- **Context assembly (§5.1)** runs only when a question carries a workspace, so
  measured behaviour without one is unchanged:
  - the business context goes into the SQL agent's system prompt, below the fixed rules
    and labelled as the owner's words;
  - approved descriptions, the definitions a question mentions, and up to three similar
    approved examples go into the context block;
  - **fast path**: a question matching an approved example (normalised text) runs its
    approved SQL, and the answer is labelled "Verified".
- **Uploads**: text PDFs are parsed by a new `parser` service on an internal-only
  network, with no internet, no database credentials, a non-root user, size and page
  limits, and a timeout.
  - It uses pypdf, not Docling (D-v3-11): Docling pulls in about 2 GB of layout models
    that text PDFs do not need.
  - The MCP chunks and embeds the pages into a new `workspace_docs` schema, not the
    sample corpus's tables, whose shape the A5 fingerprint pins.
  - Retrieval searches the workspace's documents alongside the sample corpus.
- **Own Postgres (self-hosted only, off by default):**
  - Credentials are encrypted with a key only the MCP holds.
  - Connecting checks that the role is really read-only (not superuser, no write
    privilege on any table, read-only transactions work).
  - The workspace's queries run on that connection behind the same three-layer gate.
  - The MCP picks the connection from the session's workspace, so the orchestrator
    still holds no credential.
- **Synthetic-schema code** (the DIGIPIN resolver, `_local` script repair, the
  normaliser's column rules, the supervisor's domain examples) is switched on per
  dataset from config and stays on for `public` only, so its measured behaviour does
  not change.
- **MCP methods:** `workspaces.manage` and `docs.manage` bring the count to 20 of 20
  (D-v3-10).

**Slices:** (1) migration 021 and RLS with its coverage test, and `workspaces.manage`;
(2) scan, drafts, approval and the setup test (backend, with a live verify script);
(3) context assembly and the fast path; (4) the web wizard, workspace switching and the
documents page; (5) the parser service, uploads and retrieval; (6) external Postgres;
(7) the synthetic-schema switches.

**Progress:** slice 1 ✅ (2026-09-28) — migration 021 applied; RLS on all 16 tables of
session_state and workspace_docs; `workspaces.manage` (19 of 20 methods) with the
knowledge store, approval, examples, context and setup-run bookkeeping; `/workspaces`
routes; `scripts/verify_workspaces.py` 58/58 live (including: no tenant bound sees no
rows, a forged tenant write is refused by Postgres, existing dashboards/tiles/sessions
unaffected). Slice 2 already has its storage side (scan, catalog, runs); what remains
there is the AI drafting and the setup-test runner.

- **Exit:** done-criterion 2 in §7 passes on two different sample databases (olist and
  public): a workspace set up through the wizard scores at least 8 of 10 on its setup
  test.

### Phase 4 — Deploy

- One server: Caddy, a hosted model with spending caps, a guest demo workspace, and
  sample data only.
- Real accounts through the chosen auth provider; bearer tokens kept for the API.
- A shared rate-limit counter; Langfuse and Sentry for developer mode.
- Drop the 7B chat model from the hosted build (embeddings may stay local); retire
  Qdrant into pgvector; the evaluator becomes an offline or CI tool.
- **Exit:** done-criterion 1 in §7, from a clean browser.

### Phase 5 — Package

- The README (done-criterion 5), a 2-minute video, an architecture diagram, and a
  design-decisions page.
- Move cycle and audit history into `docs/history/`. Tests read `docs/*.json`
  (provenance coverage), so references move with the files.
- A short public write-up.
- **Exit:** a reader who has never seen the project can state what it does, how it is
  secured and how well it works, from the README alone.

### After v3 (low priority)

Value matching (Bombay → the spellings actually stored), MySQL, OCR, alerts, single
sign-on, and document-based tile targets ("on-time vs the 36-hour SLA in the contract").

## 8a. Decision log

Decisions that change a locked constant or a recorded design rule (config/statesync.toml's
header requires a record for any change there).

| ID | Date | Decision | Why |
|---|---|---|---|
| D-v3-1 | 2026-09-26 | `mcp.tool_ceiling` 15 → 20; `mcp/main.py` reads it from config, and `mcp/Dockerfile` no longer bakes `ENV TOOL_CEILING=15` (which had silently overridden the config) | The ceiling came from the M9 over-exposure experiment, but the supervisor never sees MCP methods (D1 decoupled routing from MCP; `scripts/experiment_tool_ceiling.py`), so it guards API sprawl, not model accuracy. Tiles add one method (16 in use); Phase 3 needs room |
| D-v3-2 | 2026-09-26 | Retrieval is exact (`retrieval.exact_vector_search = true`) | 0.12: the HNSW index missed the right document outright; exact search costs 71 ms at this size |
| D-v3-3 | 2026-09-26 | Groq is the provider | 0.4: the shipped local model was missing; v3 demos on a hosted model |
| D-v3-4 | 2026-09-27 | A dashboard-first phase (2b) comes before Phase 3 | Owner's review: the product read as a chat log; for a portfolio, the first screen must be a live, designed dashboard |
| D-v3-5 | 2026-09-27 | MCP methods 17 and 18: `dashboards.manage`, `history.delete_session` (ceiling stays 20) | Dashboards are their own resource; deleting a conversation is a separate, destructive action worth its own audited method. Two slots remain for Phase 3; revisit the ceiling there |
| D-v3-6 | 2026-09-27 | A `live` demo schema fed by a `simulator` service with its own write-only-to-`live` role | Refreshing tiles only mean something if the data moves; the datasets the accuracy tests use must not |
| D-v3-7 | 2026-09-27 | `tiles.min_refresh_s` 60 → 15; tiles on `live` default to 30 s | A refresh is one indexed SELECT on a small schema, no model call; 30 s is what makes the dashboard feel live |
| D-v3-8 | 2026-09-27 | Presentation (chart kind, sort, labels) is decided in the browser from the rows; the server's chart proposal is unchanged | Changing the server's spec would move E2-V1's measured chart score; presentation is not what that metric grades |
| D-v3-9 | 2026-09-27 | The dashboard planner uses the `decomposer` role | Planning a dashboard is decomposing a goal into questions; a new role would add a config entry per provider for no gain |
| D-v3-10 | 2026-09-28 | MCP methods 19 and 20: `workspaces.manage`, `docs.manage`; the ceiling stays 20 | Workspaces, the knowledge store and connections share one method; documents another. The ceiling is now full: a further method needs a new decision |
| D-v3-11 | 2026-09-28 | Text PDFs are parsed with pypdf, not Docling | Docling's layout models are about 2 GB and scanned PDFs are out of scope; pypdf is BSD-licensed (PyMuPDF is AGPL) |
| D-v3-12 | 2026-09-28 | Setup tests on sample datasets are graded by execution accuracy against curated reference SQL | An objective score beats the owner grading their own demo; right answers become approved examples for that workspace only, so the benchmarks stay clean |

## 9. Issue register

| Issue | Type | Status | Where |
|---|---|---|---|
| History drops turns after the 4th | Bug | **Fixed** `3d6279b` | 0.1 |
| Cross-user session collision | Security | **Fixed** `3d6279b` | 0.2 |
| Missing model → bare 500 leaking internals | Bug | **Fixed** `c476093`, `9ac8fae` | 0.4 |
| Collation version mismatch | Correctness | **Fixed** `6959b40` | 0.5 |
| Supervisor fallback depends on hash order | Bug | **Fixed** `8da6026` | 0.6 |
| Audit log has no retention | Operations | **Fixed** `0b79dfe` | 0.7 |
| Padded `' Lucknow '` literal (d_ml_033) | Local-7B artifact | Closed; not reproducible on Groq | 0.8 |
| `run_e2_v1.py --force`; `SchemaRetriever` Protocol | Tooling | **Fixed** `06216c2` | 0.9 |
| "Delivered" count drifts between turns | Consistency | Present | Phase 1 (tiles), Phase 3 (definitions) |
| Missing `fulfillments` join (012_en) | Model limitation | Present | Phase 3 (definitions, approved examples) |
| In-process rate limiter | Scaling | Present | Phase 4 |
| Code tied to the synthetic schema | Generality | Present | Phase 3 |
| adv_14_strict (debug fields keep a rejected proposal) | Decided | Kept on purpose | — |
| Approximate vector index missed the right document | Bug | **Fixed** `2ff96ff` (exact search) | 0.12 |
| Retrieval metrics (M6, E1) measured under the approximate index | Measurement | Not re-measured | Before the README cites them |
| D-V2 scorer asymmetry; fixture and instrument items in `open_items.md` | Measurement | Documented | Fix only if the README cites the affected number |
| vega-tooltip installed despite the no-tooltips rule | Security (defence in depth) | **Fixed** (Phase 2) | Its escaping held and the CSP blocks inline handlers; now never installed |
| A withheld chart left the previous chart's canvas in the page | Bug | **Fixed** `aa53370` | Hidden, never shown; the hostile spec never reached Vega |
| Tile refresh crashed on NUMERIC or timestamp results, and one such tile failed its whole dashboard's listing | Bug (Phase 1) | **Fixed** (Phase 2b) | Found by the first live AI build; rows are now normalised, and a failing tile is marked 'error' alone |
| The dashboard grid never measured its width (stuck at 1280px) | Bug (Phase 2) | **Fixed** (Phase 2b) | The empty right side in the owner's screenshot |
| One rate budget for every route: a polling dashboard could block asking | Design | **Fixed** (Phase 2b) | Model routes 30/min, everything else 300/min |
| Answers write raw ratios ("0.0319") | Presentation | Present | Prompt change would move measured scores; cards format ratios as % |

## 10. Rules carried forward

- **Postgres is never rebuilt.** Build by explicit service name; previews use
  `docker compose up --no-build`.
- Restore the shipped flag state after every experiment.
- Run `scripts/verify_deployed.py` before trusting any measurement.
- Never alter a verification, fixture or scorer to make a number look better.
- Branch before committing; `main` is the default branch; commit and push only when the
  owner asks.
- Delegate only work with a machine-checkable acceptance bar. Design, debugging and
  security-sensitive code stay in-house, and delegated work is reviewed by running it.
- Customer content (documents, database values) enters prompts only as data.
- The web app's invariants: charts only through `VegaChart` (A7 check, then the
  interpreter, never tooltips); no HTML sinks (ESLint); security headers at nginx
  server level only; no inline script (runtime config is `/config.js`).

## 11. Risks

| Risk | Response |
|---|---|
| Scope creep: this is a second project | The in/out list in §7 is the contract. New ideas go to "After v3" |
| Accuracy on schemas we did not prepare is unknown | The setup test is the measure, built in Phase 3, not at the end |
| Hosting multiple workspaces widens the attack surface | Sample data only in public; RLS with a coverage test; credentials only in the MCP server |
| Paid model cost and abuse on a public demo | Spending caps, rate limits, a guest workspace with small quotas |
| The UI still looks home-made | A component system and one reference product to match (Linear, Vercel, Metabase or Hex) |
