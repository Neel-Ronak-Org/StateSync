/**
 * Acceptance: the browser's chart check (A7 validator 2 of 2) refuses every
 * frozen malicious spec on its own (docs/V3_PLAN.md Phase 2; E2-V3/V6, the
 * frontend half). The specs come from fixtures/malicious_specs.json, the same
 * file scripts/verify_e2.py feeds the orchestrator's validator 1. The test data
 * is shared; the validators are not.
 *
 * Each spec arrives as if the server's validator had let it through. It must be
 * withheld before Vega sees it: no canvas, the answer text intact, the specific
 * violation named in developer mode, and no request to any host but the app
 * and the (mocked) API.
 */

import { readFileSync } from 'node:fs'

import { expect, test, type Page } from '@playwright/test'

import { ask, openApp, openDashboards } from './helpers'
import type { MockApi } from './mock-api'

interface MaliciousSpec {
  id: string
  violation: string
  spec: Record<string, unknown> & { data: { values: Record<string, unknown>[] } }
  synthetic_data_size?: number
}

/** ms_09 stores one row plus a size; expanded exactly as verify_e2.py's load_malicious() does. */
function loadMalicious(): MaliciousSpec[] {
  const raw = readFileSync(new URL('../../../fixtures/malicious_specs.json', import.meta.url), 'utf8')
  const specs = (JSON.parse(raw) as { specs: MaliciousSpec[] }).specs
  for (const entry of specs) {
    if (entry.synthetic_data_size) {
      const template = entry.spec.data.values[0]
      entry.spec.data.values = Array.from({ length: entry.synthetic_data_size }, () => ({ ...template }))
    }
  }
  return specs
}

/** Where each violation must be reported: the ground, not just "something failed". */
const GROUND: Record<string, string> = {
  ms_01: 'chart/mark',
  ms_02: 'chart/data/url',
  ms_03: 'chart/encoding/y/expr',
  ms_04: 'chart/encoding/tooltip/signal',
  ms_05: 'chart/transform/0/calculate',
  ms_06: 'chart/malicious_key',
  ms_07: 'chart/encoding/x/condition',
  ms_08: 'chart/mark',
  ms_09: 'chart/data/values',
  ms_10: 'chart/$schema',
}

const SPECS = loadMalicious()
const CHART_QUESTION = 'Show orders by status as a chart'
const CHART_ANSWER = 'Delivered orders dominate: 96,478 of 98,210.'

/** Every request to a host other than the app's and the mocked API's. */
function watchExternalRequests(page: Page): string[] {
  const external: string[] = []
  page.on('request', (request) => {
    const host = new URL(request.url()).hostname
    if (host !== 'localhost' && host !== '127.0.0.1') external.push(request.url())
  })
  return external
}

test('the fixture holds the 10 frozen specs', () => {
  expect(SPECS.map((s) => s.id).sort()).toEqual(Object.keys(GROUND).sort())
})

for (const entry of SPECS) {
  test(`${entry.id} (${entry.violation}) is withheld before render`, async ({ page }) => {
    const external = watchExternalRequests(page)
    const api = await openApp(page, { hash: '#/ask' })
    await page.getByRole('switch', { name: 'Developer mode' }).click()
    api.nextChart = entry.spec
    await ask(page, CHART_QUESTION)

    const turn = page.getByTestId('turn').last()
    await expect(turn.getByTestId('answer-text')).toHaveText(CHART_ANSWER)
    const note = turn.getByRole('note')
    await expect(note).toContainText('Chart withheld')
    await expect(note).toContainText(GROUND[entry.id])
    await expect(turn.locator('canvas')).toHaveCount(0)
    expect(external).toEqual([])
  })
}

async function pinChartTile(page: Page): Promise<MockApi> {
  const api = await openApp(page, { hash: '#/ask' })
  await ask(page, CHART_QUESTION)
  await expect(page.getByTestId('answer-text')).toHaveCount(1)
  await page.getByTestId('turn').last().getByRole('button', { name: 'Pin to dashboard' }).click()
  await expect(page.getByText('Pinned to dashboard')).toBeVisible()
  return api
}

function remoteDataSpec(): MaliciousSpec['spec'] {
  const entry = SPECS.find((s) => s.id === 'ms_02')
  if (!entry) throw new Error('ms_02 missing from the fixture')
  return entry.spec
}

