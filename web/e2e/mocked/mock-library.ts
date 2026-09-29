/**
 * The /library part of the mocked orchestrator, plus a workspace's folder links
 * (docs/V3_PLAN.md Phase 3c slice 5). Mirrors the server's rules: raw-file uploads
 * (Content-Type + X-Filename, not multipart), 415 for other types, 413 over 20 MB,
 * 422 for a duplicate file or a parser refusal, and a document that is
 * `processing` until the test calls finishProcessing() (the server's background
 * job), after which the page's next poll sees it ready or failed.
 */

import type { Route } from '@playwright/test'

type Json = Record<string, unknown>
type Reply = (route: Route, status: number, body: unknown) => Promise<void>

const ALLOWED_TYPES = ['application/pdf', 'text/plain', 'text/markdown']
const MAX_BYTES = 20 * 1024 * 1024

export interface MockFolder {
  folder_id: string
  name: string
  created_at: string
}

export interface MockDocument {
  document_id: string
  folder_id: string
  title: string
  filename: string
  content_type: string
  pages: number | null
  bytes: number
  status: 'processing' | 'ready' | 'failed'
  error: string | null
  chunk_count: number
  created_at: string
}

export interface UploadRecord {
  path: string
  contentType: string | undefined
  filename: string | undefined
  bytes: number
}

export class LibraryMock {
  readonly folders = new Map<string, MockFolder>()
  readonly documents = new Map<string, MockDocument>()
  readonly workspaceFolders = new Map<string, string[]>()
  /** Every upload request that reached the server, so a test can prove a refused file was never sent. */
  readonly uploads: UploadRecord[] = []
  /** Every PUT /workspaces/{id}/folders body. */
  readonly folderSaves: string[][] = []
  private clock = Date.parse('2026-09-01T09:00:00Z')
  private seq = 0

  private now(): string {
    this.clock += 1000
    return new Date(this.clock).toISOString()
  }

  addFolder(name: string): MockFolder {
    this.seq += 1
    const folder = { folder_id: `fo-${this.seq}`, name, created_at: this.now() }
    this.folders.set(folder.folder_id, folder)
    return folder
  }

  addDocument(folderId: string, filename: string, status: MockDocument['status'] = 'ready', error: string | null = null): MockDocument {
    this.seq += 1
    const pdf = filename.toLowerCase().endsWith('.pdf')
    const doc: MockDocument = {
      document_id: `do-${this.seq}`,
      folder_id: folderId,
      title: filename.replace(/\.[^.]+$/, ''),
      filename,
      content_type: pdf ? 'application/pdf' : 'text/plain',
      pages: pdf ? 12 : 1, // as the server: a text file is one page
      bytes: 2048,
      status,
      error,
      chunk_count: status === 'ready' ? 8 : 0,
      created_at: this.now(),
    }
    this.documents.set(doc.document_id, doc)
    return doc
  }

  /** The background job ends: every processing document becomes ready, or failed with a reason. */
  finishProcessing(outcome: { failed: string } | 'ready' = 'ready'): void {
    for (const d of this.documents.values()) {
      if (d.status !== 'processing') continue
      if (outcome === 'ready') {
        d.status = 'ready'
        d.chunk_count = 8
      } else {
        d.status = 'failed'
        d.error = outcome.failed
      }
    }
  }

  private docsIn(folderId: string): MockDocument[] {
    return [...this.documents.values()].filter((d) => d.folder_id === folderId)
  }

  private folderView(f: MockFolder) {
    const docs = this.docsIn(f.folder_id)
    return {
      ...f,
      document_count: docs.length,
      ready_count: docs.filter((d) => d.status === 'ready').length,
      workspace_ids: [...this.workspaceFolders.entries()].filter(([, ids]) => ids.includes(f.folder_id)).map(([id]) => id),
    }
  }

  private docView(d: MockDocument) {
    return { ...d }
  }

  private duplicateName(name: string, exceptId?: string): boolean {
    return [...this.folders.values()].some((f) => f.folder_id !== exceptId && f.name.toLowerCase() === name.toLowerCase())
  }

