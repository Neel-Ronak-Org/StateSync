/**
 * Acceptance: the Library page and a workspace's Documents tab (docs/V3_PLAN.md
 * Phase 3c slice 5; web/DESIGN.md §6b), against mock-library.ts, which applies the
 * server's upload rules (raw body, Content-Type, X-Filename, 415/422).
 */

import { expect, test, type Page } from '@playwright/test'

import { openApp } from './helpers'
import type { MockApi } from './mock-api'

const PDF = { name: 'Returns policy.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4 fake') }

async function openLibrary(page: Page) {
  await page.getByRole('button', { name: 'Library', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Library', exact: true })).toBeVisible()
}

function seedFolder(name = 'Policies', docs: string[] = []) {
  return (api: MockApi) => {
    const folder = api.library.addFolder(name)
    for (const d of docs) api.library.addDocument(folder.folder_id, d)
  }
}

test.describe('the library page', () => {
  test('is in the rail, has its own address, and explains itself when empty', async ({ page }) => {
    await openApp(page)
    await openLibrary(page)
    await expect(page).toHaveURL(/#\/library$/)
    await expect(page.getByRole('button', { name: 'Library', exact: true })).toHaveAttribute('aria-current', 'page')
    await expect(page.getByRole('button', { name: 'Dashboards', exact: true })).not.toHaveAttribute('aria-current', 'page')
    await expect(page.getByText('Documents you upload once and use in any workspace.')).toBeVisible()
    await expect(page.getByRole('heading', { name: 'No folders yet' })).toBeVisible()
  })

  test('creates a folder and selects it; a duplicate name is refused in the dialog', async ({ page }) => {
    await openApp(page, { hash: '#/library' })
    await page.getByRole('button', { name: 'New folder' }).first().click()
    const dialog = page.getByRole('dialog', { name: 'New folder' })
    await dialog.getByLabel('Name').fill('Policies')
    await dialog.getByRole('button', { name: 'Create' }).click()
    await expect(page.getByTestId('folder-item')).toHaveCount(1)
    await expect(page.getByTestId('folder-item')).toContainText('Policies')
    await expect(page.getByTestId('folder-item')).toContainText('0 documents')
    await expect(page.getByTestId('folder-item')).toHaveAttribute('aria-current', 'true')
    await expect(page.getByRole('heading', { name: 'No documents in this folder' })).toBeVisible()
    await expect(page.getByTestId('upload-dropzone')).toContainText('Drop PDF, text or Markdown files here')

    await page.getByRole('button', { name: 'New folder' }).click()
    await dialog.getByLabel('Name').fill('policies')
    await dialog.getByRole('button', { name: 'Create' }).click()
    await expect(dialog.getByText('A folder with that name already exists.')).toBeVisible()
    await expect(page.getByTestId('folder-item')).toHaveCount(1)
    await dialog.getByRole('button', { name: 'Cancel' }).click()
    await expect(dialog).toHaveCount(0)
  })

  test('renames a folder', async ({ page }) => {
    await openApp(page, { hash: '#/library', seed: seedFolder() })
    await page.getByRole('button', { name: 'Folder actions' }).click()
    await page.getByRole('menuitem', { name: 'Rename folder' }).click()
    const dialog = page.getByRole('dialog', { name: 'Rename folder' })
    await expect(dialog.getByLabel('Name')).toHaveValue('Policies')
    await dialog.getByLabel('Name').fill('Handbooks')
    await dialog.getByRole('button', { name: 'Save' }).click()
    await expect(page.getByTestId('folder-item')).toContainText('Handbooks')
  })

  test('lists documents with pages (PDFs only), size, and status', async ({ page }) => {
    await openApp(page, {
      hash: '#/library',
      seed: (api) => {
        const f = api.library.addFolder('Policies')
        api.library.addDocument(f.folder_id, 'manual.pdf')
        api.library.addDocument(f.folder_id, 'notes.txt')
        api.library.addDocument(f.folder_id, 'scan.pdf', 'failed', 'The PDF has no text to read.')
      },
    })
    const docs = page.getByTestId('library-document')
    await expect(docs).toHaveCount(3)
    const pdf = docs.filter({ hasText: 'manual.pdf' })
    await expect(pdf).toContainText('12 pages')
    await expect(pdf).toContainText('2 KB')
    await expect(pdf).toHaveAttribute('data-status', 'ready')
    await expect(pdf).toContainText('Ready')
    await expect(docs.filter({ hasText: 'notes.txt' })).not.toContainText('page')
    const failed = docs.filter({ hasText: 'scan.pdf' })
    await expect(failed).toHaveAttribute('data-status', 'failed')
    await expect(failed).toContainText('Failed')
    await expect(failed).toContainText('The PDF has no text to read.')
  })
})

test.describe('uploading', () => {
  test('a PDF shows as Processing, then Ready once the server has read it', async ({ page }) => {
    const api = await openApp(page, { hash: '#/library', seed: seedFolder() })
    await page.getByTestId('upload-input').setInputFiles(PDF)
    const doc = page.getByTestId('library-document')
    await expect(doc).toHaveAttribute('data-status', 'processing')
    await expect(doc).toContainText('Processing')
    await expect(doc).toContainText('Returns policy.pdf')
    // The server finishes in the background; the page notices on its next poll (every 3 s).
    api.library.finishProcessing()
    await expect(doc).toHaveAttribute('data-status', 'ready', { timeout: 10_000 })
    await expect(doc).toContainText('Ready')
    await expect(page.getByTestId('folder-item')).toContainText('1 document')
    expect(api.library.uploads).toHaveLength(1)
    expect(api.library.uploads[0]).toMatchObject({
      contentType: 'application/pdf',
      filename: 'Returns policy.pdf',
      bytes: PDF.buffer.length,
    })
  })

  test('a document the server could not read ends as Failed, with the reason', async ({ page }) => {
    const api = await openApp(page, { hash: '#/library', seed: seedFolder() })
    await page.getByTestId('upload-input').setInputFiles(PDF)
    const doc = page.getByTestId('library-document')
    await expect(doc).toHaveAttribute('data-status', 'processing')
    api.library.finishProcessing({ failed: 'The PDF has no text to read.' })
    await expect(doc).toHaveAttribute('data-status', 'failed', { timeout: 10_000 })
    await expect(doc).toContainText('The PDF has no text to read.')
  })

  test('text and Markdown files go with their own content types', async ({ page }) => {
    const api = await openApp(page, { hash: '#/library', seed: seedFolder() })
    await page.getByTestId('upload-input').setInputFiles([
      { name: 'faq.md', mimeType: 'application/octet-stream', buffer: Buffer.from('# FAQ') },
      { name: 'terms.txt', mimeType: 'application/octet-stream', buffer: Buffer.from('Terms') },
    ])
    await expect(page.getByTestId('library-document')).toHaveCount(2)
    expect(api.library.uploads.map((u) => [u.filename, u.contentType])).toEqual([
      ['faq.md', 'text/markdown'],
      ['terms.txt', 'text/plain'],
    ])
  })

  test('an unsupported file is refused in the browser and never sent; the others still upload', async ({ page }) => {
    const api = await openApp(page, { hash: '#/library', seed: seedFolder() })
    await page.getByTestId('upload-input').setInputFiles([
      { name: 'setup.exe', mimeType: 'application/octet-stream', buffer: Buffer.from('MZ') },
      { name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('hello') },
    ])
    await expect(page.getByText('setup.exe: only PDF, text and Markdown files are supported.')).toBeVisible()
    await expect(page.getByTestId('library-document')).toHaveCount(1)
    expect(api.library.uploads.map((u) => u.filename)).toEqual(['notes.txt'])
  })

  test('a file over 20 MB is refused in the browser and never sent', async ({ page }) => {
    const api = await openApp(page, { hash: '#/library', seed: seedFolder() })
    await page.getByTestId('upload-input').setInputFiles({
      name: 'huge.pdf',
      mimeType: 'application/pdf',
      buffer: Buffer.alloc(20 * 1024 * 1024 + 1),
    })
    await expect(page.getByText('huge.pdf is larger than 20 MB.')).toBeVisible()
    expect(api.library.uploads).toHaveLength(0)
  })

  test('a server refusal names the file and does not stop the rest', async ({ page }) => {
    const api = await openApp(page, { hash: '#/library', seed: seedFolder() })
    await page.getByTestId('upload-input').setInputFiles([
      { name: 'locked.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF') },
      { name: 'ok.txt', mimeType: 'text/plain', buffer: Buffer.from('fine') },
    ])
    await expect(page.getByText('locked.pdf: The PDF is password-protected.')).toBeVisible()
    await expect(page.getByTestId('library-document')).toHaveCount(1)
    await expect(page.getByTestId('library-document')).toContainText('ok.txt')
    expect(api.library.uploads).toHaveLength(2)
  })

  test('the same file twice is refused by the server and shown', async ({ page }) => {
    await openApp(page, { hash: '#/library', seed: seedFolder('Policies', ['notes.txt']) })
    await page.getByTestId('upload-input').setInputFiles({ name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('x') })
    await expect(page.getByText('notes.txt: This file is already in the folder.')).toBeVisible()
    await expect(page.getByTestId('library-document')).toHaveCount(1)
  })
})

test.describe('deleting', () => {
  test('a document is deleted after confirming, and not before', async ({ page }) => {
    const api = await openApp(page, { hash: '#/library', seed: seedFolder('Policies', ['manual.pdf', 'notes.txt']) })
    await page.getByRole('button', { name: 'Delete document manual' }).click()
    const dialog = page.getByRole('dialog', { name: 'Delete document' })
    await dialog.getByRole('button', { name: 'Cancel' }).click()
    await expect(page.getByTestId('library-document')).toHaveCount(2)
    expect(api.library.documents.size).toBe(2)

    await page.getByRole('button', { name: 'Delete document manual' }).click()
    await dialog.getByRole('button', { name: 'Delete' }).click()
    await expect(page.getByTestId('library-document')).toHaveCount(1)
    await expect(page.getByTestId('library-document')).toContainText('notes.txt')
    expect(api.library.documents.size).toBe(1)
  })

  test('a folder is deleted after confirming; the confirmation says what goes with it', async ({ page }) => {
    const api = await openApp(page, {
      hash: '#/library',
      seed: (a) => {
        const f = a.library.addFolder('Policies')
        a.library.addDocument(f.folder_id, 'manual.pdf')
        a.library.addDocument(f.folder_id, 'notes.txt')
        a.library.addFolder('Manuals')
        a.workspaces.createReady('Shop', 'olist')
        a.library.workspaceFolders.set('ws-1', [f.folder_id])
      },
    })
    await expect(page.getByTestId('folder-item')).toHaveCount(2)
    await page.getByRole('button', { name: 'Folder actions' }).click()
    await page.getByRole('menuitem', { name: 'Delete folder' }).click()
    const dialog = page.getByRole('dialog', { name: 'Delete folder' })
    await expect(dialog).toContainText('2 documents')
    await expect(dialog).toContainText('Used by 1 workspace')
    await dialog.getByRole('button', { name: 'Delete' }).click()
    await expect(page.getByTestId('folder-item')).toHaveCount(1)
    await expect(page.getByTestId('folder-item')).toContainText('Manuals')
    expect(api.library.documents.size).toBe(0)
  })
})

test.describe('a workspace and its documents', () => {
  const seed = (api: MockApi) => {
    const a = api.library.addFolder('Policies')
    api.library.addDocument(a.folder_id, 'manual.pdf')
    api.library.addDocument(a.folder_id, 'busy.pdf', 'processing')
    api.library.addFolder('Manuals')
    api.workspaces.createReady('Shop', 'olist')
  }

  test('the Documents tab saves the chosen folders', async ({ page }) => {
    const api = await openApp(page, { hash: '#/workspaces/ws-1', seed })
    await page.getByRole('tab', { name: 'Documents' }).click()
    await expect(page.getByText('Questions in this workspace can search the documents in the folders you choose.')).toBeVisible()
    const rows = page.getByTestId('workspace-folder')
    await expect(rows).toHaveCount(2)
    await expect(rows.filter({ hasText: 'Policies' })).toContainText('1 ready')
    const save = page.getByRole('button', { name: 'Save', exact: true })
    await expect(save).toBeDisabled()

    await rows.filter({ hasText: 'Policies' }).getByRole('checkbox').check()
    await expect(save).toBeEnabled()
    await save.click()
    await expect(save).toBeDisabled()
    expect(api.library.folderSaves).toEqual([['fo-1']])

    // Reopening shows what was saved.
    await page.reload()
    await page.getByRole('tab', { name: 'Documents' }).click()
    await expect(rows.filter({ hasText: 'Policies' }).getByRole('checkbox')).toBeChecked()
    await expect(rows.filter({ hasText: 'Manuals' }).getByRole('checkbox')).not.toBeChecked()

    await rows.filter({ hasText: 'Policies' }).getByRole('checkbox').uncheck()
    await save.click()
    await expect(save).toBeDisabled()
    expect(api.library.folderSaves).toEqual([['fo-1'], []])
  })

  test('definitions are found in the chosen documents, each with its sentence and page', async ({ page }) => {
    const api = await openApp(page, { hash: '#/workspaces/ws-1', seed })
    await page.getByRole('tab', { name: 'Knowledge' }).click()
    const find = page.getByRole('button', { name: 'Find in documents' })
    // No folder chosen yet: nothing to read.
    await expect(find).toBeDisabled()
    await expect(page.getByText('Choose library folders with ready documents in the Documents tab')).toBeVisible()

    api.library.workspaceFolders.set('ws-1', ['fo-1'])
    await page.reload()
    await page.getByRole('tab', { name: 'Knowledge' }).click()
    await expect(page.getByText(/written in the 1 document this workspace searches/)).toBeVisible()
    await find.click()
    const summary = page.getByTestId('mine-summary')
    await expect(summary).toContainText('Found 1 definition to review below.')
    await expect(summary).toContainText('2 suggestions were left out because the quoted sentence is not in the document.')

    const row = page.getByTestId('definition-row').filter({ hasText: 'On time' })
    await expect(row.getByTestId('source-badge')).toHaveText('From document')
    await expect(row.getByTestId('definition-citation')).toContainText(
      '“An order is on time when it is delivered on or before the estimated delivery date.”')
    await expect(row.getByTestId('definition-citation')).toContainText('Ops handbook, p. 4')
  })

  test("finding definitions never replaces the owner's own", async ({ page }) => {
    const api = await openApp(page, {
      hash: '#/workspaces/ws-1',
      seed: (a) => {
        seed(a)
        a.library.workspaceFolders.set('ws-1', ['fo-1'])
        a.workspaces.workspaces.get('ws-1')!.definitions.push({
          definition_id: 'def-own', term: 'On time', definition: 'Within 3 days.', source: 'owner', approved: true,
        })
      },
    })
    await page.getByRole('tab', { name: 'Knowledge' }).click()
    await page.getByRole('button', { name: 'Find in documents' }).click()
    await expect(page.getByTestId('mine-summary')).toContainText('No new definitions were found.')
    const row = page.getByTestId('definition-row').filter({ hasText: 'On time' })
    await expect(row).toContainText('Within 3 days.')
    await expect(row.getByTestId('source-badge')).toHaveText('Yours')
    await expect(row.getByTestId('definition-citation')).toHaveCount(0)
    expect(api.workspaces.workspaces.get('ws-1')!.definitions).toHaveLength(1)
  })

  test('a failed save says so and keeps the choice', async ({ page }) => {
    const api = await openApp(page, { hash: '#/workspaces/ws-1', seed })
    await page.getByRole('tab', { name: 'Documents' }).click()
    await expect(page.getByTestId('workspace-folder')).toHaveCount(2)
    api.failNext('/workspaces/ws-1/folders', 503, 'The library is unavailable.')
    await page.getByTestId('workspace-folder').first().getByRole('checkbox').check()
    await page.getByRole('button', { name: 'Save', exact: true }).click()
    await expect(page.getByRole('alert')).toContainText('The library is unavailable.')
    await expect(page.getByTestId('workspace-folder').first().getByRole('checkbox')).toBeChecked()
    await expect(page.getByRole('button', { name: 'Save', exact: true })).toBeEnabled()
  })

  test('with an empty library it points to the library', async ({ page }) => {
    await openApp(page, { hash: '#/workspaces/ws-1', seed: (api) => void api.workspaces.createReady('Shop', 'olist') })
    await page.getByRole('tab', { name: 'Documents' }).click()
    await expect(page.getByText('Your library is empty.')).toBeVisible()
    await page.getByRole('link', { name: 'Open library' }).click()
    await expect(page).toHaveURL(/#\/library$/)
    await expect(page.getByRole('button', { name: 'Library', exact: true })).toHaveAttribute('aria-current', 'page')
  })
})
