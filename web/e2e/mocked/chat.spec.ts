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
    await openApp(page, { signedIn: false, hash: '#/ask' })
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
    const api = await openApp(page, { hash: '#/ask' })
    api.failNext('/query', 401, 'Missing or invalid bearer token.')
    await ask(page, 'How many orders are there in total?')
    await expect(page.getByRole('heading', { name: 'Sign in to StateSync' })).toBeVisible()
    await expect(page.getByRole('alert')).toContainText('Sign in again')
  })
})

test.describe('asking', () => {
  test('an answer shows its text and its result table', async ({ page }) => {
    const api = await openApp(page, { hash: '#/ask' })
    await ask(page, 'How many orders are there in total?')
    const turn = page.getByTestId('turn').last()
    await expect(turn.getByTestId('answer-text')).toHaveText('There are 550 orders in total.')
    await expect(turn.getByRole('table')).toContainText('total_orders')
    await expect(turn.getByRole('table')).toContainText('550')
    expect(api.queries[0]).toMatchObject({ query: 'How many orders are there in total?', schema: 'public' })
    expect(typeof api.queries[0].session_id).toBe('string')
  })

  test('Enter sends, Shift+Enter makes a new line', async ({ page }) => {
    const api = await openApp(page, { hash: '#/ask' })
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
    const api = await openApp(page, { hash: '#/ask' })
    await ask(page, 'How many orders are there in total?')
    await expect(page.getByTestId('answer-text')).toHaveCount(1)
    await ask(page, 'Show orders by status as a chart')
    await expect(page.getByTestId('answer-text')).toHaveCount(2)
    expect(api.queries[1].session_id).toBe(api.queries[0].session_id)
  })

  test('a chart answer draws a chart', async ({ page }) => {
    await openApp(page, { hash: '#/ask' })
    await ask(page, 'Show orders by status as a chart')
    const turn = page.getByTestId('turn').last()
    const host = turn.getByTestId('vega-chart')
    const canvas = host.locator('canvas')
    await expect(canvas).toBeVisible()
    // The width goes on the spec: vega-embed's width OPTION is ignored for
    // Vega-Lite, which drew a ~98px chart. The chart must span the answer.
    // (Measured against the turn: vega-embed's host is inline-block and
    // shrinks with the chart, so it cannot be the reference.)
    const [chartBox, turnBox] = await Promise.all([canvas.boundingBox(), turn.boundingBox()])
    expect(chartBox?.width ?? 0).toBeGreaterThan((turnBox?.width ?? Infinity) * 0.6)
  })

  test('short style: the browser draws the chart from the rows, and a single row gets none', async ({ page }) => {
    const api = await openApp(page, { hash: '#/ask' })
    api.nextSynthStyle = 'short'
    await ask(page, 'Show orders by status as a chart')
    const multi = page.getByTestId('turn').last()
    await expect(multi.getByTestId('answer-text')).toHaveText('Delivered orders dominate: 96,478 of 98,210.')
    await expect(multi.getByTestId('vega-chart').locator('canvas')).toBeVisible()
    await expect(multi.getByRole('table')).toContainText('delivered')

    await ask(page, 'How many orders are there in total?')
    const single = page.getByTestId('turn').last()
    await expect(single.getByTestId('answer-text')).toHaveText('There are 550 orders in total.')
    await expect(single.getByRole('table')).toContainText('550')
    await expect(single.getByTestId('vega-chart')).toHaveCount(0)
  })

  test('a chart that fails the safety check is withheld, and the answer still shows', async ({ page }) => {
    await openApp(page, { hash: '#/ask' })
    await ask(page, 'Show the unsafe chart')
    const turn = page.getByTestId('turn').last()
    await expect(turn.getByTestId('answer-text')).toHaveText('Here are orders by status.')
    await expect(turn.getByText('Chart withheld')).toBeVisible()
    await expect(turn.locator('canvas')).toHaveCount(0)
  })

  test('caveats show under the answer, and nothing shows when there are none', async ({ page }) => {
    const api = await openApp(page, { hash: '#/ask' })
    api.nextCaveats = [
      'These figures may count some rows more than once (join fan-out).',
      'Showing the first 1,000 rows; the full result is larger.',
    ]
    await ask(page, 'How many orders are there in total?')
    const withCaveats = page.getByTestId('turn').last()
    await expect(withCaveats.getByTestId('answer-caveat')).toHaveCount(2)
    await expect(withCaveats.getByTestId('answer-caveat').first()).toContainText('join fan-out')

    await ask(page, 'How many orders are there in total?')
    await expect(page.getByTestId('turn').last().getByTestId('answer-text')).toBeVisible()
    await expect(page.getByTestId('turn').last().getByTestId('answer-caveat')).toHaveCount(0)
  })

  test('"How I got this" is collapsed under a SQL answer and opens to the explanation; documents have none', async ({ page }) => {
    await openApp(page, { hash: '#/ask' })
    await ask(page, 'How many orders are there in total?')
    const sqlTurn = page.getByTestId('turn').last()
    const toggle = sqlTurn.getByRole('button', { name: 'How I got this' })
    await expect(toggle).toHaveAttribute('aria-expanded', 'false')
    await expect(sqlTurn.getByTestId('how-i-got-this')).toHaveCount(0)
    await toggle.click()
    await expect(toggle).toHaveAttribute('aria-expanded', 'true')
    await expect(sqlTurn.getByTestId('how-i-got-this')).toContainText('Counted rows')

    await ask(page, 'What does the SLA policy say for LP003?')
    const docsTurn = page.getByTestId('turn').last()
    await expect(docsTurn.getByTestId('answer-text')).toBeVisible()
    await expect(docsTurn.getByRole('button', { name: 'How I got this' })).toHaveCount(0)
  })

  test('a document answer lists its sources and has no table', async ({ page }) => {
    await openApp(page, { hash: '#/ask' })
    await ask(page, 'What does the SLA policy say for LP003?')
    const turn = page.getByTestId('turn').last()
    await expect(turn.getByTestId('citations')).toContainText('SLA annexure LP003')
    await expect(turn.getByRole('table')).toHaveCount(0)
  })

  test('an answer from an uploaded document cites it by title and page, not by id', async ({ page }) => {
    await openApp(page, { hash: '#/ask' })
    await ask(page, 'How fast are refunds issued?')
    const citations = page.getByTestId('turn').last().getByTestId('citations')
    await expect(citations).toContainText('Refund policy 2026 p. 3')
    await expect(citations).not.toContainText('up_0123456789abcdef')
  })

  test('the details panel shows the SQL behind an answer', async ({ page }) => {
    await openApp(page, { hash: '#/ask' })
    await ask(page, 'How many orders are there in total?')
    await page.getByTestId('turn').last().getByRole('button', { name: 'Details' }).click()
    const panel = page.getByRole('dialog', { name: 'Answer details' })
    await expect(panel).toContainText('SELECT COUNT(*) AS total_orders FROM orders')
  })

  test('the result downloads as CSV', async ({ page }) => {
    await openApp(page, { hash: '#/ask' })
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
    const api = await openApp(page, { hash: '#/ask' })
    api.failNext('/query', 429, 'Rate limit exceeded.', { 'Retry-After': '17' })
    await ask(page, 'How many orders are there in total?')
    await expect(page.getByText('Try again in 17 seconds')).toBeVisible()
  })

  test('a rate limit without Retry-After does not invent a wait time', async ({ page }) => {
    const api = await openApp(page, { hash: '#/ask' })
    api.failNext('/query', 429, 'Rate limit exceeded.')
    await ask(page, 'How many orders are there in total?')
    await expect(page.getByText('Rate limit reached. Try again shortly.')).toBeVisible()
  })

  test('the conversation survives a reload', async ({ page }) => {
    await openApp(page, { hash: '#/ask' })
    await ask(page, 'How many orders are there in total?')
    await expect(page.getByTestId('answer-text')).toHaveText('There are 550 orders in total.')
    await page.reload()
    await expect(page.getByTestId('answer-text')).toHaveText('There are 550 orders in total.')
  })
})

