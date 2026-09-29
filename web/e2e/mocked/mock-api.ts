/**
 * A stand-in orchestrator for the mocked browser suite.
 *
 * It mirrors the real API's shapes and rules (web/src/api/types.ts,
 * orchestrator/main.py, orchestrator/tiles_api.py): bearer auth on everything
 * but /health, turn numbers assigned per session, docs-only answers not
 * pinnable (422), tile ownership by construction, interval bounds (422),
 * rate limits (429 + Retry-After). Tests drive it directly (failNext,
 * setTileState) to reach states the real stack only produces rarely.
 */

import type { Page, Route } from '@playwright/test'

import { WorkspaceMock } from './mock-workspaces'

export const TOKEN = 'test-token-for-mocked-suite'
export const API = 'http://localhost:8090'
const SCHEMA_URL = 'https://vega.github.io/schema/vega-lite/v5.json'

type Row = Record<string, unknown>

export type VizKindName =
  | 'kpi'
  | 'kpi_trend'
  | 'line'
  | 'area'
  | 'bar'
  | 'hbar'
  | 'donut'
  | 'breakdown'
  | 'heatmap'
  | 'scatter'
  | 'table'

export interface TileLayout {
  x: number
  y: number
  w: number
  h: number
}

interface StoredTurn {
  turn_index: number
  raw_query: string
  rewritten_query: string
  rewrite_applied: boolean
  context_guessed: boolean
  route: 'sql' | 'docs' | 'hybrid' | null
  executed_sql: string | null
  answer_text: string
  computed_at: string
  chart: Record<string, unknown> | null
  sql_columns: string[] | null
  sql_rows_preview: Row[] | null
  sql_rows_count: number | null
  target_schema: string | null
}

export interface Tile {
  tile_id: string
  dashboard_id: string
  target_schema: string
  viz: VizKindName | null
  layout: TileLayout | null
  title: string
  state: 'ok' | 'needs_review' | 'error'
  version: number
  refresh_interval_s: number
  created_at: string
  updated_at: string
  sql: string
  chart: Record<string, unknown> | null
  columns: string[] | null
  schemas: string[]
  source: { session_id: string; turn_index: number }
  result: { rows: Row[]; row_count: number; truncated: boolean; computed_at: string; version: number } | null
  stale: boolean
  last_error: string | null
  error_at: string | null
  versions: { version: number; created_at: string; source: { session_id: string; turn_index: number } }[]
}

export interface Dashboard {
  dashboard_id: string
  name: string
  target_schema: string
  position: number
  tile_count: number
  created_at: string
  updated_at: string
}

export type BuildItemState = 'queued' | 'running' | 'added' | 'skipped'

export interface BuildItem {
  title: string
  question: string
  viz: VizKindName | null
  state: BuildItemState
  reason: string | null
  tile_id: string | null
}

export interface BuildJob {
  job_id: string
  dashboard_id: string
  state: 'planning' | 'running' | 'done' | 'failed'
  goal: string | null
  items: BuildItem[]
  error: string | null
  started_at: string
  finished_at: string | null
}

export interface BuildPlanItem {
  title: string
  question: string
  viz: VizKindName | null
}

interface Canned {
  answer: string
  route: 'sql' | 'docs' | 'hybrid'
  sql: string | null
  columns: string[]
  rows: Row[]
  chart: Record<string, unknown> | null
  citations: { kind: 'sql' | 'chunk'; ref: string; page?: number | null }[]
}

const STATUS_ROWS: Row[] = [
  { order_status: 'delivered', n: 96478 },
  { order_status: 'shipped', n: 1107 },
  { order_status: 'canceled', n: 625 },
]

const MANY_ROWS: Row[] = Array.from({ length: 60 }, (_, i) => ({ city: `City ${i + 1}`, sellers: 60 - i }))

export function barChart(rows: Row[]): Record<string, unknown> {
  return {
    $schema: SCHEMA_URL,
    mark: 'bar',
    encoding: {
      x: { field: 'order_status', type: 'nominal', title: 'Status' },
      y: { field: 'n', type: 'quantitative', title: 'Orders' },
    },
    data: { values: rows },
  }
}

