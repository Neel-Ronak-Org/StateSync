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

export const TOKEN = 'test-token-for-mocked-suite'
export const API = 'http://localhost:8090'
const SCHEMA_URL = 'https://vega.github.io/schema/vega-lite/v5.json'

type Row = Record<string, unknown>

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

interface Tile {
  tile_id: string
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
    chart.transform = [{ calculate: "datum.n * 2", as: 'double' }]
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
  private failures = new Map<string, { status: number; detail: string; headers?: Record<string, string> }>()
  /** When set, the full-rows endpoint returns only this many rows (the history store's caps). */
  storedRowsLimit: number | null = null
  private clock = Date.parse('2026-09-27T10:00:00Z')
  private tileSeq = 0

  constructor() {
    // A past conversation for the history sidebar, whose second turn has a
    // truncated preview (20 of 60 rows) so "Load full result" can be tested.
    const at = new Date(this.clock - 3_600_000).toISOString()
    this.sessions.set('seed-session-1', [
      {
        turn_index: 1, raw_query: 'How many orders are there in total?', rewritten_query: 'How many orders are there in total?',
        rewrite_applied: false, context_guessed: false, route: 'sql',
        executed_sql: 'SELECT COUNT(*) AS total_orders FROM orders LIMIT 1000', answer_text: 'There are 550 orders in total.',
        computed_at: at, chart: null, sql_columns: ['total_orders'], sql_rows_preview: [{ total_orders: 550 }], sql_rows_count: 1,
        target_schema: 'public',
      },
      {
        turn_index: 2, raw_query: 'Which cities have the most sellers?', rewritten_query: 'Which cities have the most sellers?',
        rewrite_applied: false, context_guessed: false, route: 'sql',
        executed_sql: 'SELECT city, count(*) AS sellers FROM sellers GROUP BY 1 ORDER BY 2 DESC',
        answer_text: 'There are 60 cities with at least one seller.', computed_at: at, chart: null,
        sql_columns: ['city', 'sellers'], sql_rows_preview: MANY_ROWS.slice(0, 20), sql_rows_count: 60,
        target_schema: 'public',
      },
    ])
    // A conversation on the OTHER dataset, to test that opening it switches dataset.
    this.sessions.set('seed-session-olist', [
      {
        turn_index: 1, raw_query: 'How many orders were delivered on olist?', rewritten_query: 'How many orders were delivered on olist?',
        rewrite_applied: false, context_guessed: false, route: 'sql',
        executed_sql: "SELECT count(*) AS delivered FROM olist.orders WHERE order_status = 'delivered'",
        answer_text: 'There were 96,478 orders delivered.', computed_at: at, chart: null,
        sql_columns: ['delivered'], sql_rows_preview: [{ delivered: 96478 }], sql_rows_count: 1, target_schema: 'olist',
      },
    ])
  }

