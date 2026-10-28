/**
 * The /workspaces part of the mocked orchestrator (docs/V3_PLAN.md Phase 3).
 *
 * Mirrors orchestrator/workspaces_api.py and mcp/tools/workspace_tools.py:
 * owner text is stored as "owner" and never replaced by a draft; any edit
 * un-approves; one draft at a time (409); a thumbs-up adds an approved example.
 *
 * Background jobs advance one step per status poll, so a test sees "running"
 * at least once and then the result, without timers.
 */

import type { Route } from '@playwright/test'

type Json = Record<string, unknown>
type Reply = (route: Route, status: number, body: unknown) => Promise<void>

export type SetupState = 'created' | 'scanned' | 'drafted' | 'approved' | 'tested'

export interface CatalogEntry {
  table_name: string
  column_name: string
  data_type: string
  row_estimate: number | null
  sample_values: string[]
  description: string | null
  description_source: 'ai' | 'owner' | null
  approved: boolean
}

export interface Definition {
  definition_id: string
  term: string
  definition: string
  source: 'ai' | 'owner' | 'document'
  approved: boolean
  citation?: { document_id: string; chunk_id: string; page: number | null; title: string; quote: string } | null
}

export interface SetupJob {
  job_id: string
  kind: 'draft'
  workspace_id: string
  state: 'running' | 'done' | 'failed'
  done: number
  total: number
  error: string | null
  run_id: string | null
  started_at: string
  finished_at: string | null
}


export interface MockExample {
  question: string
  sql: string
}

export interface MockWorkspace {
  workspace_id: string
  name: string
  target_schema: string
  business_context: string
  context_version: number
  setup_state: SetupState
  setup_score: number | null
  setup_total: number | null
  created_at: string
  updated_at: string
  catalog: CatalogEntry[]
  definitions: Definition[]
  examples: MockExample[]
}

const AT = '2026-09-28T10:00:00Z'

function entry(table: string, column: string, type: string, samples: string[] = []): CatalogEntry {
  return {
    table_name: table,
    column_name: column,
    data_type: type,
    row_estimate: column === '' ? 1000 : null,
    sample_values: samples,
    description: null,
    description_source: null,
    approved: false,
  }
}

function sampleCatalog(): CatalogEntry[] {
  return [
    entry('orders', '', 'table'),
    entry('orders', 'order_id', 'text', ['e481f51c', '53cdb2fc']),
    entry('orders', 'order_status', 'text', ['delivered', 'shipped', 'canceled']),
    entry('orders', 'order_purchase_timestamp', 'timestamp without time zone'),
    entry('customers', '', 'table'),
    entry('customers', 'customer_id', 'text', ['06b8999e']),
    entry('customers', 'customer_state', 'text', ['SP', 'RJ', 'MG']),
  ]
}

function aiDescription(e: CatalogEntry): string {
  return e.column_name === '' ? `AI: one row per ${e.table_name.replace(/s$/, '')}.` : `AI: the ${e.column_name.replace(/_/g, ' ')}.`
}

export class WorkspaceMock {
  readonly workspaces = new Map<string, MockWorkspace>()
  readonly calls: string[] = []
  /** When set, the next draft job fails with this message. */
  failNextDraft: string | null = null
  /** Set by MockApi: true for a connection id that is a usable dataset (a connected database with a schema). */
  extraDataset: (schema: string) => boolean = () => false
  private jobs = new Map<string, SetupJob>()
  private seq = 0

  create(name: string, targetSchema: string, businessContext = ''): MockWorkspace {
    this.seq += 1
    const ws: MockWorkspace = {
      workspace_id: `ws-${this.seq}`,
      name,
      target_schema: targetSchema,
      business_context: businessContext,
      context_version: businessContext ? 1 : 0,
      setup_state: 'created',
      setup_score: null,
      setup_total: null,
      created_at: AT,
      updated_at: AT,
      catalog: [],
      definitions: [],
      examples: [],
    }
    this.workspaces.set(ws.workspace_id, ws)
    return ws
  }

  /** A workspace already through the wizard, for tests that start after setup. */
  createReady(name: string, targetSchema: string, examples: MockExample[] = []): MockWorkspace {
    const ws = this.create(name, targetSchema, 'A test business.')
    ws.catalog = sampleCatalog().map((e) => ({ ...e, description: aiDescription(e), description_source: 'ai', approved: true }))
    ws.setup_state = 'tested'
    ws.setup_score = 9
    ws.setup_total = 10
    ws.examples = examples
    return ws
  }