/** Picks a canned answer from words in the question. */
export function cannedAnswer(question: string): Canned {
  const q = question.toLowerCase()
  if (q.includes('unsafe')) {
    const chart = barChart(STATUS_ROWS)
    chart.transform = [{ calculate: 'datum.n * 2', as: 'double' }]
    return {
      answer: 'Here are orders by status.',
      route: 'sql',
      sql: 'SELECT order_status, count(*) AS n FROM olist.orders GROUP BY 1',
      columns: ['order_status', 'n'],
      rows: STATUS_ROWS,
      chart,
      citations: [{ kind: 'sql', ref: 'SELECT order_status, count(*) AS n FROM olist.orders GROUP BY 1' }],
    }
  }
  if (q.includes('chart') || q.includes('status')) {
    const sql = 'SELECT order_status, count(*) AS n FROM olist.orders GROUP BY 1 ORDER BY 2 DESC'
    return {
      answer: 'Delivered orders dominate: 96,478 of 98,210.',
      route: 'sql',
      sql,
      columns: ['order_status', 'n'],
      rows: STATUS_ROWS,
      chart: barChart(STATUS_ROWS),
      citations: [{ kind: 'sql', ref: sql }],
    }
  }
  if (q.includes('policy') || q.includes('document') || q.includes('sla')) {
    return {
      answer: 'FastShip 3 (LP003) commits to 20 hours for Metro Mumbai at a 99% on-time rate.',
      route: 'docs',
      sql: null,
      columns: [],
      rows: [],
      chart: null,
      citations: [
        { kind: 'chunk', ref: 'doc_sla_annexure_LP003_c001', page: 1 },
        { kind: 'chunk', ref: 'doc_sla_annexure_LP003_c000', page: 1 },
      ],
    }
  }
  if (q.includes('cities') || q.includes('many rows')) {
    const sql = 'SELECT city, count(*) AS sellers FROM sellers GROUP BY 1 ORDER BY 2 DESC'
    return {
      answer: 'There are 60 cities with at least one seller.',
      route: 'sql',
      sql,
      columns: ['city', 'sellers'],
      rows: MANY_ROWS,
      chart: null,
      citations: [{ kind: 'sql', ref: sql }],
    }
  }
  const sql = 'SELECT COUNT(*) AS total_orders FROM orders LIMIT 1000'
  return {
    answer: 'There are 550 orders in total.',
    route: 'sql',
    sql,
    columns: ['total_orders'],
    rows: [{ total_orders: 550 }],
    chart: null,
    citations: [{ kind: 'sql', ref: sql }],
  }
}

export class MockApi {
  readonly queries: Record<string, unknown>[] = []
  readonly sessions = new Map<string, StoredTurn[]>()
  readonly tiles = new Map<string, Tile>()
  readonly dashboards = new Map<string, Dashboard>()
  readonly jobs = new Map<string, BuildJob>()
  /** Workspaces (Phase 3): mock-workspaces.ts. */
  readonly workspaces = new WorkspaceMock()
  private failures = new Map<string, { status: number; detail: string; headers?: Record<string, string> }>()
  storedRowsLimit: number | null = null
  nextChart: Record<string, unknown> | null = null
  /** 'short' mimics pipeline.synth_style=short: /query returns no chart and says so in state_trace. */
  nextSynthStyle: 'full' | 'short' = 'full'
  /** Caveats the next /query reports in state_trace (pipeline.answer_checks); cleared after use. */
  nextCaveats: string[] = []
  /** When set, the next /query reports this evidence tier instead of the default (verified or unchecked). */
  nextEvidence: 'verified' | 'compiled' | 'checked' | 'unchecked' | null = null
  /** When set, the next /query answer text is this instead of the canned one. */
  nextAnswer: string | null = null
  liveSchemas: string[] = []
  readonly tileCalls: string[] = []
  readonly buildCalls: string[] = []
  buildPlan: BuildPlanItem[] = [
    { title: 'Total orders', question: 'How many orders are there in total?', viz: 'kpi' },
    { title: 'Orders by status', question: 'Show orders by status as a chart', viz: 'bar' },
    { title: 'SLA policy LP003', question: 'What does the SLA policy say for LP003?', viz: 'table' },
  ]
  private clock = Date.parse('2026-09-27T10:00:00Z')
  private tileSeq = 0
  private dashSeq = 0
  private jobSeq = 0

