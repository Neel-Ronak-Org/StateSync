/**
 * Acceptance: sign-in, the chat, answers and their safety (docs/V3_PLAN.md
 * Phase 2, journey J3). The selectors ARE the UI contract: accessible names
 * first, data-testid only where no accessible name fits.
 */

import { expect, test } from '@playwright/test'

import { ask, openApp } from './helpers'
import { TOKEN } from './mock-api'

test.describe('sign-in', () => {
  test('a valid token signs in and a bad one is refused with a message', async ({ page }) => {
    await openApp(page, { signedIn: false })
    await expect(page.getByRole('heading', { name: 'Sign in to StateSync' })).toBeVisible()

    await page.getByLabel('API token').fill('wrong-token')
    await page.getByRole('button', { name: 'Sign in' }).click()
    await expect(page.getByRole('alert')).toContainText('not accepted')

    await page.getByLabel('API token').fill(TOKEN)
    await page.getByRole('button', { name: 'Sign in' }).click()
    await expect(page.getByLabel('Ask a question')).toBeVisible()
  })

  test('sign out returns to the sign-in screen and forgets the token', async ({ page }) => {
    await openApp(page)
    await page.getByRole('button', { name: 'Sign out' }).click()
    await expect(page.getByRole('heading', { name: 'Sign in to StateSync' })).toBeVisible()
    await page.reload()
    await expect(page.getByRole('heading', { name: 'Sign in to StateSync' })).toBeVisible()
  })

  test('a 401 mid-session returns to sign-in and says why', async ({ page }) => {
    const api = await openApp(page)
    api.failNext('/query', 401, 'Missing or invalid bearer token.')
    await ask(page, 'How many orders are there in total?')
    await expect(page.getByRole('heading', { name: 'Sign in to StateSync' })).toBeVisible()
    await expect(page.getByRole('alert')).toContainText('Sign in again')
  })
})

test.describe('asking', () => {
  test('an answer shows its text and its result table', async ({ page }) => {
    const api = await openApp(page)
    await ask(page, 'How many orders are there in total?')
    const turn = page.getByTestId('turn').last()
    await expect(turn.getByTestId('answer-text')).toHaveText('There are 550 orders in total.')
    await expect(turn.getByRole('table')).toContainText('total_orders')
    await expect(turn.getByRole('table')).toContainText('550')
    expect(api.queries[0]).toMatchObject({ query: 'How many orders are there in total?', schema: 'public' })
    expect(typeof api.queries[0].session_id).toBe('string')
  })

  test('Enter sends, Shift+Enter makes a new line', async ({ page }) => {
    const api = await openApp(page)
    const box = page.getByLabel('Ask a question')
    await box.fill('first line')
    await box.press('Shift+Enter')
    await box.pressSequentially('second line')
    await expect(box).toHaveValue('first line\nsecond line')
    expect(api.queries).toHaveLength(0)
    await box.press('Enter')
    await expect(page.getByTestId('turn')).toHaveCount(1)
    await expect(box).toHaveValue('')
  })

  test('follow-ups stay in the same conversation', async ({ page }) => {
    const api = await openApp(page)
    await ask(page, 'How many orders are there in total?')
    await expect(page.getByTestId('answer-text')).toHaveCount(1)
    await ask(page, 'Show orders by status as a chart')
    await expect(page.getByTestId('answer-text')).toHaveCount(2)
    expect(api.queries[1].session_id).toBe(api.queries[0].session_id)
  })

  test('a chart answer draws a chart', async ({ page }) => {
    await openApp(page)
    await ask(page, 'Show orders by status as a chart')
    const turn = page.getByTestId('turn').last()
    await expect(turn.getByTestId('vega-chart').locator('canvas')).toBeVisible()
  })

  test('a chart that fails the safety check is withheld, and the answer still shows', async ({ page }) => {
    await openApp(page)
    await ask(page, 'Show the unsafe chart')
    const turn = page.getByTestId('turn').last()
    await expect(turn.getByTestId('answer-text')).toHaveText('Here are orders by status.')
    await expect(turn.getByText('Chart withheld')).toBeVisible()
    await expect(turn.locator('canvas')).toHaveCount(0)
  })

  test('a document answer lists its sources and has no table', async ({ page }) => {
    await openApp(page)
    await ask(page, 'What does the SLA policy say for LP003?')
    const turn = page.getByTestId('turn').last()
    await expect(turn.getByTestId('citations')).toContainText('doc_sla_annexure_LP003_c001')
    await expect(turn.getByRole('table')).toHaveCount(0)
  })

  test('the details panel shows the SQL behind an answer', async ({ page }) => {
    await openApp(page)
    await ask(page, 'How many orders are there in total?')
    await page.getByTestId('turn').last().getByRole('button', { name: 'Details' }).click()
    const panel = page.getByRole('dialog', { name: 'Answer details' })
    await expect(panel).toContainText('SELECT COUNT(*) AS total_orders FROM orders')
  })

  test('the result downloads as CSV', async ({ page }) => {
    await openApp(page)
    await ask(page, 'Show orders by status as a chart')
    const download = page.waitForEvent('download')
    await page.getByTestId('turn').last().getByRole('button', { name: 'Download CSV' }).click()
    const file = await download
    expect(file.suggestedFilename()).toMatch(/\.csv$/)
    const path = await file.path()
    const text = path ? (await import('node:fs')).readFileSync(path, 'utf8') : ''
    expect(text.split('\n')[0]).toBe('order_status,n')
    expect(text).toContain('delivered,96478')
  })

  test('a rate limit says when to try again', async ({ page }) => {
    const api = await openApp(page)
    api.failNext('/query', 429, 'Rate limit exceeded.', { 'Retry-After': '17' })
    await ask(page, 'How many orders are there in total?')
    await expect(page.getByText('Try again in 17 seconds')).toBeVisible()
  })

  test('a rate limit without Retry-After does not invent a wait time', async ({ page }) => {
    const api = await openApp(page)
    api.failNext('/query', 429, 'Rate limit exceeded.')
    await ask(page, 'How many orders are there in total?')
    await expect(page.getByText('Rate limit reached. Try again shortly.')).toBeVisible()
  })

  test('the conversation survives a reload', async ({ page }) => {
    await openApp(page)
    await ask(page, 'How many orders are there in total?')
    await expect(page.getByTestId('answer-text')).toHaveText('There are 550 orders in total.')
    await page.reload()
    await expect(page.getByTestId('answer-text')).toHaveText('There are 550 orders in total.')
  })
})

