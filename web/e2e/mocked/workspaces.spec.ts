/**
 * Acceptance: workspaces. A workspace is one business: a connected database, the document folders it
 * searches, and what its owner taught StateSync about it. Each test is one thing an owner must be able to
 * do, against the mocked API in mock-workspaces.ts, which mirrors the real routes.
 */

import { expect, test, type Page } from '@playwright/test'

import { ask, openApp } from './helpers'
import type { MockApi } from './mock-api'

async function openWorkspaces(page: Page) {
  await page.getByRole('button', { name: 'Workspaces', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Workspaces', exact: true })).toBeVisible()
}

/** A connected database with a schema, so a workspace can be created on it. */
function withDatabase(api: MockApi, name = 'Shop replica'): string {
  return api.connections.add(name).connection_id
}

async function createWorkspace(page: Page, name: string, database: string, about = '') {
  await page.getByRole('button', { name: 'New workspace' }).click()
  const dialog = page.getByRole('dialog', { name: 'New workspace' })
  await dialog.getByLabel('Name').fill(name)
  await dialog.getByRole('combobox', { name: 'Database' }).click()
  await page.getByRole('option', { name: database, exact: true }).click()
  if (about) await dialog.getByLabel('About your business').fill(about)
  await dialog.getByRole('button', { name: 'Create' }).click()
  await expect(page.getByRole('heading', { name, exact: true })).toBeVisible()
}

function row(page: Page, table: string, column = '') {
  return page.locator(`[data-testid="catalog-row"][data-table="${table}"][data-column="${column}"]`)
}

async function pickWorkspace(page: Page, name: string) {
  await page.getByRole('combobox', { name: 'Workspace' }).click()
  await page.getByRole('option', { name }).click()
  await expect(page.getByRole('combobox', { name: 'Workspace' })).toContainText(name)
}

test.describe('the workspaces page', () => {
  test('is in the rail, has its own address, and explains itself when empty', async ({ page }) => {
    await openApp(page, { noWorkspace: true })
    await openWorkspaces(page)
    await expect(page).toHaveURL(/#\/workspaces$/)
    await expect(page.getByRole('button', { name: 'Workspaces', exact: true })).toHaveAttribute('aria-current', 'page')
    await expect(page.getByRole('heading', { name: 'No workspaces yet' })).toBeVisible()
  })

  test('lists workspaces with their database', async ({ page }) => {
    await openApp(page, {
      hash: '#/workspaces',
      seed: (api) => void api.workspaces.createReady('Furniture shop', withDatabase(api)),
    })
    const card = page.getByTestId('workspace-card')
    await expect(card).toHaveCount(1)
    await expect(card).toContainText('Furniture shop')
    await expect(card).toContainText('Shop replica')
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
  test('needs a connected database and a name, and opens on the overview', async ({ page }) => {
    let id = ''
    const api = await openApp(page, { hash: '#/workspaces', noWorkspace: true, seed: (a) => void (id = withDatabase(a)) })
    await page.getByRole('button', { name: 'New workspace' }).click()
    const dialog = page.getByRole('dialog', { name: 'New workspace' })
    await expect(dialog.getByRole('button', { name: 'Create' })).toBeDisabled()
    await dialog.getByRole('button', { name: 'Cancel' }).click()

    await createWorkspace(page, 'Furniture shop', 'Shop replica', 'We sell furniture across Brazil.')
    await expect(page).toHaveURL(/#\/workspaces\/ws-1$/)
    await expect(page.getByRole('tab', { name: 'Overview' })).toHaveAttribute('aria-selected', 'true')
    const created = api.workspaces.workspaces.get('ws-1')
    expect(created?.target_schema).toBe(id)
    expect(created?.business_context).toBe('We sell furniture across Brazil.')
  })

  test('without a connected database the dialog says where to connect one', async ({ page }) => {
    await openApp(page, { hash: '#/workspaces', noWorkspace: true })
    await page.getByRole('button', { name: 'New workspace' }).click()
    const dialog = page.getByRole('dialog', { name: 'New workspace' })
    await expect(dialog.getByText('Connect a database first')).toBeVisible()
    await dialog.getByLabel('Name').fill('Shop')
    await expect(dialog.getByRole('button', { name: 'Create' })).toBeDisabled()
  })

  test('a server refusal is shown in the dialog', async ({ page }) => {
    const api = await openApp(page, { hash: '#/workspaces', noWorkspace: true, seed: (a) => void withDatabase(a) })
    // failNext matches the path for any method: let the list load first.
    await expect(page.getByRole('heading', { name: 'No workspaces yet' })).toBeVisible()
    api.failNext('/workspaces', 409, 'You already have 24 workspaces; the limit is 24.')
    await page.getByRole('button', { name: 'New workspace' }).click()
    const dialog = page.getByRole('dialog', { name: 'New workspace' })
    await dialog.getByLabel('Name').fill('One too many')
    await dialog.getByRole('button', { name: 'Create' }).click()
    await expect(dialog.getByText('You already have 24 workspaces; the limit is 24.')).toBeVisible()
  })
})

test.describe('the knowledge step', () => {
  async function newShop(page: Page) {
    const api = await openApp(page, { hash: '#/workspaces', noWorkspace: true, seed: (a) => void withDatabase(a) })
    await createWorkspace(page, 'Shop', 'Shop replica')
    await page.getByRole('tab', { name: 'Knowledge' }).click()
    return api
  }

  test('drafts descriptions with AI and marks them as AI drafts', async ({ page }) => {
    await newShop(page)
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
    const api = await newShop(page)
    await page.getByRole('button', { name: 'Draft with AI' }).click()
    await expect(row(page, 'orders', 'order_status')).toBeVisible()
    await page.getByRole('button', { name: 'Approve' }).click()
    await expect(row(page, 'orders', 'order_id')).toHaveAttribute('data-approved', 'true')

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
    const api = await newShop(page)
    api.workspaces.failNextDraft = "The model's drafts could not be read. Try again."
    await page.getByRole('button', { name: 'Draft with AI' }).click()
    await expect(page.getByText("The model's drafts could not be read. Try again.")).toBeVisible()
    await page.getByRole('button', { name: 'Draft with AI' }).click()
    await expect(row(page, 'orders', 'order_status')).toBeVisible()
  })

  test('business definitions can be added and removed', async ({ page }) => {
    const api = await newShop(page)
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

  test('approving waits until edits are saved, then approves', async ({ page }) => {
    const api = await newShop(page)
    await page.getByRole('button', { name: 'Draft with AI' }).click()
    await expect(row(page, 'orders', 'order_status')).toBeVisible()
    await page.getByLabel('Description of orders.order_status').fill('Unsaved words.')
    await expect(page.getByRole('button', { name: 'Approve' })).toBeDisabled()
    await expect(page.getByText('Save your changes before approving')).toBeVisible()
    await page.getByRole('button', { name: 'Save changes' }).click()
    await page.getByRole('button', { name: 'Approve' }).click()
    await expect.poll(() => api.workspaces.workspaces.get('ws-1')?.setup_state).toBe('approved')
  })
})

test.describe('the business step', () => {
  test('the owner describes the business; each save is a new version', async ({ page }) => {
    const api = await openApp(page, { hash: '#/workspaces', noWorkspace: true, seed: (a) => void withDatabase(a) })
    await createWorkspace(page, 'Shop', 'Shop replica', 'We sell furniture.')
    await page.getByRole('tab', { name: 'Business' }).click()
    await expect(page.getByLabel('About your business')).toHaveValue('We sell furniture.')
    await expect(page.getByText('Version 1')).toBeVisible()
    await page.getByLabel('About your business').fill('We sell furniture. Revenue means price plus freight.')
    await page.getByRole('button', { name: 'Save', exact: true }).click()
    await expect(page.getByText('Version 2')).toBeVisible()
    expect(api.workspaces.workspaces.get('ws-1')?.business_context).toBe('We sell furniture. Revenue means price plus freight.')
  })
})

test.describe('approved answers', () => {
  const Q = ['How many orders are there?', 'Revenue by month?']

  test('are listed with their SQL, and one can be removed', async ({ page }) => {
    const api = await openApp(page, {
      hash: '#/workspaces/ws-1',
      seed: (a) => void a.workspaces.createReady('Furniture shop', 'olist', Q.map((question) => ({ question, sql: 'SELECT 1' }))),
    })
    await page.getByRole('tab', { name: 'Approved' }).click()
    await expect(page.getByTestId('approved-answer')).toHaveCount(2)
    await expect(page.getByTestId('approved-answer').first()).toContainText('SELECT 1')
    await page.getByRole('button', { name: `Remove approved answer ${Q[1]}` }).click()
    await expect(page.getByTestId('approved-answer')).toHaveCount(1)
    expect(api.workspaces.workspaces.get('ws-1')?.examples.map((e) => e.question)).toEqual([Q[0]])
  })
})

test.describe('asking in a workspace', () => {
  function seedTwo(api: MockApi) {
    api.workspaces.createReady('Furniture shop', 'olist', [
      { question: 'How many orders are there in total?', sql: 'SELECT count(*) FROM olist.orders' },
    ])
    api.workspaces.createReady('Network', 'public')
  }

  test('the picker offers the workspaces; asking in one sends it', async ({ page }) => {
    const api = await openApp(page, { hash: '#/ask', seed: seedTwo })
    await pickWorkspace(page, 'Network')
    await ask(page, 'How many orders are there in total?')
    await expect(page.getByTestId('answer-text').last()).toBeVisible()
    expect(api.queries[api.queries.length - 1].workspace_id).toBe('ws-2')
  })

  test('an answer from an approved example is marked verified; others are not', async ({ page }) => {
    await openApp(page, { hash: '#/ask', seed: seedTwo })
    await pickWorkspace(page, 'Furniture shop')
    await ask(page, 'How many orders are there in total?')
    await expect(page.getByTestId('turn').last().getByTestId('verified-badge')).toHaveText('Verified')
    await ask(page, 'Show orders by status as a chart')
    await expect(page.getByTestId('answer-text')).toHaveCount(2)
    await expect(page.getByTestId('turn').last().getByTestId('verified-badge')).toHaveCount(0)
  })

  test('"Ask in this workspace" switches to it and opens Ask', async ({ page }) => {
    const api = await openApp(page, { hash: '#/workspaces/ws-2', seed: seedTwo })
    await page.getByRole('button', { name: 'Ask in this workspace' }).click()
    await expect(page).toHaveURL(/#\/ask$/)
    await expect(page.getByRole('combobox', { name: 'Workspace' })).toContainText('Network')
    await ask(page, 'How many orders are there in total?')
    await expect(page.getByTestId('answer-text').last()).toBeVisible()
    expect(api.queries[api.queries.length - 1].workspace_id).toBe('ws-2')
  })

  test('the chosen workspace is remembered across a reload', async ({ page }) => {
    await openApp(page, { hash: '#/ask', seed: seedTwo })
    await pickWorkspace(page, 'Network')
    await page.reload()
    await expect(page.getByRole('combobox', { name: 'Workspace' })).toContainText('Network')
  })
})

test.describe('found in review', () => {
  test('a first visit asks to sign in without claiming a session expired', async ({ page }) => {
    await openApp(page, { signedIn: false })
    await expect(page.getByRole('button', { name: /sign in/i })).toBeVisible()
    await expect(page.getByText('Your session expired')).toHaveCount(0)
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

  test('deleting the active workspace moves Ask to another one', async ({ page }) => {
    const api = await openApp(page, {
      hash: '#/ask',
      seed: (a) => {
        a.workspaces.createReady('Old shop', 'olist')
        a.workspaces.createReady('Network', 'public')
      },
    })
    await pickWorkspace(page, 'Old shop')
    await page.goto('/#/workspaces/ws-1')
    await page.getByRole('button', { name: 'Workspace actions' }).click()
    await page.getByRole('menuitem', { name: 'Delete workspace' }).click()
    await page.getByRole('alertdialog').or(page.getByRole('dialog')).getByRole('button', { name: 'Delete' }).click()
    await page.getByRole('button', { name: 'Ask', exact: true }).click()
    await expect(page.getByRole('combobox', { name: 'Workspace' })).toContainText('Network')
    await ask(page, 'How many orders are there in total?')
    await expect(page.getByTestId('answer-text').last()).toBeVisible()
    expect(api.queries[api.queries.length - 1].workspace_id).toBe('ws-2')
  })
})

test.describe('phone width', () => {
  test('the list and the knowledge step fit 375 px without sideways scrolling', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 })
    await openApp(page, { hash: '#/workspaces', seed: (a) => void a.workspaces.createReady('A rather long workspace name', 'olist') })
    // The shell clips the page, so look inside every scroll container too.
    const overflow = () =>
      page.evaluate(() => {
        const els = [document.documentElement, ...Array.from(document.querySelectorAll<HTMLElement>('*'))].filter(
          (el) => el === document.documentElement || /(auto|scroll)/.test(getComputedStyle(el).overflowX),
        )
        return Math.max(...els.map((el) => el.scrollWidth - el.clientWidth))
      })
    await expect(page.getByTestId('workspace-card')).toBeVisible()
    expect(await overflow()).toBeLessThanOrEqual(0)
    await page.getByTestId('workspace-card').click()
    await page.getByRole('tab', { name: 'Knowledge' }).click()
    await expect(page.getByTestId('catalog-row').first()).toBeVisible()
    expect(await overflow()).toBeLessThanOrEqual(0)
  })
})

test.describe('evidence and thumbs', () => {
  const QUESTION = 'How many orders are there in total?'

  async function inWorkspace(page: Page, examples: { question: string; sql: string }[] = []) {
    return openApp(page, { hash: '#/ask', seed: (a) => void a.workspaces.createReady('Furniture shop', 'olist', examples) })
  }

  test('a new query shows its chip and thumbs; thumbs up makes the next identical question verified', async ({ page }) => {
    const api = await inWorkspace(page)
    await ask(page, QUESTION)
    const first = page.getByTestId('turn').last()
    await expect(first.getByTestId('evidence-chip')).toHaveText('New query')
    await expect(first.getByTestId('evidence-chip')).toHaveAttribute('data-evidence', 'unchecked')
    await first.getByRole('button', { name: 'Mark answer correct' }).click()
    await expect(page.getByText('Saved as an approved answer for this workspace')).toBeVisible()
    await expect(first.getByRole('button', { name: 'Mark answer correct' })).toBeDisabled()
    await expect(first.getByRole('button', { name: 'Mark answer wrong' })).toBeDisabled()
    expect(api.workspaces.workspaces.get('ws-1')?.examples.map((e) => e.question)).toEqual([QUESTION])

    await ask(page, QUESTION)
    await expect(page.getByTestId('answer-text')).toHaveCount(2)
    await expect(page.getByTestId('turn').last().getByTestId('evidence-chip')).toHaveAttribute('data-evidence', 'verified')
    await expect(page.getByTestId('turn').last().getByTestId('verified-badge')).toHaveText('Verified')
  })

  test('thumbs down on a verified answer withdraws it; the next identical question is not verified', async ({ page }) => {
    const api = await inWorkspace(page, [{ question: QUESTION, sql: 'SELECT count(*) FROM olist.orders' }])
    await ask(page, QUESTION)
    const first = page.getByTestId('turn').last()
    await expect(first.getByTestId('evidence-chip')).toHaveAttribute('data-evidence', 'verified')
    await first.getByRole('button', { name: 'Mark answer wrong' }).click()
    await expect(page.getByText('Removed the approved answer; the next time it will be worked out again')).toBeVisible()
    expect(api.workspaces.workspaces.get('ws-1')?.examples).toEqual([])

    await ask(page, QUESTION)
    await expect(page.getByTestId('answer-text')).toHaveCount(2)
    await expect(page.getByTestId('turn').last().getByTestId('evidence-chip')).toHaveAttribute('data-evidence', 'unchecked')
  })

  test('thumbs down on an unverified answer only says thanks, with no server call', async ({ page }) => {
    const api = await inWorkspace(page)
    await ask(page, QUESTION)
    await page.getByTestId('turn').last().getByRole('button', { name: 'Mark answer wrong' }).click()
    await expect(page.getByText('Thanks — noted')).toBeVisible()
    expect(api.workspaces.calls.filter((c) => c.includes('/examples'))).toEqual([])
  })

  test('a documents answer has no chip and no thumbs', async ({ page }) => {
    await inWorkspace(page)
    await ask(page, 'What does the SLA policy say for LP003?')
    const turn = page.getByTestId('turn').last()
    await expect(turn.getByTestId('answer-text')).toBeVisible()
    await expect(turn.getByTestId('evidence-chip')).toHaveCount(0)
    await expect(turn.getByRole('button', { name: 'Mark answer correct' })).toHaveCount(0)
  })
})

test.describe('the overview', () => {
  test('shows each part of the setup with its state, and every Edit opens the step that changes it', async ({ page }) => {
    await openApp(page, { hash: '#/workspaces/ws-1', seed: (a) => void a.workspaces.createReady('Furniture shop', 'olist') })
    await expect(page.getByRole('tab', { name: 'Overview' })).toHaveAttribute('aria-selected', 'true')
    const steps = page.getByTestId('setup-step')
    await expect(steps).toHaveCount(5)
    await expect(steps.nth(0)).toContainText('1. Database')
    await expect(steps.nth(1)).toContainText('2. Documents')
    await expect(steps.nth(1)).toHaveAttribute('data-done', 'false')
    await steps.nth(2).getByRole('button', { name: 'Edit' }).click()
    await expect(page.getByRole('tab', { name: 'Business' })).toHaveAttribute('aria-selected', 'true')
    await page.getByRole('tab', { name: 'Overview' }).click()
    await steps.nth(1).getByRole('button', { name: 'Choose folders' }).click()
    await expect(page.getByRole('tab', { name: 'Documents' })).toHaveAttribute('aria-selected', 'true')
    await page.getByRole('tab', { name: 'Overview' }).click()
    await steps.nth(0).getByRole('link', { name: 'Manage' }).click()
    await expect(page).toHaveURL(/#\/library\/databases$/)
    await expect(page.getByRole('tab', { name: 'Databases' })).toHaveAttribute('aria-selected', 'true')
  })
})