  constructor(options: { buildPlan?: BuildPlanItem[] } = {}) {
    if (options.buildPlan) {
      this.buildPlan = options.buildPlan
    }
    const at = new Date(this.clock - 3_600_000).toISOString()
    this.sessions.set('seed-session-1', [
      {
        turn_index: 1,
        raw_query: 'How many orders are there in total?',
        rewritten_query: 'How many orders are there in total?',
        rewrite_applied: false,
        context_guessed: false,
        route: 'sql',
        executed_sql: 'SELECT COUNT(*) AS total_orders FROM orders LIMIT 1000',
        answer_text: 'There are 550 orders in total.',
        computed_at: at,
        chart: null,
        sql_columns: ['total_orders'],
        sql_rows_preview: [{ total_orders: 550 }],
        sql_rows_count: 1,
        target_schema: 'public',
      },
      {
        turn_index: 2,
        raw_query: 'Which cities have the most sellers?',
        rewritten_query: 'Which cities have the most sellers?',
        rewrite_applied: false,
        context_guessed: false,
        route: 'sql',
        executed_sql: 'SELECT city, count(*) AS sellers FROM sellers GROUP BY 1 ORDER BY 2 DESC',
        answer_text: 'There are 60 cities with at least one seller.',
        computed_at: at,
        chart: null,
        sql_columns: ['city', 'sellers'],
        sql_rows_preview: MANY_ROWS.slice(0, 20),
        sql_rows_count: 60,
        target_schema: 'public',
      },
    ])
    this.sessions.set('seed-session-olist', [
      {
        turn_index: 1,
        raw_query: 'How many orders were delivered on olist?',
        rewritten_query: 'How many orders were delivered on olist?',
        rewrite_applied: false,
        context_guessed: false,
        route: 'sql',
        executed_sql: "SELECT count(*) AS delivered FROM olist.orders WHERE order_status = 'delivered'",
        answer_text: 'There were 96,478 orders delivered.',
        computed_at: at,
        chart: null,
        sql_columns: ['delivered'],
        sql_rows_preview: [{ delivered: 96478 }],
        sql_rows_count: 1,
        target_schema: 'olist',
      },
    ])
  }

  createDashboard(name: string, targetSchema = 'public'): Dashboard {
    this.dashSeq += 1
    const id = `dash-${this.dashSeq}`
    const at = this.now()
    const existing = [...this.dashboards.values()].filter((d) => d.target_schema === targetSchema)
    const dashboard: Dashboard = {
      dashboard_id: id,
      name,
      target_schema: targetSchema,
      position: existing.length,
      tile_count: 0,
      created_at: at,
      updated_at: at,
    }
    this.dashboards.set(id, dashboard)
    return dashboard
  }

  private toDashboardDto(d: Dashboard): Dashboard {
    const count = [...this.tiles.values()].filter((t) => t.dashboard_id === d.dashboard_id).length
    return { ...d, tile_count: count }
  }

  failNext(path: string, status: number, detail: string, headers?: Record<string, string>): void {
    this.failures.set(path, { status, detail, headers })
  }

  setTileState(tileId: string, state: Tile['state'], lastError: string | null = null): void {
    const tile = this.tiles.get(tileId)
    if (!tile) throw new Error(`no tile ${tileId}`)
    tile.state = state
    tile.last_error = lastError
    tile.error_at = lastError ? this.now() : null
  }

  setTileChart(tileId: string, chart: Record<string, unknown> | null): void {
    const tile = this.tiles.get(tileId)
    if (!tile) throw new Error(`no tile ${tileId}`)
    tile.chart = chart
  }

  async install(page: Page): Promise<void> {
    await page.route(`${API}/**`, (route) => this.handle(route))
  }

  private now(): string {
    this.clock += 1000
    return new Date(this.clock).toISOString()
  }

  private json(route: Route, status: number, body: unknown, headers: Record<string, string> = {}) {
    return route.fulfill({
      status,
      contentType: 'application/json',
      headers: { 'access-control-allow-origin': '*', 'access-control-expose-headers': 'Retry-After', ...headers },
      body: JSON.stringify(body),
    })
  }

