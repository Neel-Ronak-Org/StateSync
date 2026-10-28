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
  llm_ready: boolean
  llm_detail: string | null
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

/** One model call made while answering, in call order (developer mode shows these). */
export interface ModelCall {
  role: string
  model: string
  tokens_in?: number
  tokens_out?: number
  cached_tokens?: number
  retries?: number
  waited_ms?: number
  ms: number
  rate_remaining_tokens?: number | null
  /** Set instead of the token fields when the call failed. */
  error?: string
}

export interface GroundedFigure {
  text: string
  start: number
  end: number
  matched: boolean
  source: { kind: 'cell' | 'text' | 'aggregate' | 'question' | 'passage' | 'derived'; row?: number; column?: string; aggregate?: string } | null
}

export interface NumberGrounding {
  figures: GroundedFigure[]
  matched: number
  unmatched: string[]
}

export interface StateTrace {
  query_id: string
  target_schema: string
  workspace_id?: string | null
  verified_example?: boolean
  /** How much to trust the answer (orchestrator/evidence.py); absent or null when no SQL ran. */
  evidence?: 'verified' | 'unchecked' | null
  route: Route | null
  executed_sql: string | null
  /** Plain-English reading of the executed SQL ("How I got this"); absent or null when none. */
  explanation?: string | null
  candidate_sql: string | null
  sql_rows_count: number
  sql_rows: Row[]
  sql_columns: string[]
  attempt: number
  errors: unknown[]
  /** Warnings shown under the answer: a figure found nowhere, or a query that needed a correction. */
  caveats?: string[]
  /** Every figure in the prose traced to its source (number_grounding.py). */
  number_grounding?: NumberGrounding | null
  metrics: TraceMetrics
  normalized_query: string | null
  /** The stored turn number, or null when the turn was not recorded. */
  turn_index: number | null
  rewrite_applied: boolean
  subtasks: string[]
  retrievals: Retrieval[]
  calls?: ModelCall[]
  [key: string]: unknown
}

export interface QueryRequest {
  query: string
  session_id?: string
  /** Every question is asked in a workspace (its database, documents and approved knowledge). */
  workspace_id?: string
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
  /** Pinned in a workspace: the answer also becomes its approved example (tile_tools.py, property 6). */
  workspace_id?: string
}

/** What a workspace pin did with the approved example; absent when no workspace was sent. */
export interface PinExampleResult {
  saved: boolean
  example_id?: string
  reason?: string
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

export type SetupState = 'created' | 'scanned' | 'drafted' | 'approved' | 'tested'

export interface Workspace {
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
  counts: {
    tables: number
    approved: number
    definitions: number
    examples: number
  }
}

/** An approved answer: a question whose SQL the owner marked correct. */
export interface ExampleInfo {
  example_id: string
  question: string
  sql_text: string
  source: string
}

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

export type LibraryDocumentStatus = 'processing' | 'ready' | 'failed'

export interface LibraryFolder {
  folder_id: string
  name: string
  created_at: string
  document_count: number
  ready_count: number
  workspace_ids: string[]
}

export interface LibraryDocument {
  document_id: string
  folder_id: string
  title: string
  filename: string
  content_type: string
  pages: number | null
  bytes: number
  status: LibraryDocumentStatus
  error: string | null
  chunk_count: number
  created_at: string
}

export type ConnectionStatus = 'ok' | 'failed'

export type SslMode = 'disable' | 'prefer' | 'require' | 'verify-ca' | 'verify-full'

/** A Postgres database the user connected (never carries the password). */
export interface Connection {
  connection_id: string
  name: string
  host: string
  port: number
  dbname: string
  username: string
  sslmode: SslMode | string
  schema_name: string | null
  status: ConnectionStatus
  check_detail: string | null
  checked_at: string | null
  created_at: string
  workspace_ids: string[]
}

/** The body of POST /library/connections. The password is sent once and never returned. */
export interface NewConnection {
  name: string
  host: string
  port: number
  dbname: string
  username: string
  password: string
  sslmode: SslMode
}

/** What a create or a test returns: the connection and the schemas the login can read. */
export interface ConnectionCheck {
  connection: Connection
  schemas: string[]
  warnings: string[]
}

// ── Pipeline Inspector ───────────────────────────────────────────────────────

/** One candidate passage of the documents search, with every score it has (no text). */
export interface InspectPassage {
  chunk_id: string
  document_id: string
  title: string | null
  page: number | null
  score: number
  jev_score: number | null
  /** Its place in the list the search returned (before Jev re-ordered it), from 1. */
  rank_searched?: number | null
  /** Its place in the list the answer writer was given, from 1. */
  rank_final?: number
}

/** state_trace.retrieval_trace: the documents search stage by stage. */
export interface RetrievalTrace {
  search_text: string
  settings: {
    /** Passages the answer writer reads. */
    window: number
    /** Candidates round 2 returns (the ones Jev re-ranks). */
    candidates?: number
  }
  round1: InspectPassage[]
  narrowed_to: string[]
  passages: InspectPassage[]
  jev: { scored: number; failed: number; ms: number } | null
  ms: { search: number; jev: number }
}

/** state_trace.front_door: what the router read and decided. */
export interface FrontDoorTrace {
  arm: string
  route: string
  standalone_question?: string
  database_question?: string | null
  documents_question?: string | null
  documents?: { title: string; score: number }[]
  card?: string
  card_status?: string
  latency_ms?: number
  history_turns?: number
  jev?: {
    probabilities?: Record<string, number>
    choice?: string
    model?: string
    ms?: number
    fallback?: string
    jev_error?: string
    [key: string]: unknown
  } | null
  [key: string]: unknown
}

export interface SchemaColumn {
  name: string
  type: string | null
  nullable: boolean
  foreign_key: boolean
}

export interface SchemaTable {
  name: string
  row_estimate: number | null
  columns: SchemaColumn[]
}

export interface SchemaEdge {
  from_table: string
  from_column: string
  to_table: string
  to_column: string | null
}

/** GET /workspaces/{id}/schema-graph */
export interface SchemaGraph {
  dataset: string
  tables: SchemaTable[]
  edges: SchemaEdge[]
  tables_total: number
}
