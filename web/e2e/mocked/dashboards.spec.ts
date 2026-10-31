/**
 * Acceptance: the dashboard-first experience (docs/V3_PLAN.md Phase 2b,
 * web/DESIGN.md §4 and §6). Written ahead of trust in the implementation:
 * each test is one thing the owner asked for.
 */

import { expect, test, type Page } from '@playwright/test'

import { ask, openApp, openDashboards } from './helpers'
import type { MockApi } from './mock-api'

async function pin(page: Page, question: string) {
  await ask(page, question)
  await expect(page.getByTestId('answer-text').last()).toBeVisible()
  await page.getByTestId('turn').last().getByRole('button', { name: 'Pin to dashboard' }).click()
  await expect(page.getByText('Pinned to dashboard').first()).toBeVisible()
}

async function withTiles(page: Page, questions: string[]): Promise<MockApi> {
  const api = await openApp(page, { hash: '#/ask' })
  for (const q of questions) await pin(page, q)
  await openDashboards(page)
  await expect(page.getByTestId('tile')).toHaveCount(questions.length)
  return api
}

/** Open a tile's menu, hover a submenu, and pick an item (a radio in "Show as", a plain item in "Size"). */
async function tileMenu(page: Page, tileIndex: number, submenu: string, item: string) {
  await page.getByTestId('tile').nth(tileIndex).getByRole('button', { name: 'Tile actions' }).click()
  await page.getByRole('menuitem', { name: submenu, exact: true }).hover()
  const radio = page.getByRole('menuitemradio', { name: item, exact: true })
  const plain = page.getByRole('menuitem', { name: item, exact: true })
  await expect(radio.or(plain)).toBeVisible()
  await ((await radio.count()) ? radio : plain).click()
}

test.describe('home and navigation', () => {
  test('the app opens on Ask; the sidebar names every page and groups setting up apart', async ({ page }) => {
    await openApp(page)
    await expect(page.getByRole('button', { name: 'Ask', exact: true })).toHaveAttribute('aria-current', 'page')
    await expect(page.getByLabel('Ask a question')).toBeVisible()
    const nav = page.getByRole('navigation', { name: 'Main' })
    await expect(nav).toContainText('Set up')
    for (const label of ['Ask', 'Dashboards', 'Workspaces', 'Data sources']) {
      await expect(nav.getByRole('button', { name: label, exact: true })).toContainText(label)
    }
    await nav.getByRole('button', { name: 'Dashboards', exact: true }).click()
    await expect(page.getByRole('heading', { name: 'No dashboards in Demo yet' })).toBeVisible()
  })

  test('old links still land in the right place', async ({ page }) => {
    await openApp(page, { hash: '#/chat' })
    await expect(page.getByRole('button', { name: 'Ask', exact: true })).toHaveAttribute('aria-current', 'page')
    await expect(page.getByLabel('Ask a question')).toBeVisible()
  })

  test('the connection light says when the server cannot be reached', async ({ page }) => {
    await openApp(page)
    await expect(page.getByTestId('connection-status')).toHaveAttribute('data-status', 'ok')
    await page.route('**/health', (route) => route.abort())
    await page.reload()
    await expect(page.getByTestId('connection-status')).toHaveAttribute('data-status', 'down')
    await expect(page.getByTestId('connection-status')).toContainText('Server unreachable')
  })
})