  private async handle(route: Route) {
    const request = route.request()
    const method = request.method()
    const url = new URL(request.url())
    const path = url.pathname

    if (method === 'OPTIONS') {
      return route.fulfill({
        status: 204,
        headers: {
          'access-control-allow-origin': '*',
          'access-control-allow-methods': 'GET, POST, PUT, PATCH, DELETE',
          'access-control-allow-headers': 'authorization, content-type',
        },
      })
    }

    if (path === '/health') {
      return this.json(route, 200, {
        status: 'ok',
        service: 'orchestrator',
        llm_provider: 'groq',
        llm_ready: true,
        llm_detail: null,
        default_schema: 'public',
        available_schemas: ['olist', 'public'],
        live_schemas: this.liveSchemas,
      })
    }

    const failure = this.failures.get(path)
    if (failure) {
      this.failures.delete(path)
      return this.json(route, failure.status, { detail: failure.detail }, failure.headers)
    }

    if (request.headers()['authorization'] !== `Bearer ${TOKEN}`) {
      return this.json(route, 401, { detail: 'Missing or invalid bearer token.' })
    }

    const body = request.postData() ? (JSON.parse(request.postData() as string) as Record<string, unknown>) : {}

    if (path === '/query' && method === 'POST') return this.query(route, body)
    if (path === '/sessions' && method === 'GET') {
      const sessions = [...this.sessions.entries()].map(([session_id, turns]) => ({
        session_id,
        turn_count: turns.length,
        started_at: turns[0].computed_at,
        last_activity: turns[turns.length - 1].computed_at,
        first_query: turns[0].raw_query,
        target_schema: turns[0].target_schema,
      }))
      return this.json(route, 200, { sessions, count: sessions.length })
    }
    let m = path.match(/^\/sessions\/([^/]+)\/turns\/(\d+)\/rows$/)
    if (m && method === 'GET') {
      const turn = (this.sessions.get(decodeURIComponent(m[1])) ?? []).find((t) => t.turn_index === Number(m![2]))
      if (!turn) return this.json(route, 200, { rows: [], stored_row_count: 0, byte_size: 0, available: false })
      const all = turn.sql_rows_count === 60 ? MANY_ROWS : turn.sql_rows_preview ?? []
      const full = this.storedRowsLimit === null ? all : all.slice(0, this.storedRowsLimit)
      return this.json(route, 200, { rows: full, stored_row_count: full.length, byte_size: 1024, available: true })
    }
    m = path.match(/^\/sessions\/([^/]+)$/)
    if (m) {
      const sessionId = decodeURIComponent(m[1])
      if (method === 'GET') {
        const turns = this.sessions.get(sessionId) ?? []
        return this.json(route, 200, { turns, count: turns.length, next_turn_index: turns.length + 1 })
      }
      if (method === 'DELETE') {
        const turns = this.sessions.get(sessionId)
        if (!turns) return this.json(route, 404, { detail: 'No such conversation.' })
        const deleted_turns = turns.length
        this.sessions.delete(sessionId)
        return this.json(route, 200, { deleted: sessionId, deleted_turns })
      }
    }
    if (path.startsWith('/dashboards')) return this.dashboardsRoute(route, method, path, url, body)
    if (path.startsWith('/tiles')) return this.tilesRoute(route, method, path, url, body)
    if (path === '/workspaces' || path.startsWith('/workspaces/')) {
      return this.workspaces.handle(route, method, path, body, (r, status, b) => this.json(r, status, b))
    }
    return this.json(route, 404, { detail: 'Not Found' })
  }

