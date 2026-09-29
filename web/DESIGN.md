# StateSync web — design spec

The UI contract for docs/V3_PLAN.md Phase 2b ("dashboard first"). The owner chose
the direction from a reference analytics dashboard: dark navy, cyan and purple
accents, KPI cards with trends, smooth motion. Every screen follows this file; when
a choice is not covered here, pick the quieter option.

## 1. Direction

**A live analytics product: the dashboard is the home, chat is how you ask for more.**
It should feel alive (numbers move, cards respond to the pointer, pages glide) and
stay trustworthy (every number shows when it was computed and from which dataset).
Reference points: the owner's reference mockup for palette and card anatomy;
Linear/Vercel for restraint in type and spacing.

Never: glassmorphism or backdrop blur, emoji, all-caps labels, bouncing or looping
attention-seekers, gradients on text or buttons, centred body text, exclamation
marks in copy. Gradients are allowed only as chart fills (area under a line), in
the sparkline, and as the faint glow behind the sign-in card.

## 2. Tokens (src/index.css — use them, never raw colours)

Dark is the default theme; light is kept and equally finished.

| Token | Dark | Light | Use |
|---|---|---|---|
| `background` | `#0b1220` navy | `#f5f7fb` | page |
| `card` | `#121b2d` | `#ffffff` | cards, panels, rail |
| `muted` | `#1a2438` | `#eef2f8` | inputs, table headers, chips |
| `border` | `#223049` | `#e2e8f0` | 1px separators |
| `foreground` | `#e6edf7` | `#0f172a` | text |
| `muted-foreground` | `#8b97ad` | `#64748b` | captions, axes |
| `primary` | `#5cc8e8` cyan | `#0891b2` | primary buttons, active nav, focus, live dot |
| `accent-2` | `#8b7cf6` purple | `#7c3aed` | second series, secondary highlights |
| `success` | `#34d399` | `#059669` | positive deltas |
| `destructive` | `#fb7185` | `#e11d48` | negative deltas, errors |
| amber (`text-amber-400`/`600`) | | | "needs review" only |

- Chart palette (charts/theme.ts reads `--chart-*`): cyan, purple, teal, amber, pink,
  lime, sky, orange. Area fills are a vertical gradient of the series colour, 35% → 0%.
- **Type:** Geist and Geist Mono. `text-xs` captions · `text-sm` body and controls ·
  `text-base` section titles · `text-lg` page titles · `text-3xl` KPI values, weight 600,
  `tabular-nums`. Numbers everywhere use `tabular-nums`.
- **Spacing:** 4px grid. Controls `h-8`/`h-9`; page gutters `px-6`; card padding `p-4`.
- **Radius:** `rounded-md` controls, `rounded-xl` cards, `rounded-full` dots and chips.
- **Elevation:** 1px borders separate surfaces. A hovered card lifts 2px and gains a
  soft `primary`-tinted shadow; menus and dialogs use shadcn's shadows. No blur.
- **Icons:** lucide-react, `size-4`. Icon-only buttons have an `aria-label` and a
  Tooltip with the same text.

## 3. Motion (the `motion` library, plus CSS transitions)