test.describe('dashboards', () => {
  test('a new dashboard can be created, renamed and deleted', async ({ page }) => {
    const api = await openApp(page, { hash: '#/dashboards' })
    await page.getByRole('button', { name: 'Create a dashboard' }).click()
    await expect(page.getByRole('tab', { name: 'Overview' })).toHaveAttribute('aria-selected', 'true')

    await page.getByRole('button', { name: 'New dashboard' }).click()
    const create = page.getByRole('dialog', { name: 'New dashboard' })
    await create.getByLabel('Dashboard name').fill('Delivery')
    await create.getByRole('button', { name: 'Create' }).click()
    await expect(page.getByRole('tab', { name: 'Delivery' })).toHaveAttribute('aria-selected', 'true')

    await page.getByRole('button', { name: 'Dashboard actions' }).click()
    await page.getByRole('menuitem', { name: 'Rename dashboard' }).click()
    const rename = page.getByRole('dialog', { name: 'Rename dashboard' })
    await rename.getByLabel('Dashboard name').fill('Delivery times')
    await rename.getByRole('button', { name: 'Save' }).click()
    await expect(page.getByRole('tab', { name: 'Delivery times' })).toBeVisible()

    await page.getByRole('button', { name: 'Dashboard actions' }).click()
    await page.getByRole('menuitem', { name: 'Delete dashboard' }).click()
    await page.getByRole('dialog', { name: 'Delete this dashboard?' }).getByRole('button', { name: 'Delete' }).click()
    await expect(page.getByRole('tab', { name: 'Delivery times' })).toHaveCount(0)
    await expect(page.getByRole('tab', { name: 'Overview' })).toBeVisible()
    expect(api.tileCalls.filter((c) => c.startsWith('DELETE /dashboards/'))).toHaveLength(1)
  })

  test('the open dashboard is remembered across a reload', async ({ page }) => {
    await openApp(page, { hash: '#/dashboards' })
    await page.getByRole('button', { name: 'Create a dashboard' }).click()
    await page.getByRole('button', { name: 'New dashboard' }).click()
    const create = page.getByRole('dialog', { name: 'New dashboard' })
    await create.getByLabel('Dashboard name').fill('Second')
    await create.getByRole('button', { name: 'Create' }).click()
    await expect(page.getByRole('tab', { name: 'Second' })).toHaveAttribute('aria-selected', 'true')
    await page.reload()
    await expect(page.getByRole('tab', { name: 'Second' })).toHaveAttribute('aria-selected', 'true')
  })
})