  private query(route: Route, body: Record<string, unknown>) {
    this.queries.push(body)
    const question = String(body.query ?? '')
    const sessionId = String(body.session_id ?? `srv-${this.queries.length}`)
    const canned = cannedAnswer(question)
    const turns = this.sessions.get(sessionId) ?? []
    let schema = String(body.schema ?? 'public')
    // A workspace decides the dataset and must exist (orchestrator/main.py).
    let verifiedExample = false
    const workspaceId = typeof body.workspace_id === 'string' ? body.workspace_id : null
    if (workspaceId) {
      const ws = this.workspaces.workspaces.get(workspaceId)
      if (!ws) return this.json(route, 404, { detail: 'No such workspace.' })
      if (body.schema && body.schema !== ws.target_schema) {
        return this.json(route, 409, {
          detail: `This workspace uses the ${ws.target_schema} dataset, not ${String(body.schema)}.`,
        })
      }
      schema = ws.target_schema
      const exact = this.workspaces.exactExample(ws, question)
      if (exact) {
        verifiedExample = true
        canned.sql = exact.sql
      }
    }
    const startedOn = turns[0]?.target_schema
    if (startedOn && startedOn !== schema) {
      return this.json(route, 409, {
        detail: `This conversation uses the ${startedOn} dataset. Start a new conversation to ask about ${schema}.`,
      })
    }
    if (this.nextAnswer !== null) {
      canned.answer = this.nextAnswer
      this.nextAnswer = null
    }
    if (this.nextChart) {
      canned.chart = this.nextChart
      this.nextChart = null
    }
    if (this.nextSynthStyle === 'short') canned.chart = null
    const caveats = this.nextCaveats
    this.nextCaveats = []
    // The default tier: an approved example ran as is, else new SQL nobody checked; no SQL, no tier.
    const evidence = this.nextEvidence ?? (canned.sql ? (verifiedExample ? 'verified' : 'unchecked') : null)
    this.nextEvidence = null
    const turnIndex = turns.length + 1
    const at = this.now()
    turns.push({
      turn_index: turnIndex,
      raw_query: question,
      rewritten_query: question,
      rewrite_applied: false,
      context_guessed: false,
      route: canned.route,
      executed_sql: canned.sql,
      answer_text: canned.answer,
      computed_at: at,
      chart: canned.chart,
      sql_columns: canned.columns.length ? canned.columns : null,
      sql_rows_preview: canned.rows.slice(0, 100),
      sql_rows_count: canned.rows.length,
      target_schema: schema,
    })
    this.sessions.set(sessionId, turns)
    return this.json(route, 200, {
      query_id: `q-${this.queries.length}`,
      answer_text: canned.answer,
      chart: canned.chart,
      citations: canned.citations,
      latency_ms: 1234,
      state_trace: {
        query_id: `q-${this.queries.length}`,
        target_schema: schema,
        workspace_id: workspaceId,
        verified_example: verifiedExample,
        evidence,
        route: canned.route,
        executed_sql: canned.sql,
        ...(canned.sql ? { explanation: 'Counted rows in orders, grouped by order status.' } : {}),
        candidate_sql: canned.sql,
        sql_rows_count: canned.rows.length,
        sql_rows: canned.rows,
        sql_columns: canned.columns,
        synth_style: this.nextSynthStyle,
        ...(caveats.length > 0 ? { caveats } : {}),
        attempt: 1,
        errors: [],
        metrics: { tokens_in: 5164, tokens_out: 298, llm_calls: 5 },
        memory_hit: false,
        detected_language: 'en',
        normalized_query: question,
        preserved_entities: {},
        turn_index: turnIndex,
        rewrite_applied: false,
        context_guessed: false,
        subtasks: [],
        retrievals: [],
        calls: [
          { role: 'normalizer', model: 'llama-3.1-8b-instant', tokens_in: 412, tokens_out: 38, cached_tokens: 0, retries: 0, waited_ms: 0, ms: 310, rate_remaining_tokens: 7588 },
          { role: 'supervisor', model: 'llama-3.1-8b-instant', tokens_in: 655, tokens_out: 22, cached_tokens: 384, retries: 0, waited_ms: 0, ms: 280, rate_remaining_tokens: 6911 },
          { role: 'sql', model: 'openai/gpt-oss-120b', tokens_in: 2104, tokens_out: 96, cached_tokens: 1024, retries: 0, waited_ms: 0, ms: 1240, rate_remaining_tokens: 4800 },
          { role: 'sql', model: 'openai/gpt-oss-120b', tokens_in: 1290, tokens_out: 71, cached_tokens: 0, retries: 1, waited_ms: 2000, ms: 3410, rate_remaining_tokens: 3500 },
          { role: 'synthesizer', model: 'openai/gpt-oss-120b', tokens_in: 703, tokens_out: 71, cached_tokens: 0, retries: 0, waited_ms: 0, ms: 840, rate_remaining_tokens: 2700 },
        ],
      },
    })
  }

