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
| Does it look like a product? | No. The plain-JavaScript UI looks like an engineering tool |
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
| Audit log | Retention policy (**missing today**, Phase 0) | — |

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

**Out:** MySQL and other databases · scanned PDFs · scheduled refresh and alerts ·
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

## 8. Phases

Ordered. Each phase ends with a check a machine can run.

### Phase 0 — Clean up (next)

| ID | Item | Type | Handled | Status |
|---|---|---|---|---|
| 0.1 | **History silently stops saving after the 4th turn.** `rewrite_node` sets `turn_index = len(recent_turns) + 1`, and recent turns are capped at 3 (`rewrite_history_turns`). The insert is `ON CONFLICT … DO NOTHING`, so every turn from the 5th is dropped without an error. A history-read failure also resets to turn 1 and is dropped the same way. **Confirmed by execution 2026-09-26:** the real node assigns turn 4 to a 6th question. Benchmarks missed it because every benchmark conversation is 3 turns | User-facing bug | In-house | ✅ `3d6279b` |
| 0.2 | **Two users can collide on one session** (PR #2 review). `/query` accepts a client-supplied `session_id`, and the unique key ignores `principal_id`, so another user's writes into that session are silently dropped. Fix: the server owns session IDs, a session belongs to the user who created it, and a supplied ID owned by someone else is rejected | Security | In-house | ✅ `3d6279b` |
| 0.3 | PR #2's two smaller review notes: the History button still says "local dev only"; migration 015's comment calls `sql_rows_count` the true count, but it is capped at `max_rows` | Accuracy | Delegable | ✅ `b36c803` |
| 0.4 | **A missing model was a bare 500 whose detail leaked the internal error.** The configured `qwen2.5:7b` was not on this machine's Ollama (it has `qwen2.5:14b`), so every question failed. Decided 2026-09-26: Groq is the provider (see §3). Still to do: a provider or model failure returns a clear, generic error with no internal detail, and `/health` reports whether the model provider is ready without calling it on every probe | User-facing bug + decision | In-house | ◐ Groq chosen, `c476093`; clear errors and `/health` readiness open |
| 0.5 | **Database collation mismatch.** The data was created under glibc 2.36; the running image has 2.31. Text indexes, including on the Devanagari columns, can return wrong results. Fix without rebuilding postgres: `pg_dump` backup, `REINDEX`, `ALTER DATABASE … REFRESH COLLATION VERSION`, then pin the Dockerfile base to the image actually running. **Owner approval before running** | Correctness | In-house | Approved; open |
| 0.6 | The supervisor's fallback route loops over a `frozenset`, so the chosen route depends on per-process hash order. Make the order fixed; add a test | User-facing bug | Delegable | Open |
| 0.7 | The audit log has no retention. Extend the prune script, with limits in config | Operations | Delegable | Open |
| 0.8 | A padded literal `' Lucknow '` (d_ml_033). Find where the padding enters (preserved entity or generation), fix it at the source, add a test | User-facing bug | In-house diagnosis | Open |
| 0.9 | Tooling: `run_e2_v1.py` reads an unregistered `--force`; the `SchemaRetriever` Protocol omits the `schema` parameter its only implementation needs | Code quality | Delegable | Open |
| 0.10 | Hygiene: drop the obsolete `version:` key from both compose files; decide whether `.claude/launch.json` is committed | Hygiene | Delegable | Open |
| 0.11 | **Merge PR #2** once 0.1–0.3 are in | — | Owner approves | Open |

**Exit:** pytest green, including new tests for 0.1, 0.2 and 0.6 · CI green ·
`verify_deployed.py` clean on the running services · a 6-turn conversation stores 6 rows ·
a second user cannot write into another user's session · a missing model returns a
readable error · PR #2 merged.

### Phase 1 — Dashboard tiles (backend and API)

- Migration: `tiles` (saved SQL, chart spec, source, author, created, schema fingerprint,
  refresh interval, version) and `tile_results` (rows, computed_at).
- MCP tools: pin, list, refresh-if-stale, force refresh, unpin, edit (propose → approve).
  Every refresh passes the SQL gate. **The MCP tool ceiling (`mcp.tool_ceiling` = 15) is
  full** since `history.check_session` (0.2): either one `tiles` tool that takes an
  action, or a recorded decision to raise the ceiling.
- Fingerprint mismatch → "needs review".
- **Exit:** tests show a refresh returns identical SQL and a new timestamp; a changed
  fingerprint flips the state; a tile cannot be read across workspaces.

### Phase 2 — New frontend

- React + shadcn/ui, matching everything the current UI does (sign-in, chat, charts,
  citations, history, corpus switch), plus the dashboard grid for Phase 1's tiles.
- New features are built only in the new UI.
- Retire the old UI's checks deliberately, and replace each with an equivalent where it
  still matters: the `window.__ORCHESTRATOR_URL__` sed literal and its `verify_deployed`
  pin; the `app.js?v=20260820` asset test; `verify_e2`'s source greps (`interp`,
  `ast: true`, the eval-compiler refusal string); Vega width on the spec, not the option;
  no vega-tooltip.
