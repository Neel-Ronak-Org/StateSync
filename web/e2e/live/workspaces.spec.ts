/**
 * Live: setting up a workspace in a real browser against the real stack
 * (docs/V3_PLAN.md Phase 3). What only this can show: the browser's CORS
 * preflight lets every method through (a PUT was refused in Phase 2b while all
 * mocked tests passed), the production CSP allows the new pages, and the AI
 * drafts arrive from the real model.
 *
 * It asks the SQL model nothing (no setup test): drafting uses the planner
 * model only. The workspace it creates is deleted by the UI, and through the
 * API if the test fails midway.
 */

import { expect, test } from '@playwright/test'

import { API, devToken, signIn, watchCsp } from './helpers'

const NAME = 'Live e2e workspace'
const DRAFT_TIMEOUT = 180_000

test('a workspace is created, drafted by AI, edited, approved and deleted', async ({ page, request }) => {
  const auth = { authorization: `Bearer ${devToken()}` }
  const violations = await watchCsp(page)
  try {
    await signIn(page)
    await page.getByRole('button', { name: 'Workspaces', exact: true }).click()
    await page.getByRole('button', { name: 'New workspace' }).click()
    const dialog = page.getByRole('dialog', { name: 'New workspace' })
    await dialog.getByLabel('Name').fill(NAME)
    await dialog.getByRole('combobox', { name: 'Dataset' }).click()
    await page.getByRole('option', { name: 'olist', exact: true }).click()
    await dialog.getByLabel('About your business').fill('A Brazilian online marketplace.')
    await dialog.getByRole('button', { name: 'Create' }).click()
    await expect(page.getByRole('heading', { name: NAME, exact: true })).toBeVisible()

    await page.getByRole('button', { name: 'Draft with AI' }).click()
    const status = page.getByLabel('Description of orders.order_status')
    await expect(status).not.toHaveValue('', { timeout: DRAFT_TIMEOUT })
    await expect(page.getByTestId('draft-progress')).toHaveCount(0, { timeout: DRAFT_TIMEOUT })

    // The PUT that CORS used to refuse.
    await status.fill('Where the order is in its life cycle.')
    const saved = page.waitForResponse((r) => r.url().endsWith('/descriptions') && r.request().method() === 'PUT')
    await page.getByRole('button', { name: 'Save changes' }).click()
    expect((await saved).status()).toBe(200)
    const row = page.locator('[data-testid="catalog-row"][data-table="orders"][data-column="order_status"]')
    await expect(row.getByTestId('source-badge')).toHaveText('Yours')

    await page.getByRole('button', { name: 'Approve' }).click()
    await expect(page.getByRole('tab', { name: 'Test' })).toHaveAttribute('aria-selected', 'true')
    await expect(page.getByRole('button', { name: 'Run the test' })).toBeEnabled()

    await page.getByRole('button', { name: 'Workspace actions' }).click()
    await page.getByRole('menuitem', { name: 'Delete workspace' }).click()
    await page.getByRole('alertdialog').or(page.getByRole('dialog')).getByRole('button', { name: 'Delete' }).click()
    await expect(page).toHaveURL(/#\/workspaces$/)
    expect(await violations()).toEqual([])
  } finally {
    const list = await request.get(`${API}/workspaces`, { headers: auth })
    for (const ws of (await list.json()).workspaces ?? []) {
      if (ws.name === NAME) await request.delete(`${API}/workspaces/${ws.workspace_id}`, { headers: auth })
    }
  }
})