  /** GET/PUT /workspaces/{id}/folders. `workspaceExists` comes from the workspace mock. */
  async workspaceFoldersRoute(route: Route, method: string, workspaceId: string, body: Json, reply: Reply, workspaceExists: boolean) {
    if (!workspaceExists) return reply(route, 404, { detail: 'No such workspace.' })
    if (method === 'GET') return reply(route, 200, { folder_ids: this.workspaceFolders.get(workspaceId) ?? [] })
    if (method === 'PUT') {
      const ids = Array.isArray(body.folder_ids) ? (body.folder_ids as unknown[]).map(String) : null
      if (!ids) return reply(route, 422, { detail: 'folder_ids must be a list.' })
      if (ids.some((id) => !this.folders.has(id))) return reply(route, 422, { detail: 'Unknown folder.' })
      this.folderSaves.push(ids)
      this.workspaceFolders.set(workspaceId, ids)
      return reply(route, 200, { folder_ids: ids })
    }
    return reply(route, 405, { detail: 'Method Not Allowed' })
  }

  async handle(
    route: Route,
    method: string,
    path: string,
    search: URLSearchParams,
    body: Json,
    reply: Reply,
  ): Promise<void> {
    const request = route.request()
    const parts = path.split('/').filter(Boolean) // ['library', 'folders'|'documents', id?, 'documents'?]
    const [, kind, id, sub] = parts

    if (kind === 'folders' && !id) {
      if (method === 'GET') return reply(route, 200, { folders: [...this.folders.values()].map((f) => this.folderView(f)) })
      if (method === 'POST') {
        const name = String(body.name ?? '').trim()
        if (!name) return reply(route, 422, { detail: 'A folder needs a name.' })
        if (this.duplicateName(name)) return reply(route, 422, { detail: 'A folder with that name already exists.' })
        return reply(route, 201, { folder: this.folderView(this.addFolder(name)) })
      }
    }

    if (kind === 'folders' && id && !sub) {
      const folder = this.folders.get(id)
      if (!folder) return reply(route, 404, { detail: 'No such folder.' })
      if (method === 'PATCH') {
        const name = String(body.name ?? '').trim()
        if (!name) return reply(route, 422, { detail: 'A folder needs a name.' })
        if (this.duplicateName(name, id)) return reply(route, 422, { detail: 'A folder with that name already exists.' })
        folder.name = name
        return reply(route, 200, { folder: this.folderView(folder) })
      }
      if (method === 'DELETE') {
        this.folders.delete(id)
        for (const d of this.docsIn(id)) this.documents.delete(d.document_id)
        for (const [ws, ids] of this.workspaceFolders) this.workspaceFolders.set(ws, ids.filter((x) => x !== id))
        return reply(route, 200, { deleted: 1 })
      }
    }

    if (kind === 'folders' && id && sub === 'documents' && method === 'POST') {
      const headers = request.headers()
      const contentType = headers['content-type']?.split(';')[0].trim()
      const rawName = headers['x-filename']
      const size = request.postDataBuffer()?.length ?? 0
      this.uploads.push({
        path,
        contentType,
        filename: rawName === undefined ? undefined : decodeURIComponent(rawName),
        bytes: size,
      })
      const folder = this.folders.get(id)
      if (!folder) return reply(route, 404, { detail: 'No such folder.' })
      if (!contentType || !ALLOWED_TYPES.includes(contentType)) {
        return reply(route, 415, { detail: 'Only PDF, text and Markdown files are supported.' })
      }
      if (!rawName) return reply(route, 422, { detail: 'The X-Filename header is required.' })
      const filename = decodeURIComponent(rawName)
      if (size > MAX_BYTES) return reply(route, 413, { detail: 'The file is larger than 20 MB.' })
      if (size === 0) return reply(route, 422, { detail: 'The file is empty.' })
      if (filename.toLowerCase().startsWith('locked')) return reply(route, 422, { detail: 'The PDF is password-protected.' })
      if (this.docsIn(id).some((d) => d.filename === filename)) {
        return reply(route, 422, { detail: 'This file is already in the folder.' })
      }
      const doc = this.addDocument(id, filename, 'processing')
      doc.bytes = size
      doc.content_type = contentType
      return reply(route, 202, { document: this.docView(doc) })
    }

    if (kind === 'documents' && !id && method === 'GET') {
      const folderId = search.get('folder_id') ?? ''
      if (!this.folders.has(folderId)) return reply(route, 404, { detail: 'No such folder.' })
      const docs = this.docsIn(folderId)
      return reply(route, 200, { documents: docs.map((d) => this.docView(d)).reverse() })
    }

    if (kind === 'documents' && id && method === 'DELETE') {
      if (!this.documents.delete(id)) return reply(route, 404, { detail: 'No such document.' })
      return reply(route, 200, { deleted: 1 })
    }

    return reply(route, 404, { detail: 'Not Found' })
  }
}
