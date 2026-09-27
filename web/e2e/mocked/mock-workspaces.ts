/**
 * The /workspaces part of the mocked orchestrator (docs/V3_PLAN.md Phase 3).
 *
 * Mirrors orchestrator/workspaces_api.py and mcp/tools/workspace_tools.py:
 * owner text is stored as "owner" and never replaced by a draft; any edit
 * un-approves; approval is required before the setup test (409); sample
 * datasets (olist, public) are graded against references, anything else by the
 * owner; right answers become examples; one setup step at a time (409).
 *
 * Background jobs advance one step per status poll, so a test sees "running"
 * at least once and then the result, without timers.
 */

import type { Route } from '@playwright/test'

type Json = Record<string, unknown>
type Reply = (route: Route, status: number, body: unknown) => Promise<void>

export type SetupState = 'created' | 'scanned' | 'drafted' | 'approved' | 'tested'
export type Verdict = 'pending' | 'running' | 'right' | 'wrong' | 'error'

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
  source: 'ai' | 'owner'
  approved: boolean
}

export interface SetupResult {
  position: number
  question: string
  has_reference: boolean
  answer_text: string | null
  answer_sql: string | null
  verdict: Verdict
  detail: string | null
}

export interface SetupRun {
  run_id: string
  workspace_id: string
  state: 'running' | 'done' | 'failed'
  graded_by: 'reference' | 'owner'
  score: number | null
  total: number
  started_at: string
  finished_at: string | null
  results: SetupResult[]
}

export interface SetupJob {
  job_id: string
  kind: 'draft' | 'test'
  workspace_id: string
  state: 'running' | 'done' | 'failed'
  done: number
  total: number
  error: string | null
  run_id: string | null
  started_at: string
  finished_at: string | null
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
  examples: { question: string; sql: string }[]
  runs: SetupRun[]
}

const REFERENCE_SCHEMAS = ['olist', 'public']
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

export const SETUP_QUESTIONS = Array.from({ length: 10 }, (_, i) => `Setup question ${i + 1}?`)