// Since Phase 2b a tile draws a spec the browser builds from its ROWS
// (src/viz/spec.ts, itself A7-checked); a stored server spec lends only its mark
// as a hint. So a hostile stored spec is never handed to Vega at all. These two
// pin that: the tile still draws, from its own spec, and the hostile spec's
// remote data URL (ms_02, evil.example.com) is never fetched.
test('a hostile chart stored on a tile never reaches Vega: the tile draws its own spec', async ({ page }) => {
  const external = watchExternalRequests(page)
  const api = await pinChartTile(page)
  api.setTileChart('tile-1', remoteDataSpec())
  await openDashboards(page)
  await page.reload() // a fresh load: nothing cached from before the chart changed

  await expect(page.getByRole('tablist', { name: 'Dashboards' }).getByRole('tab', { name: 'Overview' })).toBeVisible()
  const tile = page.getByTestId('tile').first()
  await expect(tile.locator('canvas')).toBeVisible()
  await expect(tile.getByText('Chart withheld')).toHaveCount(0)
  expect(external).toEqual([])
})

test('a drawn tile whose stored chart turns hostile keeps drawing from its rows and fetches nothing', async ({ page }) => {
  const external = watchExternalRequests(page)
  const api = await pinChartTile(page)
  await openDashboards(page)
  await expect(page.getByRole('tablist', { name: 'Dashboards' }).getByRole('tab', { name: 'Overview' })).toBeVisible()
  const tile = page.getByTestId('tile').first()
  await expect(tile.locator('canvas')).toBeVisible()

  api.setTileChart('tile-1', remoteDataSpec())
  await tile.getByRole('button', { name: 'Tile actions' }).click()
  await page.getByRole('menuitem', { name: 'Refresh now' }).click()

  await expect(tile.locator('canvas')).toHaveCount(1)
  await expect(tile.getByText('Chart withheld')).toHaveCount(0)
  expect(external).toEqual([])
})

test('a chart that passes the check but cannot be drawn says so, without blaming the check', async ({ page }) => {
  const api = await openApp(page, { hash: '#/ask' })
  await page.getByRole('switch', { name: 'Developer mode' }).click()
  // A7 allows expression-free transforms; this one makes Vega-Lite's compiler throw.
  api.nextChart = {
    $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
    mark: 'bar',
    data: { values: [{ order_status: 'delivered', n: 96478 }] },
    encoding: {
      x: { field: 'order_status', type: 'nominal' },
      y: { field: 'n', type: 'quantitative' },
    },
    transform: [{ window: 5 }],
  }
  await ask(page, CHART_QUESTION)

  const turn = page.getByTestId('turn').last()
  await expect(turn.getByTestId('answer-text')).toHaveText(CHART_ANSWER)
  const note = turn.getByRole('note')
  await expect(note).toContainText('The chart could not be drawn')
  await expect(note).not.toContainText('Chart withheld')
  await expect(turn.locator('canvas')).toHaveCount(0)
})

test('charts never install the vega-tooltip handler, even when a spec asks for tooltips', async ({ page }) => {
  // vega-tooltip writes its table with innerHTML. The theme's mark.tooltip:false
  // only stops automatic tooltips; an explicit tooltip channel passes A7 (it
  // checks each channel's properties, not channel names), so the renderer
  // itself must never install the handler. Its constructor always adds this style.
  const api = await openApp(page, { hash: '#/ask' })
  api.nextChart = {
    $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
    mark: 'bar',
    data: { values: [{ order_status: '<img src=x onerror=alert(1)>', n: 96478 }] },
    encoding: {
      x: { field: 'order_status', type: 'nominal' },
      y: { field: 'n', type: 'quantitative' },
      tooltip: { field: 'order_status', type: 'nominal' },
    },
  }
  await ask(page, CHART_QUESTION)
  const canvas = page.getByTestId('turn').last().getByTestId('vega-chart').locator('canvas')
  await expect(canvas).toBeVisible()

  const box = await canvas.boundingBox()
  if (!box) throw new Error('chart canvas has no box')
  await page.mouse.move(box.x + box.width / 2, box.y + box.height - 40) // over the one bar
  await expect(page.locator('#vega-tooltip-style')).toHaveCount(0)
  await expect(page.locator('#vg-tooltip-element')).toHaveCount(0)
})
