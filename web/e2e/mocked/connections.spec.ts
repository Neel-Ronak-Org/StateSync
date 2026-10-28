/**
 * Acceptance: connecting your own Postgres (Library, Databases tab) and using it in a
 * workspace (docs/V3_PLAN.md Phase 3c slice 6a; web/DESIGN.md §6b and §6a), against
 * mock-connections.ts, which applies the server's rules (no password in any response,
 * a 422 sentence for every refusal, a connection with a schema is a dataset).
 */

import { expect, test, type Locator } from '@playwright/test'

import { openApp } from './helpers'
import type { MockApi } from './mock-api'
import { CONNECTION_LIMIT } from './mock-connections'

const PASSWORD = 'hunter2-not-for-logs'

async function fillDatabase(dialog: Locator, values: { name?: string; host?: string; dbname?: string; user?: string; password?: string } = {}) {
  await dialog.getByLabel('Name').fill(values.name ?? 'Shop replica')
  await dialog.getByLabel('Host').fill(values.host ?? 'db.example.com')
  await dialog.getByLabel('Database').fill(values.dbname ?? 'shop')
  await dialog.getByLabel('User').fill(values.user ?? 'statesync_reader')
  await dialog.getByLabel('Password').fill(values.password ?? PASSWORD)
}

const seedOne = (api: MockApi) => void api.connections.add('Shop replica')

test.describe('the library tabs', () => {
  test('open on Documents; Databases is a second tab and hides the folder button', async ({ page }) => {
    await openApp(page, { hash: '#/library' })
    const tabs = page.getByRole('tablist', { name: 'Data source sections' })
    await expect(tabs.getByRole('tab', { name: 'Documents' })).toHaveAttribute('aria-selected', 'true')
    await expect(page.getByRole('heading', { name: 'No folders yet' })).toBeVisible()
    await tabs.getByRole('tab', { name: 'Databases' }).click()
    await expect(page.getByRole('heading', { name: 'No databases yet' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'New folder' })).toHaveCount(0)
    await tabs.getByRole('tab', { name: 'Documents' }).click()
    await expect(page.getByRole('heading', { name: 'No folders yet' })).toBeVisible()
  })
})

