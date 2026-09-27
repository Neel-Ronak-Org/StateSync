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

import { ANSWER_TIMEOUT, API, askLive, devToken, openPage, signIn, watchCsp } from './helpers'

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

    await openPage(page, 'Dashboards')
    const tile = page.locator(`[data-tile-id="${tileId}"]`)
    await expect(tile).toContainText(COUNT_QUESTION)
    await expect(tile.getByTestId('tile-kpi')).toHaveText(/^\d[\d,.]*[KMB]?$/) // compact above 100,000 (DESIGN.md §8)

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

// The Phase 2b exit check: a live dashboard's numbers visibly move. The test
// pins its own tile (orders in a rolling 10-minute window, refreshed every 15 s)
// to its own dashboard, and removes both, and its conversation, afterwards.
test('a live dashboard number changes within a minute', async ({ page, request }) => {
  test.setTimeout(300_000)
  const auth = { authorization: `Bearer ${devToken()}` }
  const sessionId = `live-e2e-${Date.now()}`
  let dashboardId: string | null = null
  try {
    const asked = await request.post(`${API}/query`, {
      headers: auth,
      data: { query: 'How many orders were placed in the last 10 minutes?', session_id: sessionId, schema: 'live' },
      timeout: ANSWER_TIMEOUT,
    })
    expect(asked.ok(), await asked.text()).toBe(true)
    const turnIndex = (await asked.json()).state_trace.turn_index as number
    const created = await request.post(`${API}/dashboards`, { headers: auth, data: { name: `Live check ${Date.now()}`, target_schema: 'live' } })
    dashboardId = (await created.json()).dashboard.dashboard_id as string
    const pinned = await request.post(`${API}/tiles`, {
      headers: auth,
      data: { session_id: sessionId, turn_index: turnIndex, dashboard_id: dashboardId, refresh_interval_s: 15, viz: 'kpi' },
    })
    expect(pinned.ok(), await pinned.text()).toBe(true)

    await page.addInitScript(([id]) => {
      localStorage.setItem('statesync.corpus', 'live')
      localStorage.setItem('statesync.dashboard.live', id)
    }, [dashboardId])
    await signIn(page)
    const kpi = page.getByTestId('tile-kpi')
    await expect(kpi).toBeVisible()
    const first = await kpi.innerText()
    await expect.poll(() => kpi.innerText(), { timeout: 60_000, intervals: [5_000] }).not.toBe(first)
  } finally {
    if (dashboardId) await request.delete(`${API}/dashboards/${dashboardId}`, { headers: auth })
    await request.delete(`${API}/sessions/${sessionId}`, { headers: auth })
  }
})

