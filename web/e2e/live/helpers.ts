/**
 * Helpers for the live suite: the real stack behind nginx (STATESYNC_LIVE=1),
 * so the production CSP is in force and answers come from the real pipeline.
 */

import { readFileSync } from 'node:fs'

import { expect, type Locator, type Page } from '@playwright/test'

/** Where the browser reaches the orchestrator (the container's ORCHESTRATOR_URL). */
export const API = process.env.STATESYNC_API_URL ?? 'http://localhost:8090'

/** One real answer: Groq-backed, and the adapter may wait out a rate limit first. */
export const ANSWER_TIMEOUT = 170_000

/** The local dev principal's token (secrets/api_token_dev). Read here, never logged. */
export function devToken(): string {
  return readFileSync(new URL('../../../secrets/api_token_dev', import.meta.url), 'utf8').trim()
}

/**
 * Records every CSP violation the page reports, from before any app script
 * runs, plus the console's own CSP errors. Call before the first navigation.
 */
export async function watchCsp(page: Page): Promise<() => Promise<string[]>> {
  await page.addInitScript(() => {
    const w = window as unknown as { __cspViolations: string[] }
    w.__cspViolations = []
    document.addEventListener('securitypolicyviolation', (e) => {
      w.__cspViolations.push(`${e.violatedDirective} blocked ${e.blockedURI || '(inline)'}`)
    })
  })
  const fromConsole: string[] = []
  page.on('console', (msg) => {
    if (/Content Security Policy|unsafe-eval/i.test(msg.text())) fromConsole.push(msg.text())
  })
  return async () => [
    ...(await page.evaluate(() => (window as unknown as { __cspViolations?: string[] }).__cspViolations ?? [])),
    ...fromConsole,
  ]
}

/** Sign in through the real sign-in card. */
export async function signIn(page: Page): Promise<void> {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Sign in to StateSync' })).toBeVisible()
  await page.getByLabel('API token').fill(devToken())
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByLabel('Ask a question')).toBeVisible()
}

/**
 * Ask through the composer and wait for the real pipeline. Returns the answered
 * turn; if the app shows an error instead, fails with the app's own message.
 */
export async function askLive(page: Page, question: string): Promise<Locator> {
  const turns = page.getByTestId('turn')
  const before = await turns.count()
  const box = page.getByLabel('Ask a question')
  await box.fill(question)
  await box.press('Enter')

  const turn = turns.nth(before)
  const answer = turn.getByTestId('answer-text')
  const failure = turn.getByRole('alert')
  await expect(answer.or(failure)).toBeVisible({ timeout: ANSWER_TIMEOUT })
  if (await failure.isVisible()) {
    throw new Error(`the app answered "${question}" with an error: ${await failure.innerText()}`)
  }
  return turn
}
