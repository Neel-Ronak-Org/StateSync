# StateSync web — design spec

The UI for docs/V3_PLAN.md Phase 2. The owner delegated the visual direction; this
file is that direction, written down so every screen follows one system. When a
choice is not covered here, pick the quieter option.

## 1. Direction

**Calm, dense, trustworthy — a tool for reading numbers, not a marketing page.**
Reference points: Linear's restraint, Vercel's dashboard typography. The product's
claim is "numbers you can trust", so the UI makes provenance (which dataset, which
query, which source) visible without shouting.

Never: gradients, glassmorphism/blur, emoji, drop shadows on cards, all-caps labels,
more than one accent colour, animated attention-seekers, centred body text, "AI
sparkle" iconography, exclamation marks in copy.

## 2. Tokens (already in src/index.css — use them, never raw colours)

- **Colour:** neutral shadcn palette (`bg-background`, `bg-card`, `bg-muted`,
  `text-foreground`, `text-muted-foreground`, `border-border`). ONE accent:
  `text-brand` / `bg-brand` — only for the active nav item marker, links, focus, and the
  small "live" dot. Destructive red only for errors. Amber (`text-amber-600
  dark:text-amber-400`, `bg-amber-500/10`) only for "needs review".
- **Type:** Geist (sans) and Geist Mono (`font-mono`) for SQL, ids, numbers in tables.
  Scale: `text-xs` (12) captions/meta · `text-sm` (14) body and controls — the
  default · `text-base` (16) section titles · `text-lg` (18) page titles ·
  `text-3xl` KPI values. Weights: 400 body, 500 labels/nav, 600 titles/KPIs. Numbers in
  tables and KPIs use `tabular-nums`.
- **Spacing:** 4px grid. Controls `h-8`/`h-9`; page gutters `px-6`; gaps between turns
  `gap-8`; inside cards `p-4`.
- **Radius:** `rounded-md` controls, `rounded-lg` cards and panels, `rounded-full`
  only for avatars/dots.
- **Borders over shadows:** 1px `border` separates surfaces. Shadows only on floating
  layers (menus, dialogs — shadcn defaults).
- **Icons:** lucide-react, `size-4` (16px), stroke default. Icon-only buttons always
  have `aria-label` AND a Tooltip with the same text.

## 3. App shell

```
┌──────────────┬───────────────────────────────────────────────┐
│ ▣ StateSync  │  <View title>                 <view actions>  │  h-14, border-b
│ [+ New chat] ├───────────────────────────────────────────────┤
│ ◉ Chat       │                                               │
│ ○ Dashboard  │               view content                    │
│              │                                               │
│ Recent       │                                               │
│  · question… │                                               │
│  · question… │                                               │
│ ──────────── │                                               │
│ Dataset [▾]  │                                               │
│ Developer ◯  │                                               │
│ ◐ Theme  ⎋   │                                               │
└──────────────┴───────────────────────────────────────────────┘
```
- Sidebar `w-64`, `bg-sidebar`, `border-r`, full height. Below `md` it becomes a Sheet
  opened by a menu button (aria-label "Open menu") in the top bar.
- Brand row: the favicon mark (inline SVG component, 20px) + "StateSync" `text-sm
  font-semibold`.
- "New chat": full-width outline button with `Plus` icon.
- Nav: "Chat" (`MessageSquare`) and "Dashboard" (`LayoutDashboard`), `h-8` rows; active
  row `bg-sidebar-accent font-medium` with a 2px `bg-brand` bar on its left edge, and
  `aria-current="page"`.
- "Recent": caption `text-xs font-medium text-muted-foreground`, then a scrollable list
  inside `<nav aria-label="Recent conversations">`. Each item is a button whose
  accessible name is the conversation's first question (truncate with ellipsis; the
  full text in `title`), with the relative time under it in `text-xs muted`. The open
  conversation is highlighted like the active nav row.