  private makeTile(
    turn: StoredTurn,
    sessionId: string,
    dashboardId: string,
    targetSchema: string,
    title?: string,
    interval?: number,
    viz?: VizKindName | null,
    layout?: TileLayout | null,
  ): Tile {
    const at = this.now()
    this.tileSeq += 1
    const rows = turn.sql_rows_preview ?? []
    const chart = turn.chart ? { ...turn.chart, data: { values: rows } } : null
    return {
      tile_id: `tile-${this.tileSeq}`,
      dashboard_id: dashboardId,
      target_schema: targetSchema,
      viz: viz ?? null,
      layout: layout ?? null,
      title: title || turn.raw_query,
      state: 'ok',
      version: 1,
      refresh_interval_s: interval ?? 3600,
      created_at: at,
      updated_at: at,
      sql: turn.executed_sql ?? '',
      chart,
      columns: turn.sql_columns,
      schemas: [targetSchema],
      source: { session_id: sessionId, turn_index: turn.turn_index },
      result: { rows, row_count: rows.length, truncated: false, computed_at: at, version: 1 },
      stale: false,
      last_error: null,
      error_at: null,
      versions: [{ version: 1, created_at: at, source: { session_id: sessionId, turn_index: turn.turn_index } }],
    }
  }

  private makeTileFromBuild(dashboardId: string, item: BuildItem, canned: Canned): Tile {
    const at = this.now()
    this.tileSeq += 1
    const targetDash = this.dashboards.get(dashboardId)
    const schema = targetDash?.target_schema ?? 'public'
    const rows = canned.rows
    const chart = canned.chart ? { ...canned.chart, data: { values: rows } } : null
    return {
      tile_id: `tile-${this.tileSeq}`,
      dashboard_id: dashboardId,
      target_schema: schema,
      viz: item.viz ?? null,
      layout: { x: 0, y: 0, w: 6, h: 4 },
      title: item.title,
      state: 'ok',
      version: 1,
      refresh_interval_s: 3600,
      created_at: at,
      updated_at: at,
      sql: canned.sql ?? '',
      chart,
      columns: canned.columns,
      schemas: [schema],
      source: { session_id: 'build', turn_index: 1 },
      result: { rows, row_count: rows.length, truncated: false, computed_at: at, version: 1 },
      stale: false,
      last_error: null,
      error_at: null,
      versions: [{ version: 1, created_at: at, source: { session_id: 'build', turn_index: 1 } }],
    }
  }

