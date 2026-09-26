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
  Health,
  PinTileRequest,
  QueryRequest,
  QueryResponse,
  SessionDetail,
  SessionList,
  Tile,
  TurnRows,
  UpdateTileRequest,
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

type Method = 'GET' | 'POST' | 'PATCH' | 'DELETE'

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
  options: { method?: Method; body?: unknown; signal?: AbortSignal; auth?: boolean } = {},
): Promise<T> {
  const { method = 'GET', body, signal, auth = true } = options
  const headers: Record<string, string> = {}
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  const token = auth ? getToken() : null
  if (token) headers.Authorization = `Bearer ${token}`

  let response: Response
  try {
    response = await fetch(`${ORCHESTRATOR_URL}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
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

  tiles: () => apiFetch<{ tiles: Tile[] }>('/tiles'),
  tile: (tileId: string) => apiFetch<{ tile: Tile }>(`/tiles/${enc(tileId)}`),
  pinTile: (request: PinTileRequest) => apiFetch<{ tile: Tile }>('/tiles', { method: 'POST', body: request }),
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
}
