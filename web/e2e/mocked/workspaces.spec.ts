/**
 * Acceptance: workspaces and the setup wizard (docs/V3_PLAN.md Phase 3,
 * slice 4; web/DESIGN.md §7). Written before the implementation: each test is
 * one thing a person setting up their own data must be able to do, against
 * the mocked API in mock-workspaces.ts, which mirrors the real routes.
 */

import { expect, test, type Page } from '@playwright/test'

import { ask, openApp } from './helpers'
import type { MockApi } from './mock-api'

async function openWorkspaces(page: Page) {
  await page.getByRole('button', { name: 'Workspaces', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Workspaces', exact: true })).toBeVisible()
}

async function createWorkspace(page: Page, name: string, dataset: string, about = '') {
  await page.getByRole('button', { name: 'New workspace' }).click()
  const dialog = page.getByRole('dialog', { name: 'New workspace' })
  await dialog.getByLabel('Name').fill(name)
  await dialog.getByRole('combobox', { name: 'Dataset' }).click()
  await page.getByRole('option', { name: dataset, exact: true }).click()
  if (about) await dialog.getByLabel('About your business').fill(about)
  await dialog.getByRole('button', { name: 'Create' }).click()
  await expect(page.getByRole('heading', { name, exact: true })).toBeVisible()
}

function row(page: Page, table: string, column = '') {
  return page.locator(`[data-testid="catalog-row"][data-table="${table}"][data-column="${column}"]`)
}

async function draftAndApprove(page: Page) {
  await page.getByRole('button', { name: 'Draft with AI' }).click()
  await expect(row(page, 'orders', 'order_status')).toBeVisible()
  await page.getByRole('button', { name: 'Approve' }).click()
  await expect(page.getByRole('tab', { name: 'Test' })).toHaveAttribute('aria-selected', 'true')
}

test.describe('the workspaces page', () => {
  test('is in the rail, has its own address, and explains itself when empty', async ({ page }) => {
    await openApp(page)
    await openWorkspaces(page)
    await expect(page).toHaveURL(/#\/workspaces$/)
    await expect(page.getByRole('button', { name: 'Workspaces', exact: true })).toHaveAttribute('aria-current', 'page')
    await expect(page.getByRole('heading', { name: 'No workspaces yet' })).toBeVisible()
  })

  test('lists workspaces with their dataset and setup score', async ({ page }) => {
    await openApp(page, { hash: '#/workspaces', seed: (api) => void api.workspaces.createReady('Furniture shop', 'olist') })
    const card = page.getByTestId('workspace-card')
    await expect(card).toHaveCount(1)
    await expect(card).toContainText('Furniture shop')
    await expect(card).toContainText('olist')
    await expect(card).toContainText('9/10')
  })

  test('a workspace address opens it directly, and survives a reload', async ({ page }) => {
    await openApp(page, { hash: '#/workspaces/ws-1', seed: (api) => void api.workspaces.createReady('Furniture shop', 'olist') })
    await expect(page.getByRole('heading', { name: 'Furniture shop', exact: true })).toBeVisible()
    await page.reload()
    await expect(page.getByRole('heading', { name: 'Furniture shop', exact: true })).toBeVisible()
  })

  test('an unknown workspace address says so and offers the way back', async ({ page }) => {
    await openApp(page, { hash: '#/workspaces/ws-404' })
    await expect(page.getByText('This workspace does not exist')).toBeVisible()
    await page.getByRole('button', { name: 'All workspaces' }).click()
    await expect(page).toHaveURL(/#\/workspaces$/)
  })
})

test.describe('creating a workspace', () => {
  test('needs a name, and opens the new workspace on the knowledge step', async ({ page }) => {
    const api = await openApp(page, { hash: '#/workspaces' })
    await page.getByRole('button', { name: 'New workspace' }).click()
    const dialog = page.getByRole('dialog', { name: 'New workspace' })
    await expect(dialog.getByRole('button', { name: 'Create' })).toBeDisabled()
    await dialog.getByRole('button', { name: 'Cancel' }).click()

    await createWorkspace(page, 'Furniture shop', 'olist', 'We sell furniture across Brazil.')
    await expect(page).toHaveURL(/#\/workspaces\/ws-1$/)
    await expect(page.getByRole('tab', { name: 'Knowledge' })).toHaveAttribute('aria-selected', 'true')
    const created = api.workspaces.workspaces.get('ws-1')
    expect(created?.target_schema).toBe('olist')
    expect(created?.business_context).toBe('We sell furniture across Brazil.')
  })

  test('a server refusal is shown in the dialog', async ({ page }) => {
    const api = await openApp(page, { hash: '#/workspaces' })
    api.failNext('/workspaces', 409, 'You already have 12 workspaces; the limit is 12.')
    await page.getByRole('button', { name: 'New workspace' }).click()
    const dialog = page.getByRole('dialog', { name: 'New workspace' })
    await dialog.getByLabel('Name').fill('One too many')
    await dialog.getByRole('button', { name: 'Create' }).click()
    await expect(dialog.getByText('You already have 12 workspaces; the limit is 12.')).toBeVisible()
  })
})

test.describe('the knowledge step', () => {
  test('drafts descriptions with AI and marks them as AI drafts', async ({ page }) => {
    await openApp(page, { hash: '#/workspaces' })
    await createWorkspace(page, 'Shop', 'olist')
    await page.getByRole('button', { name: 'Draft with AI' }).click()
    await expect(page.getByTestId('draft-progress')).toBeVisible()
    await expect(row(page, 'orders', 'order_status')).toBeVisible()
    await expect(page.getByTestId('draft-progress')).toHaveCount(0)
    await expect(row(page, 'orders', 'order_status').getByTestId('source-badge')).toHaveText('AI draft')
    await expect(page.getByLabel('Description of orders.order_status')).toHaveValue('AI: the order status.')
    await expect(page.getByLabel('Description of orders', { exact: true })).toHaveValue('AI: one row per order.')
    // Sample values help the owner judge a description.
    await expect(row(page, 'orders', 'order_status')).toContainText('delivered')
  })

  test('the owner edits a description; it is saved as theirs and needs approval again', async ({ page }) => {
    const api = await openApp(page, { hash: '#/workspaces' })
    await createWorkspace(page, 'Shop', 'olist')
    await draftAndApprove(page)
    await page.getByRole('tab', { name: 'Knowledge' }).click()

    const save = page.getByRole('button', { name: 'Save changes' })
    await expect(save).toBeDisabled()
    await page.getByLabel('Description of orders.order_status').fill('Where the order is in its life cycle.')
    await expect(save).toBeEnabled()
    await save.click()
    await expect(row(page, 'orders', 'order_status').getByTestId('source-badge')).toHaveText('Yours')
    await expect(row(page, 'orders', 'order_status')).toHaveAttribute('data-approved', 'false')
    await expect(row(page, 'orders', 'order_id')).toHaveAttribute('data-approved', 'true')
    const saved = api.workspaces.workspaces.get('ws-1')?.catalog.find((c) => c.column_name === 'order_status')
    expect(saved?.description).toBe('Where the order is in its life cycle.')
    // Only the changed row was sent.
    expect(api.workspaces.calls.filter((c) => c.startsWith('PUT /workspaces/ws-1/descriptions'))).toHaveLength(1)
  })

  test('a failed draft says why and can be retried', async ({ page }) => {
    const api = await openApp(page, { hash: '#/workspaces' })
    await createWorkspace(page, 'Shop', 'olist')
    api.workspaces.failNextDraft = "The model's drafts could not be read. Try again."
    await page.getByRole('button', { name: 'Draft with AI' }).click()
    await expect(page.getByText("The model's drafts could not be read. Try again.")).toBeVisible()
    await page.getByRole('button', { name: 'Draft with AI' }).click()
    await expect(row(page, 'orders', 'order_status')).toBeVisible()
  })

  test('business definitions can be added and removed', async ({ page }) => {
    const api = await openApp(page, { hash: '#/workspaces' })
    await createWorkspace(page, 'Shop', 'olist')
    await page.getByRole('button', { name: 'Draft with AI' }).click()
    await expect(page.getByTestId('definition-row')).toHaveCount(2)

    await page.getByLabel('Term').fill('GMV')
    await page.getByLabel('Definition', { exact: true }).fill('Price plus freight, before refunds.')
    await page.getByRole('button', { name: 'Add definition' }).click()
    await expect(page.getByTestId('definition-row')).toHaveCount(3)
    await expect(page.getByTestId('definition-row').filter({ hasText: 'GMV' })).toContainText('Yours')

    await page.getByRole('button', { name: 'Delete definition Revenue' }).click()
    await expect(page.getByTestId('definition-row')).toHaveCount(2)
    expect(api.workspaces.workspaces.get('ws-1')?.definitions.map((d) => d.term)).toEqual(['Late delivery', 'GMV'])
  })

  test('approving moves on to the test', async ({ page }) => {
    const api = await openApp(page, { hash: '#/workspaces' })
    await createWorkspace(page, 'Shop', 'olist')
    await draftAndApprove(page)
    expect(api.workspaces.workspaces.get('ws-1')?.setup_state).toBe('approved')
  })
})

test.describe('the business step', () => {
  test('the owner describes the business; each save is a new version', async ({ page }) => {
    const api = await openApp(page, { hash: '#/workspaces' })
    await createWorkspace(page, 'Shop', 'olist', 'We sell furniture.')
    await page.getByRole('tab', { name: 'Business' }).click()
    await expect(page.getByLabel('About your business')).toHaveValue('We sell furniture.')
    await expect(page.getByText('Version 1')).toBeVisible()
    await page.getByLabel('About your business').fill('We sell furniture. Revenue means price plus freight.')
    await page.getByRole('button', { name: 'Save', exact: true }).click()
    await expect(page.getByText('Version 2')).toBeVisible()
    expect(api.workspaces.workspaces.get('ws-1')?.business_context).toBe('We sell furniture. Revenue means price plus freight.')
  })
})

test.describe('the setup test', () => {
  test('cannot run before the knowledge is approved', async ({ page }) => {
    await openApp(page, { hash: '#/workspaces' })
    await createWorkspace(page, 'Shop', 'olist')
    await page.getByRole('tab', { name: 'Test' }).click()
    await expect(page.getByRole('button', { name: 'Run the test' })).toBeDisabled()
    await expect(page.getByText('Approve the knowledge first')).toBeVisible()
  })

  test('on a sample dataset it is graded against reference answers and scored', async ({ page }) => {
    const api = await openApp(page, { hash: '#/workspaces' })
    await createWorkspace(page, 'Shop', 'olist')
    await draftAndApprove(page)
    await expect(page.getByText('Graded against reference answers')).toBeVisible()
    await page.getByRole('button', { name: 'Run the test' }).click()
    await expect(page.getByTestId('setup-result')).toHaveCount(10)
    await expect(page.getByTestId('setup-score')).toHaveText('8/10')
    await expect(page.getByText('Ready to use')).toBeVisible()
    await expect(page.locator('[data-testid="setup-result"][data-verdict="wrong"]')).toHaveCount(2)
    await expect(page.locator('[data-testid="setup-result"][data-verdict="right"]')).toHaveCount(8)
    // Why an answer was wrong is shown with it.
    await expect(page.getByTestId('setup-result').nth(2)).toContainText('60% of its rows found')
    expect(api.workspaces.workspaces.get('ws-1')?.examples).toHaveLength(8)

    await openWorkspaces(page)
    await expect(page.getByTestId('workspace-card')).toContainText('8/10')
  })

  test('a low score says the workspace needs work', async ({ page }) => {
    const api = await openApp(page, { hash: '#/workspaces' })
    api.workspaces.wrongPositions = [1, 2, 3, 4, 5]
    await createWorkspace(page, 'Shop', 'olist')
    await draftAndApprove(page)
    await page.getByRole('button', { name: 'Run the test' }).click()
    await expect(page.getByTestId('setup-score')).toHaveText('5/10')
    await expect(page.getByText('Needs work')).toBeVisible()
  })

  test('elsewhere the owner grades each answer, and the score follows', async ({ page }) => {
    await openApp(page, {
      hash: '#/workspaces/ws-1',
      seed: (api) => {
        const ws = api.workspaces.create('Live shop', 'live')
        ws.setup_state = 'approved'
      },
    })
    await page.getByRole('tab', { name: 'Test' }).click()
    await expect(page.getByText('You grade each answer')).toBeVisible()
    await page.getByRole('button', { name: 'Run the test' }).click()
    await expect(page.locator('[data-testid="setup-result"][data-verdict="pending"]')).toHaveCount(10)
    await page.getByRole('button', { name: 'Mark question 1 right' }).click()
    await page.getByRole('button', { name: 'Mark question 2 right' }).click()
    await page.getByRole('button', { name: 'Mark question 3 wrong' }).click()
    await expect(page.getByTestId('setup-score')).toHaveText('2/10')
    await expect(page.locator('[data-testid="setup-result"][data-verdict="wrong"]')).toHaveCount(1)
  })
})

test.describe('asking in a workspace', () => {
  function seedReady(api: MockApi) {
    api.workspaces.createReady('Furniture shop', 'olist', [
      { question: 'How many orders are there in total?', sql: 'SELECT count(*) FROM olist.orders' },
    ])
  }

  test('the picker offers workspaces; asking in one sends it and uses its dataset', async ({ page }) => {
    const api = await openApp(page, { hash: '#/ask', seed: seedReady })
    await page.getByRole('combobox', { name: 'Dataset' }).click()
    await page.getByRole('option', { name: 'Furniture shop' }).click()
    await expect(page.getByRole('combobox', { name: 'Dataset' })).toContainText('Furniture shop')
    await ask(page, 'How many orders are there in total?')
    await expect(page.getByTestId('answer-text').last()).toBeVisible()
    const sent = api.queries[api.queries.length - 1]
    expect(sent.workspace_id).toBe('ws-1')
    expect(sent.schema ?? 'olist').toBe('olist')
  })

  test('an answer from an approved example is marked verified; others are not', async ({ page }) => {
    await openApp(page, { hash: '#/ask', seed: seedReady })
    await page.getByRole('combobox', { name: 'Dataset' }).click()
    await page.getByRole('option', { name: 'Furniture shop' }).click()
    await ask(page, 'How many orders are there in total?')
    await expect(page.getByTestId('turn').last().getByTestId('verified-badge')).toHaveText('Verified')
    await ask(page, 'Show orders by status as a chart')
    await expect(page.getByTestId('answer-text')).toHaveCount(2)
    await expect(page.getByTestId('turn').last().getByTestId('verified-badge')).toHaveCount(0)
  })

  test('"Ask in this workspace" switches to it and opens Ask', async ({ page }) => {
    const api = await openApp(page, { hash: '#/workspaces/ws-1', seed: seedReady })
    await page.getByRole('button', { name: 'Ask in this workspace' }).click()
    await expect(page).toHaveURL(/#\/ask$/)
    await expect(page.getByRole('combobox', { name: 'Dataset' })).toContainText('Furniture shop')
    await ask(page, 'How many orders are there in total?')
    await expect(page.getByTestId('answer-text').last()).toBeVisible()
    expect(api.queries[api.queries.length - 1].workspace_id).toBe('ws-1')
  })

  test('picking a plain dataset leaves the workspace', async ({ page }) => {
    const api = await openApp(page, { hash: '#/ask', seed: seedReady })
    await page.getByRole('combobox', { name: 'Dataset' }).click()
    await page.getByRole('option', { name: 'Furniture shop' }).click()
    await page.getByRole('combobox', { name: 'Dataset' }).click()
    await page.getByRole('option', { name: 'public', exact: true }).click()
    await ask(page, 'How many orders are there in total?')
    await expect(page.getByTestId('answer-text').last()).toBeVisible()
    const sent = api.queries[api.queries.length - 1]
    expect(sent.workspace_id ?? null).toBeNull()
    expect(sent.schema).toBe('public')
  })

  test('the chosen workspace is remembered across a reload', async ({ page }) => {
    await openApp(page, { hash: '#/ask', seed: seedReady })
    await page.getByRole('combobox', { name: 'Dataset' }).click()
    await page.getByRole('option', { name: 'Furniture shop' }).click()
    await page.reload()
    await expect(page.getByRole('combobox', { name: 'Dataset' })).toContainText('Furniture shop')
  })
})

test.describe('deleting a workspace', () => {
  test('asks first, then removes it and returns to the list', async ({ page }) => {
    const api = await openApp(page, { hash: '#/workspaces/ws-1', seed: (a) => void a.workspaces.createReady('Old shop', 'olist') })
    await page.getByRole('button', { name: 'Workspace actions' }).click()
    await page.getByRole('menuitem', { name: 'Delete workspace' }).click()
    const confirm = page.getByRole('alertdialog').or(page.getByRole('dialog'))
    await expect(confirm).toContainText('Old shop')
    await confirm.getByRole('button', { name: 'Delete' }).click()
    await expect(page).toHaveURL(/#\/workspaces$/)
    await expect(page.getByRole('heading', { name: 'No workspaces yet' })).toBeVisible()
    expect(api.workspaces.workspaces.size).toBe(0)
  })

  test('deleting the active workspace falls back to its dataset', async ({ page }) => {
    const api = await openApp(page, { hash: '#/ask', seed: (a) => void a.workspaces.createReady('Old shop', 'olist') })
    await page.getByRole('combobox', { name: 'Dataset' }).click()
    await page.getByRole('option', { name: 'Old shop' }).click()
    await page.goto('/#/workspaces/ws-1')
    await page.getByRole('button', { name: 'Workspace actions' }).click()
    await page.getByRole('menuitem', { name: 'Delete workspace' }).click()
    await page.getByRole('alertdialog').or(page.getByRole('dialog')).getByRole('button', { name: 'Delete' }).click()
    await page.getByRole('button', { name: 'Ask', exact: true }).click()
    await ask(page, 'How many orders are there in total?')
    await expect(page.getByTestId('answer-text').last()).toBeVisible()
    const sent = api.queries[api.queries.length - 1]
    expect(sent.workspace_id ?? null).toBeNull()
    expect(sent.schema).toBe('olist')
  })
})
