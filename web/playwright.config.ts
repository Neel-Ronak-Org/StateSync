import { defineConfig, devices } from '@playwright/test'

/**
 * Two suites:
 * - e2e/mocked: the built app (vite preview) with the orchestrator API mocked
 *   by page.route. Deterministic; runs anywhere; this is the UI acceptance bar.
 * - e2e/live (STATESYNC_LIVE=1): the real stack behind nginx at :3000, so the
 *   production CSP is in force and answers come from the real pipeline.
 */
const live = Boolean(process.env.STATESYNC_LIVE)

export default defineConfig({
  testDir: './e2e',
  testMatch: live ? 'live/**/*.spec.ts' : 'mocked/**/*.spec.ts',
  fullyParallel: !live,
  workers: live ? 1 : undefined,
  timeout: live ? 180_000 : 30_000,
  expect: { timeout: live ? 60_000 : 5_000 },
  reporter: [['list']],
  use: {
    baseURL: live ? process.env.STATESYNC_BASE_URL ?? 'http://localhost:3000' : 'http://localhost:4173',
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: live
    ? undefined
    : {
        command: 'npm run build && npm run preview',
        url: 'http://localhost:4173',
        reuseExistingServer: !process.env.CI,
        timeout: 180_000,
      },
})