test.describe('tiles', () => {
  test('a chart tile can be shown another way, and back to automatic', async ({ page }) => {
    const api = await withTiles(page, ['Show orders by status as a chart'])
    const tile = page.getByTestId('tile').first()
    await expect(tile).toHaveAttribute('data-viz', 'bar')

    await tileMenu(page, 0, 'Show as', 'Donut')
    await expect(tile).toHaveAttribute('data-viz', 'donut')
    await expect(tile.locator('canvas')).toBeVisible()
    expect(api.tileCalls).toContain('PATCH /tiles/tile-1')

    await tileMenu(page, 0, 'Show as', 'Breakdown')
    await expect(tile).toHaveAttribute('data-viz', 'breakdown')
    await expect(tile.getByTestId('breakdown-row')).toHaveCount(3)

    await tileMenu(page, 0, 'Show as', 'Automatic')
    await expect(tile).toHaveAttribute('data-viz', 'bar')
  })

  test('"Show as" only offers what the rows can be drawn as', async ({ page }) => {
    await withTiles(page, ['How many orders are there in total?'])
    await page.getByTestId('tile').first().getByRole('button', { name: 'Tile actions' }).click()
    await page.getByRole('menuitem', { name: 'Show as', exact: true }).hover()
    await expect(page.getByRole('menuitemradio', { name: 'Table', exact: true })).toBeVisible()
    const options = await page.getByRole('menuitemradio').allInnerTexts()
    expect(options.map((o) => o.trim())).toEqual(['Automatic', 'KPI', 'Table'])
  })

  test('a KPI with a previous value shows its change', async ({ page }) => {
    const api = await withTiles(page, ['How many orders are there in total?'])
    const stored = api.tiles.get('tile-1')
    if (!stored?.result) throw new Error('tile-1 missing')
    stored.result.rows = [{ orders_last_hour: 110, orders_previous_hour: 100 }]
    stored.columns = ['orders_last_hour', 'orders_previous_hour']
    await page.reload()
    const tile = page.getByTestId('tile').first()
    await expect(tile.getByTestId('tile-kpi')).toHaveText('110')
    await expect(tile.getByTestId('tile-delta')).toHaveText('▲ 10%')
    await expect(tile.getByTestId('tile-delta')).toHaveAttribute('aria-label', 'up 10% vs orders previous hour')
  })

  test('a refreshed tile whose numbers changed counts up and flashes', async ({ page }) => {
    const api = await withTiles(page, ['How many orders are there in total?'])
    const tile = page.getByTestId('tile').first()
    await expect(tile.getByTestId('tile-kpi')).toHaveText('550')
    const stored = api.tiles.get('tile-1')
    if (!stored?.result) throw new Error('tile-1 missing')
    stored.result.rows = [{ total_orders: 575 }]
    await page.getByRole('button', { name: 'Refresh all' }).click()
    await expect(tile.getByTestId('tile-kpi')).toHaveText('575')
    await expect(tile.locator('.tile-flash')).toHaveCount(1)
  })

  test('a size preset stores the new slot', async ({ page }) => {
    const api = await withTiles(page, ['Show orders by status as a chart'])
    await tileMenu(page, 0, 'Size', 'Wide')
    await expect.poll(() => api.tileCalls.some((c) => c.startsWith('PUT /dashboards/'))).toBe(true)
    await expect.poll(() => api.tiles.get('tile-1')?.layout?.w).toBe(12)
  })

  test('edit layout shows the grid and the handles, and Done hides them', async ({ page }) => {
    await withTiles(page, ['How many orders are there in total?'])
    const edit = page.getByRole('button', { name: 'Edit layout' })
    await edit.click()
    await expect(page.getByRole('button', { name: 'Done' })).toHaveAttribute('aria-pressed', 'true')
    await expect(page.locator('.grid-guides')).toHaveCount(1)
    await expect(page.locator('.tile-drag-handle')).toHaveCount(1)
    await page.getByRole('button', { name: 'Done' }).click()
    await expect(page.locator('.tile-drag-handle')).toHaveCount(0)
  })

  test('a tile moves to another dashboard of the dataset', async ({ page }) => {
    const api = await withTiles(page, ['How many orders are there in total?'])
    await page.getByRole('button', { name: 'New dashboard' }).click()
    const create = page.getByRole('dialog', { name: 'New dashboard' })
    await create.getByLabel('Dashboard name').fill('Archive')
    await create.getByRole('button', { name: 'Create' }).click()
    await page.getByRole('tab', { name: 'Overview' }).click()
    await expect(page.getByTestId('tile')).toHaveCount(1)
    await page.getByTestId('tile').first().getByRole('button', { name: 'Tile actions' }).click()
    await page.getByRole('menuitem', { name: 'Move to', exact: true }).hover()
    await page.getByRole('menuitem', { name: 'Archive', exact: true }).click()
    await expect(page.getByTestId('tile')).toHaveCount(0)
    await page.getByRole('tab', { name: 'Archive' }).click()
    await expect(page.getByTestId('tile')).toHaveCount(1)
    expect(api.tiles.get('tile-1')?.dashboard_id).not.toBe(undefined)
  })
})

test.describe('ask', () => {
  test('a conversation can be deleted after confirming', async ({ page }) => {
    const api = await openApp(page, { hash: '#/ask' })
    const list = page.getByRole('navigation', { name: 'Conversations' })
    const row = list.getByRole('button', { name: 'How many orders are there in total?' })
    await row.hover()
    await row.locator('..').getByRole('button', { name: 'Delete conversation' }).click()
    const dialog = page.getByRole('dialog', { name: 'Delete this conversation?' })
    await expect(dialog).toContainText('Tiles pinned from it keep working.')
    await dialog.getByRole('button', { name: 'Delete' }).click()
    await expect(row).toHaveCount(0)
    await expect(page.getByText('Conversation deleted')).toBeVisible()
    expect(api.sessions.has('seed-session-1')).toBe(false)
  })

  test('answers render lists as lists', async ({ page }) => {
    const api = await openApp(page, { hash: '#/ask' })
    api.nextAnswer = 'Refunds by category:\n- Fashion: 5 days\n- Electronics: 7 days'
    await ask(page, 'How many orders are there in total?')
    const answer = page.getByTestId('answer-text').last()
    await expect(answer.getByRole('listitem')).toHaveText(['Fashion: 5 days', 'Electronics: 7 days'])
    await expect(page.getByTestId('turn').last()).toContainText('1 row')
  })
})