- **Exit:** every J3 and J4 flow is covered by a browser test; CSP still blocks `eval`;
  the old frontend is removed.

### Phase 3 — Workspaces: setup, knowledge store, uploads

- Workspaces with row-level security, plus the RLS-coverage test.
- Setup wizard: business form → scan → AI-drafted descriptions and definitions → owner
  approval → 10-question setup test with a score.
- Knowledge store and context assembly (§5.1–5.2), filling the existing
  `SchemaRetriever` slot.
- Text PDF upload via Docling, in an isolated worker.
- Connect your own Postgres (self-hosted mode): encrypted credentials, and a check that
  the role really is read-only.
- Generalise or retire code tied to the synthetic schema: seven source files (the
  DIGIPIN spatial resolver, the `_local` script-repair convention, column-specific
  normaliser rules, domain examples in the supervisor prompt).
- **Exit:** done-criterion 2 in §7 passes on two different sample databases.

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

## 9. Issue register

| Issue | Type | Status | Where |
|---|---|---|---|
| History drops turns after the 4th | Bug | **Fixed** `3d6279b` | 0.1 |
| Cross-user session collision | Security | **Fixed** `3d6279b` | 0.2 |
| Missing model → bare 500 leaking internals | Bug | Provider fixed; error handling open | 0.4 |
| Collation version mismatch | Correctness | Confirmed | 0.5 |
| Supervisor fallback depends on hash order | Bug | Present | 0.6 |
| Audit log has no retention | Operations | Present | 0.7 |
| Padded `' Lucknow '` literal (d_ml_033) | Bug | Present | 0.8 |
| `run_e2_v1.py --force`; `SchemaRetriever` Protocol | Tooling | Present | 0.9 |
| "Delivered" count drifts between turns | Consistency | Present | Phase 1 (tiles), Phase 3 (definitions) |
| Missing `fulfillments` join (012_en) | Model limitation | Present | Phase 3 (definitions, approved examples) |
| In-process rate limiter | Scaling | Present | Phase 4 |
| Code tied to the synthetic schema | Generality | Present | Phase 3 |
| adv_14_strict (debug fields keep a rejected proposal) | Decided | Kept on purpose | — |
| D-V2 scorer asymmetry; fixture and instrument items in `open_items.md` | Measurement | Documented | Fix only if the README cites the affected number |

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
- The old frontend's invariants apply until Phase 2 replaces them.

## 11. Risks

| Risk | Response |
|---|---|
| Scope creep: this is a second project | The in/out list in §7 is the contract. New ideas go to "After v3" |
| Accuracy on schemas we did not prepare is unknown | The setup test is the measure, built in Phase 3, not at the end |
| Hosting multiple workspaces widens the attack surface | Sample data only in public; RLS with a coverage test; credentials only in the MCP server |
| Paid model cost and abuse on a public demo | Spending caps, rate limits, a guest workspace with small quotas |
| The UI still looks home-made | A component system and one reference product to match (Linear, Vercel, Metabase or Hex) |