  view(ws: MockWorkspace): Json {
    return {
      workspace_id: ws.workspace_id,
      name: ws.name,
      target_schema: ws.target_schema,
      business_context: ws.business_context,
      context_version: ws.context_version,
      setup_state: ws.setup_state,
      setup_score: ws.setup_score,
      setup_total: ws.setup_total,
      created_at: ws.created_at,
      updated_at: ws.updated_at,
      counts: {
        tables: ws.catalog.filter((c) => c.column_name === '').length,
        approved: ws.catalog.filter((c) => c.approved).length,
        definitions: ws.definitions.length,
        examples: ws.examples.length,
      },
    }
  }

  static normalize(q: string): string {
    return q.toLowerCase().replace(/['’]/g, '').match(/[a-z0-9]+/g)?.join(' ') ?? ''
  }

  /** The exact approved example for a question, as the fast path matches it. */
  exactExample(ws: MockWorkspace, question: string): { question: string; sql: string } | null {
    const norm = WorkspaceMock.normalize
    return ws.examples.find((e) => norm(e.question) === norm(question)) ?? null
  }

  private activeJob(): SetupJob | undefined {
    return [...this.jobs.values()].find((j) => j.state === 'running')
  }

  private latestJob(workspaceId: string, kind: SetupJob['kind']): SetupJob | null {
    const mine = [...this.jobs.values()].filter((j) => j.workspace_id === workspaceId && j.kind === kind)
    return mine.length ? mine[mine.length - 1] : null
  }

  private newJob(kind: SetupJob['kind'], workspaceId: string, total: number): SetupJob {
    this.seq += 1
    const job: SetupJob = {
      job_id: `job-${this.seq}`,
      kind,
      workspace_id: workspaceId,
      state: 'running',
      done: 0,
      total,
      error: null,
      run_id: null,
      started_at: AT,
      finished_at: null,
    }
    this.jobs.set(job.job_id, job)
    return job
  }

  private advanceDraft(ws: MockWorkspace, job: SetupJob): void {
    if (job.state !== 'running') return
    if (this.failNextDraft) {
      job.state = 'failed'
      job.error = this.failNextDraft
      job.finished_at = AT
      this.failNextDraft = null
      return
    }
    if (!ws.catalog.length) ws.catalog = sampleCatalog()
    for (const e of ws.catalog) {
      if (e.description_source === 'owner') continue
      e.description = aiDescription(e)
      e.description_source = 'ai'
      e.approved = false
    }
    for (const [term, definition] of [
      ['Revenue', 'The sum of item prices.'],
      ['Late delivery', 'Delivered after the estimated date.'],
    ]) {
      if (ws.definitions.some((d) => d.term.toLowerCase() === term.toLowerCase())) continue
      this.seq += 1
      ws.definitions.push({ definition_id: `def-${this.seq}`, term, definition, source: 'ai', approved: false })
    }
    if (ws.setup_state === 'created' || ws.setup_state === 'scanned') ws.setup_state = 'drafted'
    job.done = job.total
    job.state = 'done'
    job.finished_at = AT
  }

  async handle(route: Route, method: string, path: string, body: Json, reply: Reply): Promise<void> {
    this.calls.push(`${method} ${path}`)
    const parts = path.split('/').filter(Boolean) // ['workspaces', id?, sub?, ...]
    if (parts.length === 1) {
      if (method === 'GET') return reply(route, 200, { workspaces: [...this.workspaces.values()].map((w) => this.view(w)) })
      if (method === 'POST') {
        const name = String(body.name ?? '').trim()
        const schema = String(body.target_schema ?? '')
        if (!name) return reply(route, 422, { detail: 'name must not be blank.' })
        if (!['olist', 'public', 'live'].includes(schema) && !this.extraDataset(schema)) return reply(route, 422, { detail: `Unknown dataset '${schema}'.` })
        const ws = this.create(name, schema, String(body.business_context ?? '').trim())
        return reply(route, 201, { workspace: this.view(ws) })
      }
    }
    const ws = this.workspaces.get(parts[1])
    if (!ws) return reply(route, 404, { detail: 'No such workspace.' })
    const sub = parts[2]

    if (!sub) {
      if (method === 'GET') return reply(route, 200, { workspace: this.view(ws) })
      if (method === 'DELETE') {
        this.workspaces.delete(ws.workspace_id)
        return reply(route, 200, { deleted: ws.workspace_id })
      }
      if (method === 'PATCH') {
        if (typeof body.name === 'string') {
          if (!body.name.trim()) return reply(route, 422, { detail: 'name must not be blank.' })
          ws.name = body.name.trim()
        }
        if (typeof body.business_context === 'string' && body.business_context.trim() !== ws.business_context) {
          ws.business_context = body.business_context.trim()
          ws.context_version += 1
        }
        return reply(route, 200, { workspace: this.view(ws) })
      }
    }
    if (sub === 'catalog' && method === 'GET') return reply(route, 200, { columns: ws.catalog })
    if (sub === 'descriptions' && method === 'PUT') {
      const items = (body.items as Json[]) ?? []
      for (const item of items) {
        const e = ws.catalog.find((c) => c.table_name === item.table_name && c.column_name === (item.column_name ?? ''))
        if (!e) return reply(route, 404, { detail: `${String(item.table_name)} is not in this workspace's catalog. Scan again.` })
        e.description = String(item.description ?? '').trim() || null
        e.description_source = 'owner'
        e.approved = false
      }
      return reply(route, 200, { updated: items.length })
    }
    if (sub === 'definitions') {
      if (method === 'GET') return reply(route, 200, { definitions: ws.definitions })
      if (method === 'PUT') {
        for (const item of (body.items as Json[]) ?? []) {
          const term = String(item.term ?? '').trim()
          const definition = String(item.definition ?? '').trim()
          if (!term || !definition) return reply(route, 422, { detail: 'Every definition needs a term and a definition.' })
          const existing = ws.definitions.find((d) => d.term.toLowerCase() === term.toLowerCase())
          if (existing) Object.assign(existing, { term, definition, source: 'owner', approved: false })
          else {
            this.seq += 1
            ws.definitions.push({ definition_id: `def-${this.seq}`, term, definition, source: 'owner', approved: false })
          }
        }
        return reply(route, 200, { definitions: ws.definitions })
      }
      if (method === 'DELETE' && parts[3]) {
        const before = ws.definitions.length
        ws.definitions = ws.definitions.filter((d) => d.definition_id !== parts[3])
        if (ws.definitions.length === before) return reply(route, 404, { detail: 'No such definition.' })
        return reply(route, 200, { deleted: parts[3] })
      }
    }
    if (sub === 'examples' && parts.length === 3 && method === 'GET') {
      const listed = ws.examples
        .map((e, i) => ({ example_id: `ex-${ws.workspace_id}-${i}`, question: e.question, sql_text: e.sql, source: 'owner' }))
        .sort((a, b) => a.question.toLowerCase().localeCompare(b.question.toLowerCase()))
      return reply(route, 200, { examples: listed })
    }
    if (sub === 'examples' && parts.length === 3) {
      const question = String(body.question ?? '').trim()
      if (method === 'POST') {
        const sql = String(body.sql ?? '').trim()
        if (!question || !sql) return reply(route, 422, { detail: 'An example needs a question and its SQL.' })
        // One approved answer per question: a newer one replaces it.
        ws.examples = ws.examples.filter((e) => WorkspaceMock.normalize(e.question) !== WorkspaceMock.normalize(question))
        ws.examples.push({ question, sql })
        this.seq += 1
        return reply(route, 200, { example_id: `ex-${this.seq}` })
      }
      if (method === 'DELETE') {
        if (!question) return reply(route, 422, { detail: 'question is required.' })
        const before = ws.examples.length
        ws.examples = ws.examples.filter((e) => WorkspaceMock.normalize(e.question) !== WorkspaceMock.normalize(question))
        return reply(route, 200, { removed: before - ws.examples.length })
      }
    }
    if (sub === 'approve' && method === 'POST') {
      const cols = ws.catalog.filter((c) => c.description && !c.approved)
      const defs = ws.definitions.filter((d) => !d.approved)
      cols.forEach((c) => (c.approved = true))
      defs.forEach((d) => (d.approved = true))
      if (ws.setup_state !== 'tested') ws.setup_state = 'approved'
      return reply(route, 200, {
        workspace: this.view(ws),
        approved_columns: cols.length,
        approved_definitions: defs.length,
      })
    }
    if (sub === 'draft') {
      if (method === 'POST') {
        if (this.activeJob()) return reply(route, 409, { detail: 'A setup step is already running. Wait for it to finish.' })
        return reply(route, 202, { job: this.newJob('draft', ws.workspace_id, 1) })
      }
      if (method === 'GET') {
        const job = this.latestJob(ws.workspace_id, 'draft')
        const view = job ? { ...job } : null // the poll sees the state before this step
        if (job) this.advanceDraft(ws, job)
        return reply(route, 200, { job: view })
      }
    }
    return reply(route, 404, { detail: 'Not Found' })
  }
}