test.describe('conversations and datasets', () => {
  test('switching dataset starts a new conversation on that dataset', async ({ page }) => {
    const api = await openApp(page)
    await ask(page, 'How many orders are there in total?')
    await expect(page.getByTestId('turn')).toHaveCount(1)
    await page.getByLabel('Dataset').click()
    await page.getByRole('option', { name: 'olist' }).click()
    await expect(page.getByTestId('turn')).toHaveCount(0)
    await ask(page, 'How many orders are there in total?')
    await expect(page.getByTestId('answer-text')).toHaveCount(1)
    expect(api.queries[1].schema).toBe('olist')
    expect(api.queries[1].session_id).not.toBe(api.queries[0].session_id)
    await expect(page.getByTestId('turn').last().getByTestId('turn-dataset')).toHaveText('olist')
  })

  test('New chat clears the thread and starts a new session', async ({ page }) => {
    const api = await openApp(page)
    await ask(page, 'How many orders are there in total?')
    await expect(page.getByTestId('turn')).toHaveCount(1)
    await page.getByRole('button', { name: 'New chat' }).click()
    await expect(page.getByTestId('turn')).toHaveCount(0)
    await ask(page, 'How many orders are there in total?')
    await expect(page.getByTestId('turn')).toHaveCount(1)
    expect(api.queries[1].session_id).not.toBe(api.queries[0].session_id)
  })

  test('a past conversation opens from the sidebar and follow-ups continue it', async ({ page }) => {
    const api = await openApp(page)
    await page
      .getByRole('navigation', { name: 'Recent conversations' })
      .getByRole('button', { name: 'How many orders are there in total?' })
      .click()
    await expect(page.getByTestId('turn')).toHaveCount(2)
    await expect(page.getByTestId('answer-text').first()).toHaveText('There are 550 orders in total.')
    await ask(page, 'And how many sellers?')
    await expect(page.getByTestId('turn')).toHaveCount(3)
    expect(api.queries[0].session_id).toBe('seed-session-1')
  })

  test('opening a conversation from the other dataset switches to that dataset', async ({ page }) => {
    const api = await openApp(page)
    await page
      .getByRole('navigation', { name: 'Recent conversations' })
      .getByRole('button', { name: 'How many orders were delivered on olist?' })
      .click()
    await expect(page.getByTestId('turn')).toHaveCount(1)
    await expect(page.getByLabel('Dataset')).toContainText('olist')
    await expect(page.getByTestId('turn-dataset').first()).toHaveText('olist')
    await ask(page, 'And how many were canceled?')
    await expect(page.getByTestId('answer-text')).toHaveCount(2)
    expect(api.queries[0]).toMatchObject({ session_id: 'seed-session-olist', schema: 'olist' })
  })

  test('a result the store kept only part of says so after loading', async ({ page }) => {
    const api = await openApp(page)
    api.storedRowsLimit = 50
    await page
      .getByRole('navigation', { name: 'Recent conversations' })
      .getByRole('button', { name: 'How many orders are there in total?' })
      .click()
    const turn = page.getByTestId('turn').nth(1)
    await turn.getByRole('button', { name: 'Load full result' }).click()
    await expect(turn.getByText('Showing 50 of 60 rows (the rest was not stored)')).toBeVisible()
    await expect(turn.getByRole('button', { name: 'Load full result' })).toHaveCount(0)
  })

  test('a truncated stored result can load in full', async ({ page }) => {
    await openApp(page)
    await page
      .getByRole('navigation', { name: 'Recent conversations' })
      .getByRole('button', { name: 'How many orders are there in total?' })
      .click()
    const turn = page.getByTestId('turn').nth(1)
    await expect(turn.getByText('Showing 20 of 60 rows')).toBeVisible()
    await turn.getByRole('button', { name: 'Load full result' }).click()
    await expect(turn.getByText('60 rows')).toBeVisible()
    await expect(turn.getByRole('button', { name: 'Load full result' })).toHaveCount(0)
  })
})

test.describe('appearance', () => {
  test('dark theme applies and persists', async ({ page }) => {
    await openApp(page)
    await page.getByRole('button', { name: 'Theme' }).click()
    await page.getByRole('menuitemradio', { name: 'Dark' }).click()
    await expect(page.locator('html')).toHaveClass(/dark/)
    await page.reload()
    await expect(page.locator('html')).toHaveClass(/dark/)
  })

  test('developer mode reveals the raw trace in the details panel', async ({ page }) => {
    await openApp(page)
    await ask(page, 'How many orders are there in total?')
    await page.getByRole('switch', { name: 'Developer mode' }).click()
    await page.getByTestId('turn').last().getByRole('button', { name: 'Details' }).click()
    const panel = page.getByRole('dialog', { name: 'Answer details' })
    await expect(panel.getByText('llm_calls')).toBeVisible()
  })
})
