/**
 * Response shapes of the orchestrator API (orchestrator/main.py,
 * orchestrator/tiles_api.py, mcp/tools/history_tools.py, tile_tools.py).
 *
 * Written from the server code, not guessed: if a field changes there, change
 * it here. Fields the UI does not use are typed loosely (unknown) rather than
 * omitted, so the developer panel can still show them.
 */

export type Row = Record<string, unknown>

/** A Vega-Lite v5 spec that has passed the server's A7 validation. */
export type ChartSpec = Record<string, unknown>

export interface Health {
  status: string
  service: string
  llm_provider: string
  llm_ready: boolean
  llm_detail: string | null
  default_schema: string
  available_schemas: string[]
}

export type Route = 'sql' | 'docs' | 'hybrid'

export interface Citation {
  /** 'sql': ref is the executed statement. 'chunk': ref is a document chunk id. */
  kind: 'sql' | 'chunk'
  ref: string
  page?: number | null
}

export interface Retrieval {
  content?: string
  document_id?: string
  chunk_id?: string
  page?: number | null
  score?: number | null
  [key: string]: unknown
}

export interface TraceMetrics {
  tokens_in?: number
  tokens_out?: number
  llm_calls?: number
  latency_ms?: number
  [key: string]: unknown
}

export interface StateTrace {
  query_id: string
  target_schema: string
  route: Route | null
  executed_sql: string | null
  candidate_sql: string | null
  sql_rows_count: number
  sql_rows: Row[]
  sql_columns: string[]
  attempt: number
  errors: unknown[]
  metrics: TraceMetrics
  memory_hit: boolean
  detected_language: string | null
  normalized_query: string | null
  preserved_entities: Record<string, string>
  /** The stored turn number, or null when the turn was not recorded. */
  turn_index: number | null
  rewrite_applied: boolean
  context_guessed: boolean
  subtasks: string[]
  retrievals: Retrieval[]
  [key: string]: unknown
}

export interface QueryRequest {
  query: string
  session_id?: string
  schema?: string
}

export interface QueryResponse {
  query_id: string
  answer_text: string
  chart: ChartSpec | null
  citations: Citation[]
  state_trace: StateTrace
  latency_ms: number
}

export interface SessionSummary {
  session_id: string
  turn_count: number
  started_at: string
  last_activity: string
  first_query: string
  /** Dataset the conversation was started on; null for turns stored before migration 018. */
  target_schema: string | null
}

export interface SessionList {
  sessions: SessionSummary[]
  count: number
}

/** One stored turn, as history replays it (no full rows; see TurnRows). */
export interface StoredTurn {
  turn_index: number
  raw_query: string
  rewritten_query: string
  rewrite_applied: boolean
  context_guessed: boolean
  route: Route | null
  executed_sql: string | null
  answer_text: string
  computed_at: string
  chart: ChartSpec | null
  sql_columns: string[] | null
  sql_rows_preview: Row[] | null
  /** Rows the query returned (capped at run_select's max_rows), not the preview size. */
  sql_rows_count: number | null
  /** Dataset this turn was answered from; null before migration 018. */
  target_schema: string | null
}

export interface SessionDetail {
  turns: StoredTurn[]
  count: number
  next_turn_index?: number
}

export interface TurnRows {
  rows: Row[]
  stored_row_count: number
  byte_size: number
  /** false = pruned or never stored; fall back to the preview. */
  available: boolean
}

export type TileState = 'ok' | 'needs_review' | 'error'

export interface TileResult {
  rows: Row[]
  row_count: number
  truncated: boolean
  computed_at: string
  version: number
}

export interface TileVersion {
  version: number
  created_at: string
  source: { session_id: string; turn_index: number }
}

export interface Tile {
  tile_id: string
  title: string
  state: TileState
  version: number
  refresh_interval_s: number
  created_at: string
  updated_at: string
  sql: string
  /** Bound by the server: data.values holds result.rows. */
  chart: ChartSpec | null
  columns: string[] | null
  schemas: string[]
  source: { session_id: string; turn_index: number }
  result: TileResult | null
  stale: boolean
  last_error: string | null
  error_at: string | null
  versions?: TileVersion[]
}

export interface PinTileRequest {
  session_id: string
  turn_index: number
  title?: string
  refresh_interval_s?: number
}

export interface UpdateTileRequest {
  title?: string
  refresh_interval_s?: number
}
