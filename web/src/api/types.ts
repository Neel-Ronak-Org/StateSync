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
  /** Datasets whose data keeps changing (Phase 2b): dashboards on them poll and default to fast refresh. */
  live_schemas?: string[]
}

export type Route = 'sql' | 'docs' | 'hybrid'

export interface Citation {
  /** 'sql': ref is the executed statement. 'chunk': ref is a document chunk id. */
  kind: 'sql' | 'chunk'
  ref: string
  page?: number | null
  /** The document's title, when the server knows it (Phase 2b). */
  title?: string | null
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

/** How a tile is drawn (migration 019's tiles_viz_ck); null = decided from the rows. */
export type VizKindName =
  | 'kpi' | 'kpi_trend' | 'line' | 'area' | 'bar' | 'hbar'
  | 'donut' | 'breakdown' | 'heatmap' | 'scatter' | 'table'

export interface TileLayout {
  x: number
  y: number
  w: number
  h: number
}

export interface Tile {
  tile_id: string
  dashboard_id: string
  /** The dataset of the turn it was pinned from. */
  target_schema: string
  viz: VizKindName | null
  layout: TileLayout | null
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
  dashboard_id?: string
  viz?: VizKindName
  layout?: TileLayout
}

export interface UpdateTileRequest {
  title?: string
  refresh_interval_s?: number
  /** A kind, or 'auto' to decide from the rows again. */
  viz?: VizKindName | 'auto'
  /** Move to another dashboard of the same dataset. */
  dashboard_id?: string
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

export interface LayoutItem extends TileLayout {
  tile_id: string
}

export type BuildItemState = 'queued' | 'running' | 'added' | 'skipped'

export interface BuildItem {
  title: string
  question: string
  viz: VizKindName | null
  state: BuildItemState
  /** Why it was skipped, in words a person can act on. */
  reason: string | null
  tile_id: string | null
}

export interface BuildJob {
  job_id: string
  dashboard_id: string
  state: 'planning' | 'running' | 'done' | 'failed'
  goal: string | null
  items: BuildItem[]
  /** Why the whole build failed (planning could not produce insights). */
  error: string | null
  started_at: string
  finished_at: string | null
}
