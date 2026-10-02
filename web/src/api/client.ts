/**
 * The only way the app talks to the orchestrator.
 *
 * - Adds the bearer token; never puts it anywhere but the Authorization header.
 * - Turns every failure into an ApiError whose message is safe to show: the
 *   server's `detail` (plain text by design) or a fixed sentence. Raw
 *   exception text from fetch never reaches the UI.
 * - A 401 announces that the session expired, so the app can return to the
 *   sign-in screen from wherever the request was made.
 */

import type {
  BuildJob,
  Connection,
  ConnectionCheck,
  NewConnection,
  CatalogEntry,
  ExampleCheckResult,
  ExampleInfo,
  Dashboard,
  Definition,
  Health,
  LayoutItem,
  LibraryDocument,
  LibraryFolder,
  PinExampleResult,
  PinTileRequest,
  QueryRequest,
  QueryResponse,
  SessionDetail,
  SessionList,
  SetupJob,
  SetupRun,
  Tile,
  TurnRows,
  UpdateTileRequest,
  Workspace,
} from '@/api/types'
import { ORCHESTRATOR_URL } from '@/config'
import { announceAuthExpired, getToken } from '@/lib/auth'

export class ApiError extends Error {
  readonly status: number
  readonly retryAfterSeconds: number | null

  constructor(status: number, message: string, retryAfterSeconds: number | null = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.retryAfterSeconds = retryAfterSeconds
  }
}

type Method = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'

function detailText(body: unknown): string | null {
  if (!body || typeof body !== 'object') return null
  const detail = (body as { detail?: unknown }).detail
  if (typeof detail === 'string') return detail
  // FastAPI request-validation errors: [{loc, msg, type}, ...]
  if (Array.isArray(detail)) {
    const messages = detail
      .map((d) => (d && typeof d === 'object' ? (d as { msg?: unknown }).msg : null))
      .filter((m): m is string => typeof m === 'string')
    if (messages.length) return messages.join('; ')
  }
  return null
}

export async function apiFetch<T>(
  path: string,
  options: {
    method?: Method
    body?: unknown
    /** A raw file body (uploads): sent as is with its own Content-Type and X-Filename. */
    upload?: { file: Blob; contentType: string; filename: string }
    signal?: AbortSignal
    auth?: boolean
  } = {},
): Promise<T> {
  const { method = 'GET', body, upload, signal, auth = true } = options
  const headers: Record<string, string> = {}
  if (upload) {
    headers['Content-Type'] = upload.contentType
    headers['X-Filename'] = encodeURIComponent(upload.filename)
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
  }
  const token = auth ? getToken() : null
  if (token) headers.Authorization = `Bearer ${token}`

  let response: Response
  try {
    response = await fetch(`${ORCHESTRATOR_URL}${path}`, {
      method,
      headers,
      body: upload ? upload.file : body === undefined ? undefined : JSON.stringify(body),
      signal,
    })
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') throw err
    throw new ApiError(0, `Can't reach the StateSync API at ${ORCHESTRATOR_URL}. Is the stack running?`)
  }

  let parsed: unknown = null
  const text = await response.text()
  if (text) {
    try {
      parsed = JSON.parse(text)
    } catch {
      parsed = null
    }
  }

  if (response.ok) return parsed as T

  if (response.status === 401) {
    announceAuthExpired()
    throw new ApiError(401, 'Your session expired or the token is invalid. Sign in again.')
  }
  if (response.status === 429) {
    const header = response.headers.get('Retry-After')
    const seconds = header && /^\d+$/.test(header) ? Number(header) : null
    throw new ApiError(
      429,
      seconds ? `Rate limit reached. Try again in ${seconds} seconds.` : 'Rate limit reached. Try again shortly.',
      seconds,
    )
  }
  throw new ApiError(response.status, detailText(parsed) ?? `Request failed (HTTP ${response.status}).`)
}

const enc = encodeURIComponent