test.describe('the databases tab', () => {
  test('explains itself when empty, and shows the read-only login recipe as text', async ({ page }) => {
    await openApp(page, { hash: '#/library' })
    await page.getByRole('tab', { name: 'Databases' }).click()
    await expect(page.getByRole('heading', { name: 'No databases yet' })).toBeVisible()
    await expect(page.getByText('Connect a Postgres database with a read-only login. StateSync only ever reads it.')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Add database' })).toBeVisible()

    const summary = page.getByText('How to create a read-only login')
    const sql = page.locator('details pre')
    await expect(sql).toBeHidden()
    await summary.click()
    await expect(sql).toBeVisible()
    await expect(sql).toContainText("CREATE ROLE statesync_reader LOGIN PASSWORD 'choose-a-password';")
    await expect(sql).toContainText('GRANT CONNECT ON DATABASE your_db TO statesync_reader;')
    await expect(sql).toContainText('GRANT USAGE ON SCHEMA your_schema TO statesync_reader;')
    await expect(sql).toContainText('GRANT SELECT ON ALL TABLES IN SCHEMA your_schema TO statesync_reader;')
  })

  test('adds a database: the password is sent once and is nowhere on the page afterwards', async ({ page }) => {
    const api = await openApp(page, { hash: '#/library' })
    await page.getByRole('tab', { name: 'Databases' }).click()
    await page.getByRole('button', { name: 'Add database' }).click()
    const dialog = page.getByRole('dialog', { name: 'Add database' })

    // Defaults and the hint under Host.
    await expect(dialog.getByLabel('Port')).toHaveValue('5432')
    await expect(dialog.getByRole('combobox', { name: 'SSL' })).toHaveText('prefer')
    await expect(dialog.getByText('For a database on this computer, use host.docker.internal.')).toBeVisible()
    const password = dialog.getByLabel('Password')
    await expect(password).toHaveAttribute('type', 'password')
    await expect(password).toHaveAttribute('autocomplete', 'new-password')
    await expect(dialog.getByRole('button', { name: 'Connect' })).toBeDisabled()

    await fillDatabase(dialog)
    await dialog.getByRole('combobox', { name: 'SSL' }).click()
    await page.getByRole('option', { name: 'require', exact: true }).click()
    await dialog.getByRole('button', { name: 'Connect' }).click()

    await expect(dialog).toHaveCount(0)
    await expect(page.getByRole('region', { name: /Notifications/ }).getByText('Connected', { exact: true })).toBeVisible()
    const item = page.getByTestId('connection-item')
    await expect(item).toHaveCount(1)
    await expect(item).toHaveAttribute('data-status', 'ok')
    await expect(item).toContainText('Shop replica')
    await expect(item).toContainText('statesync_reader@db.example.com:5432/shop')
    await expect(item).toContainText('Ready')
    await expect(item).toContainText('public') // one readable schema: the server chose it

    expect(api.connections.creates).toHaveLength(1)
    expect(api.connections.creates[0]).toEqual({
      name: 'Shop replica',
      host: 'db.example.com',
      port: 5432,
      dbname: 'shop',
      username: 'statesync_reader',
      password: PASSWORD,
      sslmode: 'require',
    })

    // The password is not in the document, in any input, or in browser storage.
    expect(await page.content()).not.toContain(PASSWORD)
    await expect(page.locator('input[type="password"]')).toHaveCount(0)
    // page.content() sees attributes only; also look at every input's live value and the visible text.
    const inDom = await page.evaluate(
      (secret) => [...document.querySelectorAll('input, textarea')].some((el) => (el as HTMLInputElement).value.includes(secret)) || document.body.innerText.includes(secret),
      PASSWORD,
    )
    expect(inDom).toBe(false)
    const stored = await page.evaluate(() => JSON.stringify([{ ...localStorage }, { ...sessionStorage }]))
    expect(stored).not.toContain(PASSWORD)
    // Nor when the dialog is opened again.
    await page.getByRole('button', { name: 'Add database' }).click()
    await expect(page.getByRole('dialog', { name: 'Add database' }).getByLabel('Password')).toHaveValue('')
  })

  test('a cancelled dialog forgets what was typed, the password included', async ({ page }) => {
    await openApp(page, { hash: '#/library' })
    await page.getByRole('tab', { name: 'Databases' }).click()
    await page.getByRole('button', { name: 'Add database' }).click()
    const dialog = page.getByRole('dialog', { name: 'Add database' })
    await fillDatabase(dialog)
    await dialog.getByRole('button', { name: 'Cancel' }).click()
    await expect(dialog).toHaveCount(0)
    expect(await page.content()).not.toContain(PASSWORD)
    await page.getByRole('button', { name: 'Add database' }).click()
    const again = page.getByRole('dialog', { name: 'Add database' })
    await expect(again.getByLabel('Password')).toHaveValue('')
    await expect(again.getByLabel('Name')).toHaveValue('')
  })

  test('a refusal is shown in the dialog, which stays open with what was typed', async ({ page }) => {
    const api = await openApp(page, { hash: '#/library' })
    await page.getByRole('tab', { name: 'Databases' }).click()
    await page.getByRole('button', { name: 'Add database' }).click()
    const dialog = page.getByRole('dialog', { name: 'Add database' })

    await fillDatabase(dialog, { user: 'shop_writer' })
    await dialog.getByRole('button', { name: 'Connect' }).click()
    await expect(dialog.getByRole('alert')).toHaveText(
      'This login can change data (for example in shop.orders). Use a login that can only read.',
    )
    await expect(dialog.getByLabel('Name')).toHaveValue('Shop replica')
    await expect(dialog.getByLabel('User')).toHaveValue('shop_writer')
    await expect(page.getByTestId('connection-item')).toHaveCount(0)

    // Each refusal has its own sentence; fixing the field and retrying works.
    await dialog.getByLabel('User').fill('statesync_reader')
    await dialog.getByLabel('Host').fill('postgres')
    await dialog.getByRole('button', { name: 'Connect' }).click()
    await expect(dialog.getByRole('alert')).toContainText('postgres is part of StateSync itself.')
    await dialog.getByLabel('Host').fill('db.example.com')
    await dialog.getByLabel('Password').fill('wrong-password')
    await dialog.getByRole('button', { name: 'Connect' }).click()
    await expect(dialog.getByRole('alert')).toContainText('Could not connect:')
    await dialog.getByLabel('Password').fill(PASSWORD)
    await dialog.getByRole('button', { name: 'Connect' }).click()
    await expect(dialog).toHaveCount(0)
    await expect(page.getByTestId('connection-item')).toHaveCount(1)
    expect(api.connections.creates).toHaveLength(4)
  })

  test('a duplicate name and the connection limit are refused in the dialog', async ({ page }) => {
    await openApp(page, {
      hash: '#/library',
      seed: (api) => {
        api.connections.add('Shop replica')
        for (let i = 1; i < CONNECTION_LIMIT; i += 1) api.connections.add(`Extra ${i}`)
      },
    })
    await page.getByRole('tab', { name: 'Databases' }).click()
    await page.getByRole('button', { name: 'Add database' }).click()
    const dialog = page.getByRole('dialog', { name: 'Add database' })
    await fillDatabase(dialog)
    await dialog.getByRole('button', { name: 'Connect' }).click()
    await expect(dialog.getByRole('alert')).toContainText('You already have')
  })

  test('shows a busy state while checking, and cannot be submitted twice', async ({ page }) => {
    const api = await openApp(page, { hash: '#/library' })
    await page.getByRole('tab', { name: 'Databases' }).click()
    await page.getByRole('button', { name: 'Add database' }).click()
    const dialog = page.getByRole('dialog', { name: 'Add database' })
    await fillDatabase(dialog)
    let release: () => void = () => undefined
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    await page.route('**/library/connections', async (route) => {
      if (route.request().method() === 'POST') await gate
      await route.fallback()
    })
    await dialog.getByRole('button', { name: 'Connect' }).click()
    await expect(dialog.getByRole('button', { name: 'Checking…' })).toBeDisabled()
    release()
    await expect(dialog).toHaveCount(0)
    expect(api.connections.creates).toHaveLength(1)
  })

  test('several readable schemas: the new connection asks which one, and remembers the choice', async ({ page }) => {
    const api = await openApp(page, { hash: '#/library' })
    api.connections.schemasByDb['warehouse'] = ['marketing', 'sales']
    await page.getByRole('tab', { name: 'Databases' }).click()
    await page.getByRole('button', { name: 'Add database' }).click()
    const dialog = page.getByRole('dialog', { name: 'Add database' })
    await fillDatabase(dialog, { name: 'Warehouse', dbname: 'warehouse' })
    await dialog.getByRole('button', { name: 'Connect' }).click()
    await expect(dialog).toHaveCount(0)

    const item = page.getByTestId('connection-item')
    const select = item.getByRole('combobox', { name: 'Schema for Warehouse' })
    await expect(select).toBeVisible()
    await select.click()
    await expect(page.getByRole('option')).toHaveText(['marketing', 'sales'])
    await page.getByRole('option', { name: 'sales', exact: true }).click()

    await expect(item.getByRole('combobox')).toHaveCount(0)
    await expect(item).toContainText('Schema sales')
    expect(api.connections.schemaPuts).toEqual([{ id: [...api.connections.connections.keys()][0], schema_name: 'sales' }])
    expect([...api.connections.connections.values()][0].schema_name).toBe('sales')
  })

  test('a connection with no known schemas asks to be tested again, which lists them', async ({ page }) => {
    const api = await openApp(page, {
      hash: '#/library',
      seed: (a) => {
        a.connections.add('Warehouse', { dbname: 'warehouse', schema: null })
        a.connections.schemasByDb['warehouse'] = ['marketing', 'sales']
      },
    })
    await page.getByRole('tab', { name: 'Databases' }).click()
    const item = page.getByTestId('connection-item')
    await expect(item.getByRole('combobox')).toHaveCount(0)
    await expect(item).toContainText('Test again to list the schemas')
    await item.getByRole('button', { name: 'Test again' }).click()
    await expect(item.getByRole('combobox', { name: 'Schema for Warehouse' })).toBeVisible()
    expect(api.connections.tests).toHaveLength(1)
  })

  test('a schema the server refuses is explained next to the connection', async ({ page }) => {
    const api = await openApp(page, {
      hash: '#/library',
      seed: (a) => {
        a.connections.add('Warehouse', { dbname: 'warehouse', schema: null })
        a.connections.schemasByDb['warehouse'] = ['marketing', 'sales']
      },
    })
    await page.getByRole('tab', { name: 'Databases' }).click()
    const item = page.getByTestId('connection-item')
    await item.getByRole('button', { name: 'Test again' }).click()
    await item.getByRole('combobox', { name: 'Schema for Warehouse' }).click()
    // The login loses access to the schema between the test and the choice.
    api.connections.schemasByDb['warehouse'] = ['marketing']
    await page.getByRole('option', { name: 'sales', exact: true }).click()
    await expect(item.getByRole('alert')).toHaveText('The schema sales is not readable with this login.')
    await expect(item.getByRole('combobox', { name: 'Schema for Warehouse' })).toBeVisible()
  })

  test('Test again turns a connection Failed with its reason, and back to Ready', async ({ page }) => {
    const api = await openApp(page, { hash: '#/library', seed: seedOne })
    const id = [...api.connections.connections.keys()][0]
    await page.getByRole('tab', { name: 'Databases' }).click()
    const item = page.getByTestId('connection-item')
    await expect(item).toHaveAttribute('data-status', 'ok')

    api.connections.failNextTest.set(id, 'Could not connect: connection refused (is the server running?).')
    await item.getByRole('button', { name: 'Test again' }).click()
    await expect(item).toHaveAttribute('data-status', 'failed')
    await expect(item).toContainText('Failed')
    await expect(item).toContainText('Could not connect: connection refused (is the server running?).')

    await item.getByRole('button', { name: 'Test again' }).click()
    await expect(item).toHaveAttribute('data-status', 'ok')
    await expect(item).toContainText('Ready')
    await expect(item).not.toContainText('connection refused')
    expect(api.connections.tests).toEqual([id, id])
  })

  test('deleting is confirmed; a connection in use is refused in the dialog, then allowed', async ({ page }) => {
    const api = await openApp(page, {
      hash: '#/library',
      seed: (a) => {
        const c = a.connections.add('Shop replica')
        a.workspaces.createReady('Shop analytics', c.connection_id)
      },
    })
    await page.getByRole('tab', { name: 'Databases' }).click()
    const item = page.getByTestId('connection-item')
    await expect(item).toContainText('Used by 1 workspace')

    await item.getByRole('button', { name: 'Delete connection Shop replica' }).click()
    const dialog = page.getByRole('dialog', { name: 'Delete connection' })
    await expect(dialog).toContainText('Shop replica')
    await dialog.getByRole('button', { name: 'Cancel' }).click()
    await expect(dialog).toHaveCount(0)
    expect(api.connections.deletes).toHaveLength(0)

    await item.getByRole('button', { name: 'Delete connection Shop replica' }).click()
    await dialog.getByRole('button', { name: 'Delete', exact: true }).click()
    await expect(dialog.getByRole('alert')).toHaveText('1 workspace uses this connection. Delete it first.')
    await expect(item).toHaveCount(1)

    // The owner deletes the workspace, then the same dialog succeeds.
    api.workspaces.workspaces.clear()
    await dialog.getByRole('button', { name: 'Delete', exact: true }).click()
    await expect(dialog).toHaveCount(0)
    await expect(page.getByTestId('connection-item')).toHaveCount(0)
    await expect(page.getByRole('heading', { name: 'No databases yet' })).toBeVisible()
    expect(api.connections.deletes).toHaveLength(1)
  })

  test('warnings from a check show muted next to a Ready connection', async ({ page }) => {
    const api = await openApp(page, { hash: '#/library' })
    await page.getByRole('tab', { name: 'Databases' }).click()
    api.connections.nextWarnings = ['This connection is not encrypted (sslmode prefer).']
    await page.getByRole('button', { name: 'Add database' }).click()
    const dialog = page.getByRole('dialog', { name: 'Add database' })
    await fillDatabase(dialog)
    await dialog.getByRole('button', { name: 'Connect' }).click()
    await expect(page.getByTestId('connection-item')).toContainText('This connection is not encrypted (sslmode prefer).')
    expect(api.connections.creates).toHaveLength(1)
  })

  test('a server without an encryption key says how to set it up, and hides Add database', async ({ page }) => {
    await openApp(page, { hash: '#/library', seed: (a) => void (a.connections.available = false) })
    await page.getByRole('tab', { name: 'Databases' }).click()
    await expect(
      page.getByText(
        'Connecting your own databases is not set up on this server. Run scripts/make_connection_key.py and restart the stack.',
      ),
    ).toBeVisible()
    await expect(page.getByRole('button', { name: 'Add database' })).toHaveCount(0)
    await expect(page.getByRole('heading', { name: 'No databases yet' })).toHaveCount(0)
  })

  test('a failed load offers a retry', async ({ page }) => {
    const api = await openApp(page, { hash: '#/library' })
    api.failNext('/library/connections', 403, 'The connections could not be read.')
    await page.getByRole('tab', { name: 'Databases' }).click()
    await expect(page.getByText('The connections could not be read.')).toBeVisible()
    await page.getByRole('button', { name: 'Retry' }).click()
    await expect(page.getByRole('heading', { name: 'No databases yet' })).toBeVisible()
  })
})

test.describe('using a connected database in a workspace', () => {
  test('the New workspace dialog lists your databases with a schema, and the workspace is created on its id', async ({ page }) => {
    const api = await openApp(page, {
      hash: '#/workspaces',
      noWorkspace: true,
      seed: (a) => {
        a.connections.add('Shop replica')
        a.connections.add('Half set up', { dbname: 'warehouse', schema: null })
      },
    })
    const id = [...api.connections.connections.keys()][0]
    await page.getByRole('button', { name: 'New workspace' }).click()
    const dialog = page.getByRole('dialog', { name: 'New workspace' })
    await dialog.getByLabel('Name').fill('Shop analytics')
    await dialog.getByRole('combobox', { name: 'Database' }).click()

    // Only connected databases with a schema are offered.
    await expect(page.getByRole('option')).toHaveText(['Shop replica'])
    await expect(page.getByRole('option', { name: 'Half set up' })).toHaveCount(0)

    await page.getByRole('option', { name: 'Shop replica' }).click()
    await expect(dialog.getByRole('combobox', { name: 'Database' })).toHaveText('Shop replica')
    await dialog.getByRole('button', { name: 'Create' }).click()
    await expect(page.getByRole('heading', { name: 'Shop analytics', exact: true })).toBeVisible()
    expect(api.workspaces.workspaces.get('ws-1')?.target_schema).toBe(id)

    // The connection now says it is in use.
    await page.getByRole('button', { name: 'Data sources', exact: true }).click()
    await page.getByRole('tab', { name: 'Databases' }).click()
    await expect(page.getByTestId('connection-item').first()).toContainText('Used by 1 workspace')
  })

  test('a workspace card shows the connection name, never its id', async ({ page }) => {
    const api = await openApp(page, {
      hash: '#/workspaces',
      seed: (a) => {
        const c = a.connections.add('Shop replica')
        a.workspaces.createReady('Shop analytics', c.connection_id)
        a.workspaces.createReady('Furniture', 'olist')
      },
    })
    const id = [...api.connections.connections.keys()][0]
    expect(id).toMatch(/^cn_[0-9a-f]{16}$/)
    const card = page.getByTestId('workspace-card').filter({ hasText: 'Shop analytics' })
    await expect(card).toContainText('Shop replica')
    await expect(card).not.toContainText(id)
    // Sample datasets are shown as they were.
    await expect(page.getByTestId('workspace-card').filter({ hasText: 'Furniture' })).toContainText('olist')
    expect(await page.content()).not.toContain(`>${id}<`)
  })

  test('a deleted connection never leaves a raw id on the card', async ({ page }) => {
    await openApp(page, {
      hash: '#/workspaces',
      seed: (a) => {
        a.workspaces.createReady('Orphan', 'cn_00000000000000ff')
      },
    })
    const card = page.getByTestId('workspace-card')
    await expect(card).toContainText('Your database')
    await expect(card).not.toContainText('cn_00000000000000ff')
  })

  test('asking in the workspace names the database, not its id', async ({ page }) => {
    await openApp(page, {
      hash: '#/workspaces',
      seed: (a) => {
        const c = a.connections.add('Shop replica')
        a.workspaces.createReady('Shop analytics', c.connection_id)
      },
    })
    await page.getByTestId('workspace-card').click()
    await page.getByRole('button', { name: 'Ask in this workspace' }).click()
    await expect(page.getByText('Questions are answered from the')).toContainText('Shop replica')
    await expect(page.getByText('Questions are answered from the')).not.toContainText('cn_')
  })
})
