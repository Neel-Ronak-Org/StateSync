/**
 * The API bearer token.
 *
 * Kept in localStorage so a reload does not sign the user out, through
 * storage.ts so blocked storage degrades to memory instead of crashing. The
 * token is only ever sent as an Authorization header to the orchestrator; it
 * is never put in a URL, logged, or rendered.
 *
 * Any 401 from the API fires AUTH_EXPIRED_EVENT; the app listens, clears the
 * token and shows the sign-in screen with an explanation.
 */

import { KEYS, readStored, writeStored } from '@/lib/storage'

export const AUTH_EXPIRED_EVENT = 'statesync:auth-expired'

export function getToken(): string | null {
  return readStored(KEYS.token)
}

export function setToken(token: string): void {
  writeStored(KEYS.token, token.trim())
}

export function clearToken(): void {
  writeStored(KEYS.token, null)
}

export function announceAuthExpired(): void {
  window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT))
}