export const api = {
  health: () => apiFetch<Health>('/health', { auth: false }),

  query: (request: QueryRequest, signal?: AbortSignal) =>
    apiFetch<QueryResponse>('/query', { method: 'POST', body: request, signal }),

  sessions: () => apiFetch<SessionList>('/sessions'),
  session: (sessionId: string) => apiFetch<SessionDetail>(`/sessions/${enc(sessionId)}`),
  turnRows: (sessionId: string, turnIndex: number) =>
    apiFetch<TurnRows>(`/sessions/${enc(sessionId)}/turns/${enc(String(turnIndex))}/rows`),

  tiles: (dashboardId?: string) =>
    apiFetch<{ tiles: Tile[] }>(dashboardId ? `/tiles?dashboard_id=${enc(dashboardId)}` : '/tiles'),
  tile: (tileId: string) => apiFetch<{ tile: Tile }>(`/tiles/${enc(tileId)}`),
  pinTile: (request: PinTileRequest) =>
    apiFetch<{ tile: Tile; example?: PinExampleResult }>('/tiles', { method: 'POST', body: request }),
  refreshTile: (tileId: string) => apiFetch<{ tile: Tile }>(`/tiles/${enc(tileId)}/refresh`, { method: 'POST' }),
  replaceTile: (tileId: string, sessionId: string, turnIndex: number) =>
    apiFetch<{ tile: Tile }>(`/tiles/${enc(tileId)}/replace`, {
      method: 'POST',
      body: { session_id: sessionId, turn_index: turnIndex },
    }),
  updateTile: (tileId: string, request: UpdateTileRequest) =>
    apiFetch<{ tile: Tile }>(`/tiles/${enc(tileId)}`, { method: 'PATCH', body: request }),
  confirmTile: (tileId: string) => apiFetch<{ tile: Tile }>(`/tiles/${enc(tileId)}/confirm`, { method: 'POST' }),
  deleteTile: (tileId: string) => apiFetch<{ deleted: string }>(`/tiles/${enc(tileId)}`, { method: 'DELETE' }),

  deleteSession: (sessionId: string) =>
    apiFetch<{ deleted: string; deleted_turns: number }>(`/sessions/${enc(sessionId)}`, { method: 'DELETE' }),

  dashboards: (schema?: string) =>
    apiFetch<{ dashboards: Dashboard[] }>(schema ? `/dashboards?schema=${enc(schema)}` : '/dashboards'),
  createDashboard: (name: string, targetSchema: string) =>
    apiFetch<{ dashboard: Dashboard }>('/dashboards', { method: 'POST', body: { name, target_schema: targetSchema } }),
  renameDashboard: (dashboardId: string, name: string) =>
    apiFetch<{ dashboard: Dashboard }>(`/dashboards/${enc(dashboardId)}`, { method: 'PATCH', body: { name } }),
  deleteDashboard: (dashboardId: string) =>
    apiFetch<{ deleted: string }>(`/dashboards/${enc(dashboardId)}`, { method: 'DELETE' }),
  saveLayout: (dashboardId: string, items: LayoutItem[]) =>
    apiFetch<{ dashboard_id: string; updated: number }>(`/dashboards/${enc(dashboardId)}/layout`, {
      method: 'PUT',
      body: { items },
    }),
  buildDashboard: (dashboardId: string, goal?: string) =>
    apiFetch<{ job: BuildJob }>(`/dashboards/${enc(dashboardId)}/build`, {
      method: 'POST',
      body: goal ? { goal } : {},
    }),
  buildStatus: (dashboardId: string) =>
    apiFetch<{ job: BuildJob | null }>(`/dashboards/${enc(dashboardId)}/build`),

  workspaces: () => apiFetch<{ workspaces: Workspace[] }>('/workspaces'),
  createWorkspace: (body: { name: string; target_schema: string; business_context?: string }) =>
    apiFetch<{ workspace: Workspace }>('/workspaces', { method: 'POST', body }),
  workspace: (workspaceId: string) =>
    apiFetch<{ workspace: Workspace }>(`/workspaces/${enc(workspaceId)}`),
  updateWorkspace: (workspaceId: string, body: { name?: string; business_context?: string }) =>
    apiFetch<{ workspace: Workspace }>(`/workspaces/${enc(workspaceId)}`, { method: 'PATCH', body }),
  deleteWorkspace: (workspaceId: string) =>
    apiFetch<{ deleted: string }>(`/workspaces/${enc(workspaceId)}`, { method: 'DELETE' }),

  workspaceCatalog: (workspaceId: string) =>
    apiFetch<{ columns: CatalogEntry[] }>(`/workspaces/${enc(workspaceId)}/catalog`),
  updateDescriptions: (
    workspaceId: string,
    items: { table_name: string; column_name: string; description: string | null }[],
  ) =>
    apiFetch<{ updated: number }>(`/workspaces/${enc(workspaceId)}/descriptions`, {
      method: 'PUT',
      body: { items },
    }),

  workspaceDefinitions: (workspaceId: string) =>
    apiFetch<{ definitions: Definition[] }>(`/workspaces/${enc(workspaceId)}/definitions`),
  updateDefinitions: (
    workspaceId: string,
    items: { term: string; definition: string }[],
  ) =>
    apiFetch<{ definitions: Definition[] }>(`/workspaces/${enc(workspaceId)}/definitions`, {
      method: 'PUT',
      body: { items },
    }),
  deleteDefinition: (workspaceId: string, definitionId: string) =>
    apiFetch<{ deleted: string }>(
      `/workspaces/${enc(workspaceId)}/definitions/${enc(definitionId)}`,
      { method: 'DELETE' },
    ),

  addExample: (workspaceId: string, body: { question: string; sql: string }) =>
    apiFetch<{ example_id: string }>(`/workspaces/${enc(workspaceId)}/examples`, { method: 'POST', body }),
  removeExample: (workspaceId: string, question: string) =>
    apiFetch<{ removed: number }>(`/workspaces/${enc(workspaceId)}/examples`, {
      method: 'DELETE',
      body: { question },
    }),

  workspaceExamples: (workspaceId: string) =>
    apiFetch<{ examples: ExampleInfo[] }>(`/workspaces/${enc(workspaceId)}/examples`),
  checkExamples: (workspaceId: string) =>
    apiFetch<ExampleCheckResult>(`/workspaces/${enc(workspaceId)}/examples/check`, { method: 'POST' }),

  approveWorkspace: (workspaceId: string) =>
    apiFetch<{ workspace: Workspace; approved_columns: number; approved_definitions: number }>(
      `/workspaces/${enc(workspaceId)}/approve`,
      { method: 'POST' },
    ),

  startDraft: (workspaceId: string) =>
    apiFetch<{ job: SetupJob }>(`/workspaces/${enc(workspaceId)}/draft`, { method: 'POST' }),
  draftStatus: (workspaceId: string) =>
    apiFetch<{ job: SetupJob | null }>(`/workspaces/${enc(workspaceId)}/draft`),
  startMining: (workspaceId: string) =>
    apiFetch<{ job: SetupJob }>(`/workspaces/${enc(workspaceId)}/definitions/mine`, { method: 'POST' }),
  miningStatus: (workspaceId: string) =>
    apiFetch<{ job: SetupJob | null }>(`/workspaces/${enc(workspaceId)}/definitions/mine`),

  startSetup: (workspaceId: string) =>
    apiFetch<{ job: SetupJob; graded_by: 'reference' | 'owner' }>(
      `/workspaces/${enc(workspaceId)}/setup`,
      { method: 'POST' },
    ),
  setupStatus: (workspaceId: string) =>
    apiFetch<{ run: SetupRun | null; job: SetupJob | null }>(
      `/workspaces/${enc(workspaceId)}/setup`,
    ),
  setResultVerdict: (
    workspaceId: string,
    runId: string,
    position: number,
    verdict: 'right' | 'wrong',
  ) =>
    apiFetch<{ run: SetupRun }>(
      `/workspaces/${enc(workspaceId)}/setup/${enc(runId)}/results/${enc(String(position))}`,
      { method: 'PUT', body: { verdict } },
    ),

  connections: () =>
    apiFetch<{ connections: Connection[]; available: boolean }>('/library/connections'),
  createConnection: (body: NewConnection) =>
    apiFetch<ConnectionCheck>('/library/connections', { method: 'POST', body }),
  setConnectionSchema: (connectionId: string, schemaName: string) =>
    apiFetch<{ connection: Connection }>(`/library/connections/${enc(connectionId)}/schema`, {
      method: 'PUT',
      body: { schema_name: schemaName },
    }),
  testConnection: (connectionId: string) =>
    apiFetch<ConnectionCheck>(`/library/connections/${enc(connectionId)}/test`, { method: 'POST' }),
  deleteConnection: (connectionId: string) =>
    apiFetch<{ deleted: number }>(`/library/connections/${enc(connectionId)}`, { method: 'DELETE' }),

  libraryFolders: () => apiFetch<{ folders: LibraryFolder[] }>('/library/folders'),
  createFolder: (name: string) =>
    apiFetch<{ folder: LibraryFolder }>('/library/folders', { method: 'POST', body: { name } }),
  renameFolder: (folderId: string, name: string) =>
    apiFetch<{ folder: LibraryFolder }>(`/library/folders/${enc(folderId)}`, { method: 'PATCH', body: { name } }),
  deleteFolder: (folderId: string) =>
    apiFetch<{ deleted: number }>(`/library/folders/${enc(folderId)}`, { method: 'DELETE' }),
  libraryDocuments: (folderId: string) =>
    apiFetch<{ documents: LibraryDocument[] }>(`/library/documents?folder_id=${enc(folderId)}`),
  uploadDocument: (folderId: string, file: File, contentType: string) =>
    apiFetch<{ document: LibraryDocument }>(`/library/folders/${enc(folderId)}/documents`, {
      method: 'POST',
      upload: { file, contentType, filename: file.name },
    }),
  deleteDocument: (documentId: string) =>
    apiFetch<{ deleted: number }>(`/library/documents/${enc(documentId)}`, { method: 'DELETE' }),

  workspaceFolders: (workspaceId: string) =>
    apiFetch<{ folder_ids: string[] }>(`/workspaces/${enc(workspaceId)}/folders`),
  setWorkspaceFolders: (workspaceId: string, folderIds: string[]) =>
    apiFetch<{ folder_ids: string[] }>(`/workspaces/${enc(workspaceId)}/folders`, {
      method: 'PUT',
      body: { folder_ids: folderIds },
    }),
}