  private dashboardsRoute(route: Route, method: string, path: string, url: URL, body: Record<string, unknown>) {
    this.tileCalls.push(`${method} ${path}`)

    let m = path.match(/^\/dashboards\/([^/]+)\/build$/)
    if (m) {
      this.buildCalls.push(`${method} ${path}`)
      const id = decodeURIComponent(m[1])
      const dashboard = this.dashboards.get(id)
      if (!dashboard) return this.json(route, 404, { detail: 'No such dashboard.' })

      if (method === 'POST') {
        this.jobSeq += 1
        const jobId = `job-${this.jobSeq}`
        const items: BuildItem[] = this.buildPlan.map((p) => ({
          title: p.title,
          question: p.question,
          viz: p.viz,
          state: 'queued',
          reason: null,
          tile_id: null,
        }))
        const job: BuildJob = {
          job_id: jobId,
          dashboard_id: id,
          state: 'running',
          goal: typeof body.goal === 'string' ? body.goal : null,
          items,
          error: null,
          started_at: this.now(),
          finished_at: null,
        }
        this.jobs.set(id, job)
        return this.json(route, 202, { job })
      }

      if (method === 'GET') {
        const job = this.jobs.get(id)
        if (!job) return this.json(route, 200, { job: null })
        if (job.state === 'running') {
          const nextItem = job.items.find((i) => i.state === 'queued')
          if (nextItem) {
            const canned = cannedAnswer(nextItem.question)
            if (canned.route === 'docs') {
              nextItem.state = 'skipped'
              nextItem.reason = 'Answered from documents, not the database.'
            } else {
              nextItem.state = 'added'
              const tile = this.makeTileFromBuild(id, nextItem, canned)
              this.tiles.set(tile.tile_id, tile)
              nextItem.tile_id = tile.tile_id
            }
            if (!job.items.some((i) => i.state === 'queued')) {
              job.state = 'done'
              job.finished_at = this.now()
            }
          }
        }
        return this.json(route, 200, { job })
      }
      return this.json(route, 404, { detail: 'Not Found' })
    }

    m = path.match(/^\/dashboards\/([^/]+)\/layout$/)
    if (m && method === 'PUT') {
      const id = decodeURIComponent(m[1])
      const dashboard = this.dashboards.get(id)
      if (!dashboard) return this.json(route, 404, { detail: 'No such dashboard.' })
      const items = (body.items as { tile_id: string; x: number; y: number; w: number; h: number }[]) ?? []
      for (const item of items) {
        const tile = this.tiles.get(item.tile_id)
        if (!tile || tile.dashboard_id !== id) {
          return this.json(route, 404, { detail: 'Tile not found on this dashboard.' })
        }
      }
      for (const item of items) {
        const tile = this.tiles.get(item.tile_id)!
        tile.layout = { x: item.x, y: item.y, w: item.w, h: item.h }
        tile.updated_at = this.now()
      }
      return this.json(route, 200, { dashboard_id: id, updated: items.length })
    }

    if (path === '/dashboards') {
      if (method === 'GET') {
        const schema = url.searchParams.get('schema')
        let list = [...this.dashboards.values()]
        if (schema) list = list.filter((d) => d.target_schema === schema)
        list.sort((a, b) => a.position - b.position)
        return this.json(route, 200, { dashboards: list.map((d) => this.toDashboardDto(d)) })
      }
      if (method === 'POST') {
        if (typeof body.name !== 'string' || !body.name.trim()) {
          return this.json(route, 422, { detail: 'name must not be blank.' })
        }
        const targetSchema = String(body.target_schema ?? 'public')
        const dashboard = this.createDashboard(body.name.trim(), targetSchema)
        return this.json(route, 201, { dashboard: this.toDashboardDto(dashboard) })
      }
    }

    m = path.match(/^\/dashboards\/([^/]+)$/)
    if (m) {
      const id = decodeURIComponent(m[1])
      const dashboard = this.dashboards.get(id)
      if (!dashboard) return this.json(route, 404, { detail: 'No such dashboard.' })

      if (method === 'PATCH') {
        if (typeof body.name === 'string') {
          if (!body.name.trim()) return this.json(route, 422, { detail: 'name must not be blank.' })
          dashboard.name = body.name.trim()
        }
        dashboard.updated_at = this.now()
        return this.json(route, 200, { dashboard: this.toDashboardDto(dashboard) })
      }

      if (method === 'DELETE') {
        this.dashboards.delete(id)
        for (const [tileId, tile] of this.tiles.entries()) {
          if (tile.dashboard_id === id) {
            this.tiles.delete(tileId)
          }
        }
        return this.json(route, 200, { deleted: id })
      }
    }

    return this.json(route, 404, { detail: 'Not Found' })
  }