export class WorkspaceMock {
  readonly workspaces = new Map<string, MockWorkspace>()
  readonly calls: string[] = []
  /** Positions the reference grading marks wrong. */
  wrongPositions: number[] = [3, 7]
  /** When set, the next draft job fails with this message. */
  failNextDraft: string | null = null
  /** When set, the next setup test stops at its first poll with this message (as when the quota runs out). */
  failNextTest: string | null = null
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
      runs: [],
    }
    this.workspaces.set(ws.workspace_id, ws)
    return ws
  }

  /** A workspace already through the wizard, for tests that start after setup. */
  createReady(name: string, targetSchema: string, examples: { question: string; sql: string }[] = []): MockWorkspace {
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

  /** The exact approved example for a question, as the fast path matches it. */
  exactExample(ws: MockWorkspace, question: string): { question: string; sql: string } | null {
    const norm = (q: string) => q.toLowerCase().replace(/['’]/g, '').match(/[a-z0-9]+/g)?.join(' ') ?? ''
    return ws.examples.find((e) => norm(e.question) === norm(question)) ?? null
  }

  private activeJob(): SetupJob | undefined {
    return [...this.jobs.values()].find((j) => j.state === 'running')
  }

  private latestJob(workspaceId: string, kind: 'draft' | 'test'): SetupJob | null {
    const mine = [...this.jobs.values()].filter((j) => j.workspace_id === workspaceId && j.kind === kind)
    return mine.length ? mine[mine.length - 1] : null
  }

  private newJob(kind: 'draft' | 'test', workspaceId: string, total: number): SetupJob {
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

  private advanceTest(ws: MockWorkspace, job: SetupJob): void {
    const run = ws.runs.find((r) => r.run_id === job.run_id)
    if (!run || job.state !== 'running') return
    if (this.failNextTest) {
      run.results.forEach((r) => {
        r.verdict = 'error'
        r.detail = this.failNextTest
      })
      run.state = 'failed'
      run.score = 0
      job.state = 'failed'
      job.error = this.failNextTest
      job.finished_at = AT
      this.failNextTest = null
      return
    }
    const next = run.results.filter((r) => r.verdict === 'pending' || r.verdict === 'running')
    const step = run.graded_by === 'owner' ? run.results.filter((r) => r.answer_text === null) : next
    for (const r of step.slice(0, 4)) {
      r.answer_text = `The answer to question ${r.position}.`
      r.answer_sql = `SELECT ${r.position}`
      if (run.graded_by === 'reference') {
        const wrong = this.wrongPositions.includes(r.position)
        r.verdict = wrong ? 'wrong' : 'right'
        r.detail = wrong
          ? 'Different result: 3 rows against the reference\'s 5; 60% of its rows found.'
          : 'Same result as the reference (5 rows).'
      }
    }
    job.done = run.results.filter((r) => r.answer_text !== null).length
    if (job.done < run.total) return
    this.finishRun(ws, run)
    job.state = 'done'
    job.finished_at = AT
  }

  private finishRun(ws: MockWorkspace, run: SetupRun): void {
    run.state = 'done'
    run.finished_at = AT
    run.score = run.results.filter((r) => r.verdict === 'right').length
    ws.setup_state = 'tested'
    ws.setup_score = run.score
    ws.setup_total = run.total
    for (const r of run.results) {
      const existing = ws.examples.findIndex((e) => e.question === r.question)
      if (r.verdict === 'right' && existing < 0) ws.examples.push({ question: r.question, sql: r.answer_sql ?? '' })
      if (r.verdict === 'wrong' && existing >= 0) ws.examples.splice(existing, 1)
    }
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
        if (!['olist', 'public', 'live'].includes(schema)) return reply(route, 422, { detail: `Unknown dataset '${schema}'.` })
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
    if (sub === 'setup') {
      if (parts.length === 3 && method === 'POST') {
        if (ws.setup_state !== 'approved' && ws.setup_state !== 'tested') {
          return reply(route, 409, { detail: 'Review and approve the descriptions before the test.' })
        }
        if (this.activeJob()) return reply(route, 409, { detail: 'A setup step is already running. Wait for it to finish.' })
        const gradedBy = REFERENCE_SCHEMAS.includes(ws.target_schema) ? 'reference' : 'owner'
        this.seq += 1
        const run: SetupRun = {
          run_id: `run-${this.seq}`,
          workspace_id: ws.workspace_id,
          state: 'running',
          graded_by: gradedBy,
          score: null,
          total: SETUP_QUESTIONS.length,
          started_at: AT,
          finished_at: null,
          results: SETUP_QUESTIONS.map((question, i) => ({
            position: i + 1,
            question,
            has_reference: gradedBy === 'reference',
            answer_text: null,
            answer_sql: null,
            verdict: 'pending' as Verdict,
            detail: null,
          })),
        }
        ws.runs.push(run)
        const job = this.newJob('test', ws.workspace_id, run.total)
        job.run_id = run.run_id
        return reply(route, 202, { job, graded_by: gradedBy })
      }
      if (parts.length === 3 && method === 'GET') {
        const job = this.latestJob(ws.workspace_id, 'test')
        if (job) this.advanceTest(ws, job)
        const run = ws.runs.length ? ws.runs[ws.runs.length - 1] : null
        return reply(route, 200, { run, job: job ? { ...job } : null })
      }
      // PUT /workspaces/{id}/setup/{run_id}/results/{position}
      if (parts.length === 6 && parts[4] === 'results' && method === 'PUT') {
        const run = ws.runs.find((r) => r.run_id === parts[3])
        if (!run) return reply(route, 404, { detail: 'No such setup run.' })
        if (run.graded_by !== 'owner') {
          return reply(route, 422, { detail: 'This run is graded against reference answers, not by hand.' })
        }
        const verdict = body.verdict
        if (verdict !== 'right' && verdict !== 'wrong') return reply(route, 422, { detail: 'verdict must be right or wrong.' })
        const result = run.results.find((r) => r.position === Number(parts[5]))
        if (!result) return reply(route, 404, { detail: 'No such question in that run.' })
        result.verdict = verdict
        this.finishRun(ws, run)
        return reply(route, 200, { run })
      }
    }
    return reply(route, 404, { detail: 'Not Found' })
  }
}
