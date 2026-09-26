import type { Page } from '@playwright/test'

import { MockApi, TOKEN } from './mock-api'

/** Mock the API, and optionally start signed in (token pre-seeded before the app loads). */
export async function openApp(page: Page, options: { signedIn?: boolean; hash?: string } = {}): Promise<MockApi> {
  const { signedIn = true, hash = '' } = options
  const api = new MockApi()
  await api.install(page)
  if (signedIn) {
    await page.addInitScript((token) => {
      try {
        window.localStorage.setItem('statesync.token', token)
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
