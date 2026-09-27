import { expect, type Page } from '@playwright/test'

import { MockApi, TOKEN } from './mock-api'

/** Mock the API, and optionally start signed in (token pre-seeded before the app loads). */
export async function openApp(page: Page, options: { signedIn?: boolean; hash?: string } = {}): Promise<MockApi> {
  const { signedIn = true, hash = '' } = options
  const api = new MockApi()
  await api.install(page)
  if (signedIn) {
    // addInitScript runs on EVERY load, reloads included. Seeding only once per
    // tab (marker in sessionStorage) keeps "sign out, reload, still signed out"
    // testable without the app needing any test-only behaviour.
    await page.addInitScript((token) => {
      try {
        if (!window.sessionStorage.getItem('e2e.seeded')) {
          window.localStorage.setItem('statesync.token', token)
          window.sessionStorage.setItem('e2e.seeded', '1')
        }
      } catch {
        /* storage blocked: the app must still load */
      }
    }, TOKEN)
  }
  await page.goto(`/${hash}`)
  return api
}

export async function ask(page: Page, question: string): Promise<void> {
  const box = page.getByLabel('Ask a question')
  await box.fill(question)
  await box.press('Enter')
}

export async function openDashboards(page: Page): Promise<void> {
  await page.getByRole('button', { name: 'Dashboards', exact: true }).click()
  const tablist = page.getByRole('tablist', { name: 'Dashboards' })
  const emptyHeading = page.getByRole('heading', { name: /No dashboards for|This dashboard is empty/ })
  await expect(tablist.or(emptyHeading)).toBeVisible()
}
