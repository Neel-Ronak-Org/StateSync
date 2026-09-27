# StateSync web

The browser app for StateSync (docs/V3_PLAN.md Phase 2): chat with your data and
documents, and a dashboard of pinned answers that re-run their saved SQL.

- React 19 + TypeScript + Vite, Tailwind v4 with shadcn/ui (Radix), lucide icons.
- Charts: Vega-Lite 5 via `src/charts/VegaChart.tsx` only — every spec is checked
  against the A7 allowlist first, then rendered with vega-interpreter, so the page runs
  under a CSP with no `unsafe-eval`.
- Design system and screen anatomy: [DESIGN.md](DESIGN.md).

```bash
npm install
npm run dev            # http://localhost:5173, talks to the orchestrator on :8090
npm run build && npm run lint
npm run test:e2e       # mocked-API browser suite (builds and previews the app)
STATESYNC_LIVE=1 npm run test:e2e   # live suite against the running stack on :3000
```

Two browser suites:

- **Mocked** (`e2e/mocked`, the UI acceptance bar): the built app with the API
  stubbed by `page.route`, so every state is reachable and runs are deterministic.
  `chart-safety.spec.ts` feeds the 10 frozen `fixtures/malicious_specs.json` specs
  through the real render path, as if the server's validator had missed them.
- **Live** (`e2e/live`): the real stack behind nginx, so the production CSP is in
  force. It checks that the browser refuses `eval` and `new Function` on the served
  page, that a real chart draws with no CSP violation, and the ask → pin → dashboard
  → unpin journey. It needs the running stack (see the root README) and signs in with
  `secrets/api_token_dev`. It asks three real questions. Those conversations stay
  in history; the one tile it pins is removed again.

The orchestrator URL is runtime config: `public/config.js` in development, written
by the container's entrypoint in production.