- Page switch (Dashboards ↔ Ask): fade + 8px rise, 200ms, `ease-out`.
- Cards enter with fade + 6px rise, staggered 30ms, on first appearance only.
- Hover on cards and list rows: 150ms transitions of transform, border and shadow.
- Numbers: KPI values count from their previous value to the new one over 600ms.
- A tile whose value changed on refresh flashes a `primary` ring for 1.2s.
- Tab indicator and nav marker slide between items (shared layout animation).
- Layout changes in edit mode animate (react-grid-layout's CSS transforms).
- `prefers-reduced-motion: reduce` turns all of it into instant changes
  (`MotionConfig reducedMotion="user"` plus a CSS media query).

## 4. App shell

```
┌────┬──────────────────────────────────────────────────────────────┐
│ ▣  │ <page header: tabs / title · dataset picker · actions>   h-14 │
│    ├──────────────────────────────────────────────────────────────┤
│ ▦  │                                                              │
│ ✦  │                     page content                              │
│    │                                                              │
│    │                                                              │
│ ●  │                                                              │
│ </>│                                                              │
│ ◐  │                                                              │
│ ⎋  │                                                              │
└────┴──────────────────────────────────────────────────────────────┘
```

- **Rail** `w-16`, `bg-card border-r`, full height. Top: the mark (links to Dashboards).
  Nav icon buttons with tooltips and `aria-current="page"` when active: **"Dashboards"**
  (`LayoutDashboard`), **"Workspaces"** (`Boxes`), **"Library"** (`Library`) and **"Ask"** (`MessageSquare`). The active item has a `primary`
  pill behind it that slides between items.
- Rail bottom, top to bottom: the connection status dot (`data-testid="connection-status"`,
  tooltip + sr-only text: "Connected" / "Model unavailable: <detail>" / "Server
  unreachable"; polls `/health` every 30 s); **"Developer mode"** (`Code2`, a
  `role="switch"` button with `aria-checked`); **"Theme"** (menu with radio items
  Light / Dark / System); **"Sign out"** (`LogOut`).
- Below `md` the rail stays (it is already narrow).
- Routing: hash — `#/dashboards` (default), `#/workspaces`, `#/workspaces/<id>`,
  `#/library` and `#/ask`. Old links `#/dashboard` and `#/chat` map to them. No router library.
- **Dataset picker**: a Select labelled **"Dataset"** in each page header. Options are the
  API's `available_schemas`, then a **"Workspaces"** group with my workspaces by name
  (values `ds:<schema>` / `ws:<id>`). A new browser starts on `live` when it exists.
  Choosing a workspace makes it active (remembered): its dataset is used everywhere and
  every question carries its `workspace_id`. Choosing a dataset, or opening a past
  conversation on another dataset, leaves the workspace. Switching starts a new chat.

## 5. Sign-in

Centred card `max-w-sm` on the navy background with a faint cyan glow behind it: the
mark, **"Sign in to StateSync"**, muted help ("Paste the API token your administrator
gave you."), input **"API token"** (`type="password"`), full-width **"Sign in"**.
Errors as before: "That token was not accepted. Check it and try again." and, after a
401, "Your session expired or the token is invalid. Sign in again."

## 6. Dashboards (home)

**Header:** dashboard tabs (`role="tablist"` labelled **"Dashboards"**, one `role="tab"`
per dashboard, named by its name; the active tab has a sliding `primary` underline),
**"New dashboard"** (`Plus` icon button), **"Dashboard actions"** menu (**"Rename
dashboard"**, **"Delete dashboard"**). Right side: the Dataset picker, the live indicator
(`data-testid="live-indicator"`: pulsing dot + "Live" + "Updated 12 s ago"),
**"Edit layout"** (toggle button, `aria-pressed`), **"Refresh all"** (icon button),
**"Build with AI"** (primary, `Wand2`).

**Insights strip** (`<section aria-label="Insights">`): up to 4 one-line insights
(`data-testid="insight"`) computed from the tiles' own rows (src/viz/insights.ts), e.g.
"Orders, last hour: 312 · ▲ 4.2% vs the hour before", "Mumbai leads Orders by city with
18% of the total". Hidden when there are none.

**Grid:** react-grid-layout, 12 columns, row height 32px, margin 16px, layout from the
server (`tile.layout`); tiles without one are placed at the bottom by kind (KPI 3×4,
trend and charts 6×9, breakdown and donut 4×9, table 6×9). In **edit mode** the grid
shows dotted guides, every tile shows a grip in its header and a resize handle, and
changes are saved (debounced 600ms) with `PUT /dashboards/{id}/layout`. Outside edit
mode tiles do not move.

**Tile** (`data-testid="tile"`, `data-tile-id`, `data-viz` = the kind being shown):
- Header: title (`text-sm font-medium`, truncated, full text in `title`), status badge
  when not ok ("Needs review", "Refresh failed"), **"Tile actions"** menu:
  **"Show as"** (radio submenu: Automatic, KPI, KPI with trend, Line, Area, Bar,
  Horizontal bar, Donut, Breakdown, Heatmap, Scatter, Table — only the kinds the rows
  can be drawn as), **"Size"** (Small, Medium, Large, Wide), **"Refresh now"**,
  **"Rename"**, **"Refresh interval"**, **"Move to"** (other dashboards of the dataset),
  **"View SQL"** (developer mode), **"Unpin"**.
- Body by kind (src/viz):
  - KPI: the value (`data-testid="tile-kpi"`, counts up), the humanised column name as
    caption; with two numbers (current, previous) a delta chip (`data-testid="tile-delta"`,
    "▲ 4.2%" in success / "▼ 3.1%" in destructive, `aria-label` "up 4.2% vs <label>").
  - KPI with trend: value = the last complete point, delta vs the point before, and a
    sparkline (`data-testid="sparkline"`) of the whole series.
  - Line / Area / Bar / Horizontal bar / Donut / Heatmap / Scatter: `<VegaChart>` with a
    spec built in the browser from the rows (src/viz/spec.ts), A7-checked like every chart.
    Bars are sorted by value unless the axis is time; axis titles are humanised.
  - Breakdown: up to 6 rows of label, value, share and a `primary` bar
    (`data-testid="breakdown-row"`), the rest summed as "Other".
  - Table: the compact table.
- Footer: "Updated 12 s ago · every 30 s" (absolute time in `title`), "Refreshing…"
  while in flight. Interval labels: 30 → "30 s", 60 → "1 min", 900 → "15 min",
  3600 → "1 h", 21600 → "6 h", 86400 → "1 d".
- `needs_review` and `error` banners, and the Rename, Refresh interval, Unpin and
  View SQL dialogs, keep the Phase 2 names: **"Rename tile"** / **"Tile title"** /
  **"Save"**; **"Refresh interval"** with radios **"Every 30 seconds"** (30), **"Every
  minute"** (60), **"Every 15 minutes"**, **"Every hour"**, **"Every 6 hours"**, **"Every
  day"**; **"Unpin this tile?"** / **"Unpin"**; **"Tile SQL"**; **"Review and accept"**;
  **"Retry"**.

**Refresh:** the page polls `GET /tiles?dashboard_id=` every 15 s while visible; the
server re-runs tiles that are due. A changed primary value animates and flashes the
tile (§3).

**Empty states:** no dashboard for the dataset — "No dashboards for **live** yet", one
muted line, **"Build with AI"** (primary) and **"Start empty"**. An empty dashboard —
"This dashboard is empty" with **"Build with AI"** and **"Ask a question"**.

**Build with AI:** a Dialog **"Build a dashboard with AI"** with inputs **"Focus
(optional)"** (placeholder "e.g. delivery performance") and **"Dashboard name"**, and
**"Cancel"** / **"Build"**. Building runs on the server; the dashboard shows a progress
panel (`data-testid="build-progress"`, `role="status"`): "Building 3 of 8", one row per
planned insight with its state (queued, running, added, skipped with the reason), and
cards appear as they are added. It ends with a toast: "Added 7 of 8 insights."

## 6a. Workspaces (docs/V3_PLAN.md Phase 3)

- List (`#/workspaces`): heading **"Workspaces"**, **"New workspace"**; cards
  (`data-testid="workspace-card"`) with name, dataset, setup state in words (New /
  Scanned / Drafts to review / Approved, not tested / Tested) and the score "S/T".
  Empty: **"No workspaces yet"** and one sentence on what a workspace is.
- **New workspace** dialog: **Name**, **Dataset**, **About your business** (optional);
  **Cancel** / **Create**; server refusals shown in the dialog. Opens the new workspace.
- Detail (`#/workspaces/<id>`): name, **"Ask in this workspace"**, **"Workspace
  actions"** → **"Delete workspace"** (confirm). Tabs (`aria-label="Setup steps"`):
  - **Business**: **About your business**, "Version N", **Save** (each save is a version).
  - **Knowledge**: **Draft with AI** (progress `data-testid="draft-progress"`, failures
    shown); one row per table and column (`data-testid="catalog-row"`, `data-table`,
    `data-column`, `data-approved`) with type, sample values, the editable description
    ("Description of <table>.<column>") and its source badge (**AI draft** / **Yours**);
    **Save changes** sends only changed rows. Definitions (`data-testid="definition-row"`)
    with **Term** / **Definition** / **Add definition** and "Delete definition <term>".
    **Approve** (blocked while edits are unsaved) then moves to Test. Only approved
    knowledge is used when answering; any edit needs approving again.
  - **Test**: "Graded against reference answers" (olist, public) or "You grade each
    answer"; **Run the test** (needs approval); ten results (`data-testid="setup-result"`,
    `data-verdict`) with answer and reason; owner grading via "Mark question N right /
    wrong"; score `data-testid="setup-score"` "S/T" with **Ready to use** (≥ 8) or
    **Needs work**; a run that stopped early says why (`role="alert"`).
  - **Documents**: one checkbox per library folder (`data-testid="workspace-folder"`, the
    folder name and "N ready"); **Save** (disabled until changed). "Questions in this
    workspace can search the documents in the folders you choose." **Open library** →
    `#/library`; an empty library says "Your library is empty." with the same link.
- Ask: an answer that ran an approved example shows a **Verified** badge
  (`data-testid="verified-badge"`, tooltip "Answered with a query approved for this
  workspace.").

## 6b. Library (docs/V3_PLAN.md Phase 3c slice 5, D-v3-17)

- `#/library`: heading **"Library"**, "Documents you upload once and use in any workspace."
- Folders (`data-testid="folder-item"`, `aria-current` on the selected one) with
  "N documents"; **New folder** (dialog **Name**, **Cancel** / **Create**, server refusals in
  the dialog, e.g. a duplicate name). **Folder actions** → **Rename folder** and **Delete
  folder** (confirm; says how many documents go with it and "Used by N workspaces").
  No folders: **"No folders yet"**, one sentence, **New folder**.
- Documents of the selected folder (`data-testid="library-document"`, `data-status`):
  title, filename, "N pages" (PDFs only), size, relative upload time (absolute in
  `title`), status **Processing** (spinner) / **Ready** / **Failed** with the reason.
  "Delete document <title>" (confirm). Empty folder: **"No documents in this folder"**.
- **Upload** and a drop zone (`data-testid="upload-dropzone"`, "Drop PDF, text or Markdown
  files here"). The type comes from the extension (.pdf, .txt, .md, .markdown); other
  files, and files over 20 MB, are refused in the browser and never sent. Files upload
  one after another; each refusal names its file and does not stop the rest. While any
  document is Processing the list refreshes every 3 s.

## 7. Ask (chat)

- Two panes. Left (`w-72`, `border-r`, collapsible below `lg`): **"New chat"** and
  `<nav aria-label="Conversations">`, one row per conversation (accessible name = its
  first question; time and dataset under it). Hover or focus shows **"Delete
  conversation"** (`Trash2`), which asks in a Dialog **"Delete this conversation?"**
  ("Its questions and answers are removed. Tiles pinned from it keep working.") with
  **"Cancel"** / **"Delete"**.
- Right: the chat column (`max-w-3xl`), unchanged in anatomy from Phase 2: composer
  **"Ask a question"** / **"Send"**; turns (`data-testid="turn"`), answer text
  (`data-testid="answer-text"`), artifact card, sources (`data-testid="citations"`),
  meta row with **"Details"**, **"Download CSV"**, **"More actions"**, **"Pin to
  dashboard"**; header **"Copy conversation"**.
- Answer text: paragraphs split on blank lines; lines starting "- ", "* " or "1. " are
  rendered as a list — React elements, never HTML.
- Sources: document chunks show the document title when the API gives one (else the
  chunk id), with "p. N".
- Result caption: "1 row" / "N rows".
- Details panel sections: Summary, SQL (once), Sources (a SQL source reads "SQL query —
  shown above"), and in developer mode the chart spec and the Trace.
- Empty state suggestions per dataset, now including live: "What was revenue in the
  last hour?", "Show orders per hour over the last 24 hours", "Which channel converts
  best this week?", "What share of orders is delivered on time?"

## 8. States and copy

- Every async surface has loading (skeletons with a slow shimmer), empty, error
  (message + retry) and success states.
- Copy: sentence case, short, specific, no emoji, no exclamation marks.
- Numbers: `Intl.NumberFormat`, compact above 100,000 in KPIs ("1.2M"); times relative
  with the absolute time in `title`.

## 9. Security rules for UI code (lint-enforced where possible)

- No `dangerouslySetInnerHTML`, `innerHTML`, `insertAdjacentHTML`, `eval`,
  `new Function` — ESLint fails the build on them.
- Charts only through `src/charts/VegaChart.tsx` (A7 check, then the interpreter; never
  tooltips). Specs built in the browser (src/viz/spec.ts) go through the same check.
- Sparklines and breakdown bars are SVG/DOM built by React from numbers only.
- All requests through `src/api/client.ts`; the token only via `src/lib/auth.ts`.
