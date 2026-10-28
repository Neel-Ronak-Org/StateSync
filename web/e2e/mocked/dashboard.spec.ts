/**
 * Acceptance: pinning answers and the dashboard (docs/V3_PLAN.md Phase 2,
 * journey J4; web/DESIGN.md §6). A tile re-runs its saved SQL; the UI must
 * make its freshness and its state (ok / needs review / failed) visible.
 */

import { expect, test, type Page } from '@playwright/test'

import { ask, openApp, openDashboards } from './helpers'
import type { MockApi } from './mock-api'

async function pinLast(page: Page) {
  await page.getByTestId('turn').last().getByRole('button', { name: 'Pin to dashboard' }).click()
  await expect(page.getByText('Pinned to dashboard')).toBeVisible()
}

async function openDashboard(page: Page) {
  await openDashboards(page)
  await expect(page.getByRole('tablist', { name: 'Dashboards' })).toBeVisible()
}

async function tileActions(page: Page, item: string) {
  await page.getByTestId('tile').first().getByRole('button', { name: 'Tile actions' }).click()
  await page.getByRole('menuitem', { name: item }).click()
}

async function withOnePinnedTile(page: Page): Promise<MockApi> {
  const api = await openApp(page, { hash: '#/ask' })
  await ask(page, 'How many orders are there in total?')
  await expect(page.getByTestId('answer-text')).toHaveCount(1)
  await pinLast(page)
  await openDashboard(page)
  await expect(page.getByTestId('tile')).toHaveCount(1)
  return api
}

test.describe('pinning', () => {
  test('a database answer pins, and the tile shows its value', async ({ page }) => {
    const api = await withOnePinnedTile(page)
    const tile = page.getByTestId('tile').first()
    await expect(tile).toContainText('How many orders are there in total?')
    await expect(tile.getByTestId('tile-kpi')).toHaveText('550')
    await expect(tile).toContainText('every 1 h')
    expect(api.tileCalls).toContain('POST /tiles')
  })

  test('a chart answer pins as a chart tile', async ({ page }) => {
    await openApp(page, { hash: '#/ask' })
    await ask(page, 'Show orders by status as a chart')
    await expect(page.getByTestId('answer-text')).toHaveCount(1)
    await pinLast(page)
    await openDashboard(page)
    await expect(page.getByTestId('tile').first().getByTestId('vega-chart').locator('canvas')).toBeVisible()
  })

  test('a document-only answer offers no pin', async ({ page }) => {
    await openApp(page, { hash: '#/ask' })
    await ask(page, 'What does the SLA policy say for LP003?')
    await expect(page.getByTestId('answer-text')).toHaveCount(1)
    await expect(page.getByTestId('turn').last().getByRole('button', { name: 'Pin to dashboard' })).toHaveCount(0)
  })

  test("a refused pin shows the server's reason", async ({ page }) => {
    const api = await openApp(page, { hash: '#/ask' })
    await ask(page, 'How many orders are there in total?')
    await expect(page.getByTestId('answer-text')).toHaveCount(1)
    api.failNext('/tiles', 409, 'You already have 30 tiles; the limit is 30.')
    await page.getByTestId('turn').last().getByRole('button', { name: 'Pin to dashboard' }).click()
    await expect(page.getByText('You already have 30 tiles; the limit is 30.')).toBeVisible()
  })
})

test.describe('dashboard', () => {
  test('an empty dashboard says so and leads back to the chat', async ({ page }) => {
    await openApp(page, { hash: '#/dashboards' })
    await expect(page.getByRole('heading', { name: 'No dashboards for public yet' })).toBeVisible()
    await page.getByRole('button', { name: 'Create a dashboard' }).click()
    await expect(page.getByRole('heading', { name: 'This dashboard is empty' })).toBeVisible()
    await page.getByRole('button', { name: 'Ask a question' }).click()
    await expect(page.getByLabel('Ask a question')).toBeVisible()
  })

  test('Refresh now re-runs the tile', async ({ page }) => {
    const api = await withOnePinnedTile(page)
    await tileActions(page, 'Refresh now')
    await expect.poll(() => api.tileCalls.filter((c) => c.endsWith('/refresh')).length).toBe(1)
  })

  test('Refresh all re-runs every tile', async ({ page }) => {
    const api = await withOnePinnedTile(page)
    await page.getByRole('button', { name: 'Refresh all' }).click()
    await expect.poll(() => api.tileCalls.filter((c) => c.endsWith('/refresh')).length).toBe(1)
  })

  test('a tile can be renamed', async ({ page }) => {
    await withOnePinnedTile(page)
    await tileActions(page, 'Rename')
    const dialog = page.getByRole('dialog', { name: 'Rename tile' })
    await dialog.getByLabel('Tile title').fill('Orders, all time')
    await dialog.getByRole('button', { name: 'Save' }).click()
    await expect(page.getByTestId('tile').first()).toContainText('Orders, all time')
  })

  test('the refresh interval can be changed', async ({ page }) => {
    await withOnePinnedTile(page)
    await tileActions(page, 'Refresh interval')
    const dialog = page.getByRole('dialog', { name: 'Refresh interval' })
    await dialog.getByRole('radio', { name: 'Every 15 minutes' }).click()
    await dialog.getByRole('button', { name: 'Save' }).click()
    await expect(page.getByTestId('tile').first()).toContainText('every 15 min')
  })

  test('unpinning asks first, then removes the tile', async ({ page }) => {
    await withOnePinnedTile(page)
    await tileActions(page, 'Unpin')
    await page.getByRole('dialog', { name: 'Unpin this tile?' }).getByRole('button', { name: 'Unpin' }).click()
    await expect(page.getByTestId('tile')).toHaveCount(0)
    await expect(page.getByRole('heading', { name: 'This dashboard is empty' })).toBeVisible()
  })

  test('a tile whose data changed asks for review, and accepting clears it', async ({ page }) => {
    const api = await withOnePinnedTile(page)
    api.setTileState('tile-1', 'needs_review')
    await page.reload()
    const tile = page.getByTestId('tile').first()
    await expect(tile.getByText('The data this tile reads has changed')).toBeVisible()
    await tile.getByRole('button', { name: 'Review and accept' }).click()
    await expect(tile.getByText('The data this tile reads has changed')).toHaveCount(0)
    expect(api.tileCalls).toContain('POST /tiles/tile-1/confirm')
  })

  test('a failed refresh shows why and can be retried', async ({ page }) => {
    const api = await withOnePinnedTile(page)
    api.setTileState('tile-1', 'error', 'canceling statement due to statement timeout')
    await page.reload()
    const tile = page.getByTestId('tile').first()
    await expect(tile.getByText('Last refresh failed: canceling statement due to statement timeout')).toBeVisible()
    await tile.getByRole('button', { name: 'Retry' }).click()
    await expect.poll(() => api.tileCalls.filter((c) => c.endsWith('/refresh')).length).toBe(1)
  })

  test('developer mode shows the SQL a tile runs', async ({ page }) => {
    await withOnePinnedTile(page)
    await page.getByRole('switch', { name: 'Developer mode' }).click()
    await tileActions(page, 'View SQL')
    await expect(page.getByRole('dialog', { name: 'Tile SQL' })).toContainText(
      'SELECT COUNT(*) AS total_orders FROM orders',
    )
  })
})