test.describe('conversations and datasets', () => {
  test('switching dataset starts a new conversation on that dataset', async ({ page }) => {
    const api = await openApp(page, { hash: '#/ask' })
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
    const api = await openApp(page, { hash: '#/ask' })
    await ask(page, 'How many orders are there in total?')
    await expect(page.getByTestId('turn')).toHaveCount(1)
    await page.getByRole('button', { name: 'New chat' }).click()
    await expect(page.getByTestId('turn')).toHaveCount(0)
    await ask(page, 'How many orders are there in total?')
    await expect(page.getByTestId('turn')).toHaveCount(1)
    expect(api.queries[1].session_id).not.toBe(api.queries[0].session_id)
  })

  test('a past conversation opens from the sidebar and follow-ups continue it', async ({ page }) => {
    const api = await openApp(page, { hash: '#/ask' })
    await page
      .getByRole('navigation', { name: 'Conversations' })
      .getByRole('button', { name: 'How many orders are there in total?' })
      .click()
    await expect(page.getByTestId('turn')).toHaveCount(2)
    await expect(page.getByTestId('answer-text').first()).toHaveText('There are 550 orders in total.')
    await ask(page, 'And how many sellers?')
    await expect(page.getByTestId('turn')).toHaveCount(3)
    expect(api.queries[0].session_id).toBe('seed-session-1')
  })

  test('opening a conversation from the other dataset switches to that dataset', async ({ page }) => {
    const api = await openApp(page, { hash: '#/ask' })
    await page
      .getByRole('navigation', { name: 'Conversations' })
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
    const api = await openApp(page, { hash: '#/ask' })
    api.storedRowsLimit = 50
    await page
      .getByRole('navigation', { name: 'Conversations' })
      .getByRole('button', { name: 'How many orders are there in total?' })
      .click()
    const turn = page.getByTestId('turn').nth(1)
    await turn.getByRole('button', { name: 'Load full result' }).click()
    await expect(turn.getByText('Showing 50 of 60 rows (the rest was not stored)')).toBeVisible()
    await expect(turn.getByRole('button', { name: 'Load full result' })).toHaveCount(0)
  })

  test('a truncated stored result can load in full', async ({ page }) => {
    await openApp(page, { hash: '#/ask' })
    await page
      .getByRole('navigation', { name: 'Conversations' })
      .getByRole('button', { name: 'How many orders are there in total?' })
      .click()
    const turn = page.getByTestId('turn').nth(1)
    await expect(turn.getByText('Showing 20 of 60 rows')).toBeVisible()
    await turn.getByRole('button', { name: 'Load full result' }).click()
    // exact: 'Show all 60 rows' (the toggle) also contains the phrase
    await expect(turn.getByText('60 rows', { exact: true })).toBeVisible()
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
    await openApp(page, { hash: '#/ask' })
    await ask(page, 'How many orders are there in total?')
    await page.getByRole('switch', { name: 'Developer mode' }).click()
    await page.getByTestId('turn').last().getByRole('button', { name: 'Details' }).click()
    const panel = page.getByRole('dialog', { name: 'Answer details' })
    await expect(panel.getByText('llm_calls')).toBeVisible()
  })

  test('developer mode lists every model call with a total; off, it is hidden', async ({ page }) => {
    await openApp(page, { hash: '#/ask' })
    await ask(page, 'How many orders are there in total?')
    const details = page.getByTestId('turn').last().getByRole('button', { name: 'Details' })
    const panel = page.getByRole('dialog', { name: 'Answer details' })

    await details.click()
    await expect(panel.getByTestId('model-calls')).toHaveCount(0)
    await page.keyboard.press('Escape')

    await page.getByRole('switch', { name: 'Developer mode' }).click()
    await details.click()
    const table = panel.getByTestId('model-calls')
    await expect(table.getByTestId('model-call')).toHaveCount(5)
    await expect(table.getByTestId('model-calls-total')).toContainText('5,164')
    await expect(table.getByText('0.84 s')).toBeVisible()
  })
})
