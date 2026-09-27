/**
 * Live: the main journeys through the real pipeline (docs/V3_PLAN.md Phase 2,
 * J3 and J4). Every question is a real Groq-backed answer, so the suite asks
 * only three, one test at a time (workers: 1 in playwright.config.ts).
 *
 * Side effects: the conversations stay in the dev principal's history, like any
 * other use of the app. The one tile this suite creates is removed again, by
 * the UI when the test passes and through the API if it fails midway; the
 * user's own tiles are never touched.
 */

import { expect, test } from '@playwright/test'

import { API, askLive, devToken, signIn, watchCsp } from './helpers'

const COUNT_QUESTION = 'How many orders are there in total?'
const CHART_QUESTION = 'Show orders by city as a bar chart'

test('a database answer shows its text and its result table', async ({ page }) => {
  await signIn(page)
  const turn = await askLive(page, COUNT_QUESTION)
  await expect(turn.getByTestId('answer-text')).not.toBeEmpty()
  await expect(turn.getByRole('table')).toBeVisible()
  await expect(turn.getByRole('button', { name: 'Pin to dashboard' })).toBeVisible()
})

test('a chart answer is drawn under the served CSP, with no violation', async ({ page }) => {
  const violations = await watchCsp(page)
  await signIn(page)
  const turn = await askLive(page, CHART_QUESTION)

  const chart = turn.getByTestId('vega-chart')
  expect(await chart.count(), `the pipeline returned no chart for "${CHART_QUESTION}"`).toBe(1)
  // Both validators must agree: a spec the server sent must pass the browser's check too.
  await expect(turn.getByText('Chart withheld')).toHaveCount(0)
  await expect(chart.locator('canvas')).toBeVisible()
  expect(await violations()).toEqual([])
})

test('an answer pins to the dashboard with its value, and unpins', async ({ page, request }) => {
  const auth = { authorization: `Bearer ${devToken()}` }
  let tileId: string | null = null
  try {
    await signIn(page)
    const turn = await askLive(page, COUNT_QUESTION)

    const pinResponse = page.waitForResponse(
      (r) => r.url() === `${API}/tiles` && r.request().method() === 'POST',
    )
    await turn.getByRole('button', { name: 'Pin to dashboard' }).click()
    const pinned = await pinResponse
    expect(pinned.ok(), `pin refused: ${await pinned.text()}`).toBe(true)
    tileId = ((await pinned.json()) as { tile: { tile_id: string } }).tile.tile_id
    await expect(page.getByText('Pinned to dashboard')).toBeVisible()

    await page.getByRole('button', { name: 'Dashboard', exact: true }).click()
    const tile = page.locator(`[data-tile-id="${tileId}"]`)
    await expect(tile).toContainText(COUNT_QUESTION)
    await expect(tile.getByTestId('tile-kpi')).toHaveText(/^\d[\d,]*$/)

    await tile.getByRole('button', { name: 'Tile actions' }).click()
    await page.getByRole('menuitem', { name: 'Unpin' }).click()
    await page.getByRole('dialog', { name: 'Unpin this tile?' }).getByRole('button', { name: 'Unpin' }).click()
    await expect(tile).toHaveCount(0)

    const after = await request.get(`${API}/tiles/${tileId}`, { headers: auth })
    expect(after.status(), 'the unpinned tile is gone on the server too').toBe(404)
    tileId = null
  } finally {
    if (tileId) await request.delete(`${API}/tiles/${tileId}`, { headers: auth })
  }
})