- Footer (border-t, `p-3`, `gap-2`): "Dataset" Select (label "Dataset", options = the
  API's available_schemas); "Developer mode" Switch with its label; a row with the theme
  menu button (aria-label "Theme"; `DropdownMenuRadioGroup` with items Light / Dark /
  System) and a "Sign out" button (`LogOut` icon + text, ghost).
- Routing: hash-based — `#/chat` (default) and `#/dashboard`. No router library.

## 4. Sign-in

Centred card `w-full max-w-sm`: the mark, heading **"Sign in to StateSync"** (`text-lg
font-semibold`), one line of muted help ("Paste the API token your administrator gave
you."), an input labelled **"API token"** (`type="password"`, `autocomplete="off"`),
and a full-width **"Sign in"** button. Submitting validates the token by calling
`api.sessions()` with it stored: success enters the app; a 401 shows an inline
`role="alert"`: "That token was not accepted. Check it and try again." If the app
returned here because a request got a 401 (AUTH_EXPIRED_EVENT), show the alert "Your
session expired or the token is invalid. Sign in again." on arrival.

## 5. Chat

- Column `mx-auto w-full max-w-3xl px-6`; the thread scrolls; the composer is docked at
  the bottom (`border-t bg-background`, same column width).
- **Empty state** (no turns): heading "Ask about your data" (`text-lg font-semibold`),
  one muted line naming the dataset ("Questions are answered from the **olist**
  dataset."), then 4 suggestion buttons in a 2×2 grid (outline, `text-left`, `h-auto
  py-2`), from a per-dataset list:
  - public: "How many orders are there in total?", "Which sellers breached their
    contracted SLA last quarter?", "Show orders by city as a bar chart", "What does the
    returns policy say about refunds?"
  - olist: "How many orders were delivered?", "Show orders by status as a chart", "What
    are the top 5 states by number of customers?", "What is the average review score?"
- **Composer:** Textarea (aria-label **"Ask a question"**, placeholder "Ask about your
  data…", autosize 1–8 rows, `resize-none`), Send icon button (aria-label **"Send"**,
  `ArrowUp`) inside its right edge. Enter sends; Shift+Enter inserts a newline; `/`
  anywhere outside an input focuses it. While a question is in flight the button shows a
  spinner and is disabled.
- **Turn** (`data-testid="turn"`, `gap-3`):
  - Question: right-aligned, `bg-muted rounded-lg px-3 py-2 text-sm max-w-[85%]`.
  - While waiting: a `role="status"` line "Thinking…" with two muted skeleton bars.
  - Answer text (`data-testid="answer-text"`): `text-sm leading-6`, plain text with
    paragraphs split on blank lines. NEVER rendered as HTML or Markdown-to-HTML.
  - Artifact card (only when there is a chart or rows): `rounded-lg border bg-card`:
    chart first (`<VegaChart>`, height 260), then the results table.
  - Results table: shadcn Table, sticky header, numeric columns right-aligned with
    `font-mono tabular-nums`, first 10 rows then a "Show all N rows" toggle. A 1×1 result
    also shows the value large (`text-3xl font-semibold tabular-nums`) above the table.
    Caption row in `text-xs muted`: "N rows" — or, for a replayed turn whose preview is
    shorter than its row count, "Showing P of N rows" plus a **"Load full result"**
    button that fetches `api.turnRows` and replaces the preview (then shows "N rows").
  - Sources (`data-testid="citations"`), when citations exist: caption "Sources", then
    small outline badges: SQL citations as "SQL query" (opens Details); chunk citations
    as the document/chunk id in `font-mono`, with "p. N" when a page is given.
  - Meta + actions row (`text-xs muted`, `justify-between`): left — dataset badge
    (`data-testid="turn-dataset"`, outline, text = the dataset the turn used), then
    route ("Database", "Documents", "Database + documents") and latency ("1.2 s");
    right — icon buttons with tooltips: **"Details"** (`PanelRight`), **"Download
    CSV"** (`Download`, only when rows), and a **"More actions"** menu: Download JSON,
    Download PNG (only when a chart rendered), Copy SQL, Copy answer.
  - Failed question: an inline destructive Alert inside the turn with the ApiError
    message (e.g. "Rate limit reached. Try again in 17 seconds.") and a "Retry" button.
- **Header actions** (top bar, right): "Copy conversation" (copies the thread as
  Markdown: `## question`, answer, SQL in a fenced block).
- **Details panel:** Sheet from the right, `sm:max-w-lg`, title **"Answer details"**.
  Sections: Summary (dataset, route, turn number, latency, LLM calls, tokens in/out),
  SQL (`pre` in `font-mono text-xs bg-muted rounded-md p-3 overflow-auto` + Copy
  button; "No SQL was run for this answer." when none), Sources. In developer mode add
  "Trace": every `state_trace` key as a row, key in `font-mono`, value JSON-stringified
  and truncated to 300 characters — text nodes only.
- **Persistence:** the current thread and session id live in sessionStorage (reload
  keeps them); the chosen dataset, theme and developer mode in localStorage. All via
  `src/lib/storage.ts`.

## 6. Dashboard (Phase 2, second slice)

- Top bar: title "Dashboard", muted "N tiles", right: **"Refresh all"** (outline,
  `RefreshCw`).
- Grid: react-grid-layout, 12 columns, row height 40px, margin 16px; default tile size
  4×6 (KPI-shaped results 3×4). Layout persisted per browser in localStorage
  (`KEYS.layout`). Drag by the tile header only.
- Tile (`data-testid="tile"`, `rounded-lg border bg-card flex flex-col`):
  - Header `h-10 px-4 border-b`: title (`text-sm font-medium truncate`), a status badge
    when not ok, and a **"Tile actions"** menu (`MoreHorizontal`): Refresh now, Rename,
    Refresh interval, View SQL (developer mode), Unpin.
  - Body: a 1×1 result as a KPI (`text-3xl font-semibold tabular-nums` + the column
    name as a muted caption); otherwise the chart (`<VegaChart>` filling the body);
    otherwise a compact table (first 20 rows).
  - Footer `px-4 py-2 text-xs muted`: "Updated 5 min ago · every 1 h" (absolute time in
    `title`); "Refreshing…" while a refresh is in flight.
  - `needs_review`: an amber banner at the top of the body: "The data this tile reads
    has changed. The numbers shown are from before the change." + **"Review and
    accept"** (calls confirm).
  - `error`: a destructive line "Last refresh failed: <last_error>" + "Retry".
- Empty state: "No tiles yet" + "Pin an answer from the chat to keep it here." + a
  "Go to chat" button.
- Pinning (chat): SQL-backed turns get a **"Pin to dashboard"** icon button (`Pin`) in
  the actions row; success toasts "Pinned to dashboard" with a "View" action; any
  refusal (404/409/422) toasts the server's message. Document-only turns show no pin
  button.
- Exact names the tests use (e2e/mocked/dashboard.spec.ts):
  - KPI value element: `data-testid="tile-kpi"` (text = the formatted value, e.g. "550").
  - Footer interval label: 60 → "1 min", 900 → "15 min", 3600 → "1 h", 21600 → "6 h",
    86400 → "1 d" (else minutes/hours), rendered as "every 1 h".
  - "Rename": a Dialog titled **"Rename tile"** with an Input labelled **"Tile title"**
    (prefilled) and a **"Save"** button (Enter submits).
  - "Refresh interval": a Dialog titled **"Refresh interval"** with a RadioGroup (role
    radio) of **"Every 15 minutes"** (900), **"Every hour"** (3600), **"Every 6 hours"**
    (21600), **"Every day"** (86400), preselecting the tile's current value, and
    **"Save"**.
  - "Unpin": a confirmation Dialog titled **"Unpin this tile?"** ("It stops refreshing
    and is removed from the dashboard. The conversation it came from is kept.") with
    **"Cancel"** and a destructive **"Unpin"**.
  - "View SQL" (developer mode only): a Dialog titled **"Tile SQL"** showing the SQL in
    `font-mono`, with Copy.
  - Menu items are `DropdownMenuItem`s (role menuitem) with these exact names.
- Data: `useQuery(['tiles'], api.tiles)`; mutations (pin, refresh, update, confirm,
  delete) update the cached list from the returned tile (setQueryData) instead of
  refetching everything. Opening the dashboard lists tiles (the server refreshes any
  that are due); "Refresh all" calls refresh for each tile, at most 3 at a time.

## 7. States and copy

- Every async surface has four states designed: loading (skeleton, not spinners, for
  content; spinner only inside buttons), empty, error (message + retry), success.
- Copy: sentence case, short, specific ("Rate limit reached. Try again in 17
  seconds."), no emoji, no exclamation marks, never blame the user.
- Numbers: `Intl.NumberFormat` (grouping); times: relative ("5 min ago") with the
  absolute time in `title`.

## 8. Security rules for UI code (lint-enforced where possible)

- No `dangerouslySetInnerHTML`, `innerHTML`, `insertAdjacentHTML`, `eval`,
  `new Function` — ESLint fails the build on them.
- Charts only through `src/charts/VegaChart.tsx` (validates, then renders without
  eval). Never call vega/vega-embed anywhere else. No tooltips.
- All requests through `src/api/client.ts`. The token only via `src/lib/auth.ts`.
- External links (none expected) would need `rel="noopener noreferrer"`.