  /** The next request to `path` (e.g. '/query') fails once with this status. */
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
      // Mirrors the orchestrator's CORS: Retry-After is only readable cross-origin
      // because the server lists it in Access-Control-Expose-Headers.
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
          'access-control-allow-methods': 'GET, POST, PATCH, DELETE',
          'access-control-allow-headers': 'authorization, content-type',
        },
      })
    }

    if (path === '/health') {
      return this.json(route, 200, {
        status: 'ok', service: 'orchestrator', llm_provider: 'groq', llm_ready: true, llm_detail: null,
        default_schema: 'public', available_schemas: ['olist', 'public'],
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
        session_id, turn_count: turns.length, started_at: turns[0].computed_at,
        last_activity: turns[turns.length - 1].computed_at, first_query: turns[0].raw_query,
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
    if (m && method === 'GET') {
      const turns = this.sessions.get(decodeURIComponent(m[1])) ?? []
      return this.json(route, 200, { turns, count: turns.length, next_turn_index: turns.length + 1 })
    }
    if (path.startsWith('/tiles')) return this.tilesRoute(route, method, path, body)
    return this.json(route, 404, { detail: 'Not Found' })
  }

  private query(route: Route, body: Record<string, unknown>) {
    this.queries.push(body)
    const question = String(body.query ?? '')
    const sessionId = String(body.session_id ?? `srv-${this.queries.length}`)
    const canned = cannedAnswer(question)
    const turns = this.sessions.get(sessionId) ?? []
    const schema = String(body.schema ?? 'public')
    // The server refuses a follow-up on a different dataset (migration 018).
    const startedOn = turns[0]?.target_schema
    if (startedOn && startedOn !== schema) {
      return this.json(route, 409, {
        detail: `This conversation uses the ${startedOn} dataset. Start a new conversation to ask about ${schema}.`,
      })
    }
    const turnIndex = turns.length + 1
    const at = this.now()
    turns.push({
      turn_index: turnIndex, raw_query: question, rewritten_query: question, rewrite_applied: false, context_guessed: false,
      route: canned.route, executed_sql: canned.sql, answer_text: canned.answer, computed_at: at, chart: canned.chart,
      sql_columns: canned.columns.length ? canned.columns : null, sql_rows_preview: canned.rows.slice(0, 100),
      sql_rows_count: canned.rows.length, target_schema: schema,
    })
    this.sessions.set(sessionId, turns)
    return this.json(route, 200, {
      query_id: `q-${this.queries.length}`,
      answer_text: canned.answer,
      chart: canned.chart,
      citations: canned.citations,
      latency_ms: 1234,
      state_trace: {
        query_id: `q-${this.queries.length}`, target_schema: schema, route: canned.route,
        executed_sql: canned.sql, candidate_sql: canned.sql, sql_rows_count: canned.rows.length, sql_rows: canned.rows,
        sql_columns: canned.columns, attempt: 1, errors: [], metrics: { tokens_in: 5164, tokens_out: 298, llm_calls: 5 },
        memory_hit: false, detected_language: 'en', normalized_query: question, preserved_entities: {},
        turn_index: turnIndex, rewrite_applied: false, context_guessed: false, subtasks: [], retrievals: [],
      },
    })
  }

  private makeTile(turn: StoredTurn, sessionId: string, title?: string, interval?: number): Tile {
    const at = this.now()
    this.tileSeq += 1
    const rows = turn.sql_rows_preview ?? []
    const chart = turn.chart ? { ...turn.chart, data: { values: rows } } : null
    return {
      tile_id: `tile-${this.tileSeq}`, title: title || turn.raw_query, state: 'ok', version: 1,
      refresh_interval_s: interval ?? 3600, created_at: at, updated_at: at, sql: turn.executed_sql ?? '', chart,
      columns: turn.sql_columns, schemas: ['public'], source: { session_id: sessionId, turn_index: turn.turn_index },
      result: { rows, row_count: rows.length, truncated: false, computed_at: at, version: 1 }, stale: false,
      last_error: null, error_at: null, versions: [{ version: 1, created_at: at, source: { session_id: sessionId, turn_index: turn.turn_index } }],
    }
  }

  private tilesRoute(route: Route, method: string, path: string, body: Record<string, unknown>) {
    if (path === '/tiles' && method === 'GET') return this.json(route, 200, { tiles: [...this.tiles.values()] })
    if (path === '/tiles' && method === 'POST') {
      const sessionId = String(body.session_id ?? '')
      const turn = (this.sessions.get(sessionId) ?? []).find((t) => t.turn_index === Number(body.turn_index))
      if (!turn) return this.json(route, 404, { detail: 'No such conversation turn.' })
      if (!turn.executed_sql) return this.json(route, 422, { detail: 'Only answers computed from the database can be pinned.' })
      const tile = this.makeTile(turn, sessionId, body.title as string | undefined, body.refresh_interval_s as number | undefined)
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
      if (interval !== undefined && (typeof interval !== 'number' || interval < 60 || interval > 604800)) {
        return this.json(route, 422, { detail: 'refresh_interval_s must be a whole number of seconds between 60 and 604800.' })
      }
      if (typeof body.title === 'string') {
        if (!body.title.trim()) return this.json(route, 422, { detail: 'title must not be blank.' })
        tile.title = body.title.trim()
      }
      if (typeof interval === 'number') tile.refresh_interval_s = interval
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