  private tilesRoute(route: Route, method: string, path: string, url: URL, body: Record<string, unknown>) {
    this.tileCalls.push(`${method} ${path}`)
    if (path === '/tiles' && method === 'GET') {
      const dashboardId = url.searchParams.get('dashboard_id')
      if (dashboardId) {
        if (!this.dashboards.has(dashboardId)) {
          return this.json(route, 404, { detail: 'No such dashboard.' })
        }
        const tiles = [...this.tiles.values()].filter((t) => t.dashboard_id === dashboardId)
        return this.json(route, 200, { tiles })
      }
      return this.json(route, 200, { tiles: [...this.tiles.values()] })
    }
    if (path === '/tiles' && method === 'POST') {
      const sessionId = String(body.session_id ?? '')
      const turn = (this.sessions.get(sessionId) ?? []).find((t) => t.turn_index === Number(body.turn_index))
      if (!turn) return this.json(route, 404, { detail: 'No such conversation turn.' })
      if (!turn.executed_sql) return this.json(route, 422, { detail: 'Only answers computed from the database can be pinned.' })
      const targetSchema = turn.target_schema ?? 'public'
      let dashboardId = body.dashboard_id as string | undefined
      if (dashboardId) {
        const d = this.dashboards.get(dashboardId)
        if (!d) return this.json(route, 404, { detail: 'No such dashboard.' })
        if (d.target_schema !== targetSchema) return this.json(route, 422, { detail: 'Dashboard schema differs.' })
      } else {
        let d = [...this.dashboards.values()].find((x) => x.target_schema === targetSchema)
        if (!d) {
          d = this.createDashboard('Overview', targetSchema)
        }
        dashboardId = d.dashboard_id
      }
      const tile = this.makeTile(
        turn,
        sessionId,
        dashboardId,
        targetSchema,
        body.title as string | undefined,
        body.refresh_interval_s as number | undefined,
        (body.viz as VizKindName) ?? null,
        (body.layout as TileLayout) ?? null,
      )
      this.tiles.set(tile.tile_id, tile)
      return this.json(route, 201, { tile })
    }
    const m = path.match(/^\/tiles\/([^/]+)(\/(refresh|replace|confirm))?$/)
    const tile = m ? this.tiles.get(decodeURIComponent(m[1])) : undefined
    if (!m || !tile) return this.json(route, 404, { detail: 'No such tile.' })
    const action = m[3]
    if (method === 'GET' && !action) return this.json(route, 200, { tile })
    if (method === 'DELETE' && !action) {
      this.tiles.delete(tile.tile_id)
      return this.json(route, 200, { deleted: tile.tile_id })
    }
    if (method === 'PATCH' && !action) {
      const interval = body.refresh_interval_s
      if (interval !== undefined && (typeof interval !== 'number' || interval < 30 || interval > 604800)) {
        return this.json(route, 422, { detail: 'refresh_interval_s must be a whole number of seconds between 30 and 604800.' })
      }
      if (typeof body.title === 'string') {
        if (!body.title.trim()) return this.json(route, 422, { detail: 'title must not be blank.' })
        tile.title = body.title.trim()
      }
      if (typeof interval === 'number') tile.refresh_interval_s = interval
      if (body.viz !== undefined) {
        tile.viz = body.viz === 'auto' ? null : (body.viz as VizKindName)
      }
      if (body.dashboard_id !== undefined) {
        const targetDash = this.dashboards.get(String(body.dashboard_id))
        if (!targetDash) return this.json(route, 404, { detail: 'No such dashboard.' })
        if (targetDash.target_schema !== tile.target_schema) {
          return this.json(route, 422, { detail: 'Cannot move tile to dashboard with different target_schema.' })
        }
        tile.dashboard_id = targetDash.dashboard_id
        tile.layout = null
      }
      tile.updated_at = this.now()
      return this.json(route, 200, { tile })
    }
    if (method === 'POST' && action === 'refresh') {
      if (tile.state !== 'needs_review' && tile.result) {
        tile.result = { ...tile.result, computed_at: this.now() }
        tile.state = 'ok'
        tile.last_error = null
      }
      return this.json(route, 200, { tile })
    }
    if (method === 'POST' && action === 'replace') {
      const sessionId = String(body.session_id ?? '')
      const turn = (this.sessions.get(sessionId) ?? []).find((t) => t.turn_index === Number(body.turn_index))
      if (!turn) return this.json(route, 404, { detail: 'No such conversation turn.' })
      tile.version += 1
      tile.source = { session_id: sessionId, turn_index: turn.turn_index }
      tile.sql = turn.executed_sql ?? ''
      const rows = turn.sql_rows_preview ?? []
      tile.chart = turn.chart ? { ...turn.chart, data: { values: rows } } : null
      tile.columns = turn.sql_columns
      tile.state = 'ok'
      tile.versions.push({ version: tile.version, created_at: this.now(), source: tile.source })
      tile.result = { rows, row_count: rows.length, truncated: false, computed_at: this.now(), version: tile.version }
      tile.updated_at = this.now()
      return this.json(route, 200, { tile })
    }
    if (method === 'POST' && action === 'confirm') {
      tile.version += 1
      tile.state = 'ok'
      tile.versions.push({ version: tile.version, created_at: this.now(), source: tile.source })
      if (tile.result) tile.result = { ...tile.result, computed_at: this.now(), version: tile.version }
      return this.json(route, 200, { tile })
    }
    return this.json(route, 404, { detail: 'Not Found' })
  }
}
