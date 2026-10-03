# StateSync demo guide (end to end, from an empty account)

Audience: the team running a live demo on the owner's machine. About 20 minutes.
Everything below was dry-run on 2026-09-30 (connection, scan, three uploads ready in
2.1 s, 7 of 7 definitions found in the handbook, none invented).

## What you need (all in the repo)

| Item | Where | What it is |
|---|---|---|
| Demo account | `secrets/api_token_demo` (principal `p_demo`, tenant `t_demo`) | An account of its own: every screen starts empty, and nothing it does touches the other accounts. |
| Datasets | built in: **olist** (real Brazilian e-commerce, ~99k orders), **live** (simulated shop, new orders every few seconds), **public** (synthetic ONDC logistics) | Pick one in the Dataset picker, or build a workspace on one. |
| Documents | `demo/documents/` (3 PDFs; regenerate with `python3 demo/make_documents.py`) | Fictional policies written for the olist data: a seller handbook with 7 definitions, a returns and refunds policy, a delivery SLA by region. |
| Your own database | `demo/local_shop_db.sh` | A separate Postgres on this Mac (port 5433) with a furniture shop: 64 products, 600 customers, 4,000 orders, a read-only login for StateSync. |
| Reset | `python3 demo/reset_demo_account.py` | Empties the demo account again (after a rehearsal). |

## Before the review (10 minutes)

1. Stack up and healthy: `docker compose ps` (all `healthy`; the evaluator stays off).
   Never `docker compose down -v`; restart single services by name.
2. Start the local database: `./demo/local_shop_db.sh` → ends with "Ready: host.docker.internal:5433 …".
3. Empty the demo account if you rehearsed: `python3 demo/reset_demo_account.py` → "left: all 0".
4. Copy the demo token to the clipboard: `pbcopy < secrets/api_token_demo`.
5. Open http://localhost:3000, paste the token into **API token**, **Sign in**.
   You should see an empty Dashboards page with **Build with AI** and **Start empty**.
6. Quota: the Groq free tier allows roughly 40–50 answered questions a day for the whole
   team. The script below uses about 15–20 questions' worth. Don't run evaluations today.

## The script

### 1. Library: documents (2 min, no model calls)
- **Library** (rail) → **Documents** → **New folder** "Policies".
- Drag the three PDFs from `demo/documents/` onto the drop zone. Each shows **Processing**,
  then **Ready** within seconds (parsed in an isolated service, split into passages,
  embedded locally with Ollama; nothing leaves the machine).
- Talking point: the parser has no network and no credentials; a hostile PDF can only
  crash its own child process.

### 2. Library: connect your own database (2 min, no model calls)
- **Library** → **Databases** → **Add database**:
  Name `Local shop`, Host `host.docker.internal`, Port `5433`, Database `shop`,
  User `statesync_reader`, Password `demo-reader-pass`, SSL `disable` → **Connect**.
  It shows **Ready** with schema `sales`.
- Optional security moment: add another with User `postgres`, Password `demo-owner-pass`
  → refused: "This login is a superuser … Use a login that can only read." StateSync only
  saves logins it has proved are read-only; the password is encrypted and never shown again.
- Show "How to create a read-only login" (the SQL a customer would run).

### 3. Workspace: set it up (4 min, a few model calls)
- **Workspaces** → **New workspace**: Name `Furniture shop`, Dataset → **Your databases** →
  `Local shop`, About your business: "We sell furniture online, in our app and in stores
  across Indian cities." → **Create**.
- **Knowledge** → **Draft with AI**: AI drafts a description for each table and column
  (marked **AI draft**). Edit one to show that the owner's words win (**Yours**).
- **Documents** tab → tick **Policies** → **Save**.
- **Knowledge** → **From your documents** → **Find in documents**: 7 definitions appear
  (On time, Late order, Active seller, GMV, Net revenue, Repeat customer, Cancellation
  rate), each **From document** with the exact sentence and "Marketplace seller handbook
  2026, p. 1". Talking point: a suggestion is kept only if its quoted sentence really is
  in the document; invented ones are dropped and counted.
- **Approve**. (Skip the **Test** tab today: its 10 questions take a while on the free tier.)

### 4. Conversations (5 min, ~5 questions)
- **Ask in this workspace**, then try:
  - "Which city had the most delivered orders in the last 30 days?"
  - "What is our cancellation rate by channel?" (uses the approved definition)
  - "How long do customers have to request a return?" (answered from the returns policy,
    cited with its page)
- On each SQL answer show: the evidence chip, **How I got this** (plain-English reading of
  the SQL, no model), and the figures in the text: hover a number to see which row and
  column it came from; "All N figures in this answer match the result."
- Thumbs up on one answer, ask the exact question again → it comes back **Verified**
  (the approved query ran as is).

### 5. Dashboard with live refresh (5 min, ~8 questions' worth)
- Switch the Dataset picker to **live** → **Dashboards** → **Build with AI** (goal e.g.
  "orders and revenue today"). It plans 4–8 insights, asks each through the same checked
  pipeline, and pins them as tiles.
- Point at a **Live** tile: tiles store their SQL, not the question, and re-run it every
  30 s with **no model call**; the numbers move as the simulator adds orders.
- Pin an answer from step 4 to a dashboard too ("Pin").

### 6. Close (1 min)
- The security split: the AI side holds no database password; one service runs every
  query through a three-layer read-only gate; row-level security on every table.
- The research angle: `docs/research/paper/main.pdf` (work in progress).

## Known issue (say it if asked)

Document search is not yet scoped by dataset: on the original account, a question also
searches the built-in sample documents (ONDC policies), so a returns question can mix our
returns policy with the sample one. The demo account has no sample documents (they belong
to the original tenant), so its answers cite only what you uploaded. Dry-run: "How long do
customers have to request a return?" was answered "7 days", cited to the returns policy, p. 1.

## If something goes wrong

| Symptom | Fix |
|---|---|
| "The model is rate-limited" / slow answers | Groq per-minute limit: wait ~60 s. Daily limit: switch to showing already-built screens. |
| Add database: "host could not be found" / refused for `localhost` | Use `host.docker.internal`, not `localhost` (localhost inside the container is StateSync itself). Check `./demo/local_shop_db.sh` ran. |
| A document stays **Processing** | `docker compose ps parser mcp ollama`; restart one by name: `docker compose restart parser`. |
| Signed out unexpectedly | Paste the token again (`pbcopy < secrets/api_token_demo`). |
| Need a clean slate mid-demo | `python3 demo/reset_demo_account.py` (demo account only). |
