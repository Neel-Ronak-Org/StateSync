/**
 * Browser sign-in state.
 *
 * The API token is NOT kept in the browser any more (2026-10-02). Signing in sends it once to
 * POST /auth/session, which sets an HttpOnly, SameSite=Strict cookie that page scripts cannot
 * read (an XSS could read localStorage). What is stored here is only a non-secret marker, the
 * principal id, so a reload knows to show the app rather than the sign-in screen; the server
 * decides whether the session is still valid.
 *
 * A token left in localStorage by an older version is exchanged for a session once and then
 * deleted (takeLegacyToken).
 *
 * Any 401 from the API fires AUTH_EXPIRED_EVENT; the app listens, clears the marker and shows
 * the sign-in screen with an explanation.
 */

import { KEYS, readStored, writeStored } from '@/lib/storage'

export const AUTH_EXPIRED_EVENT = 'statesync:auth-expired'
/** Sent on every API call: the server accepts the session cookie only with it (CSRF guard). */
export const CSRF_HEADER = 'X-StateSync'

export function isSignedIn(): boolean {
  return Boolean(readStored(KEYS.signedIn))
}

export function markSignedIn(principalId: string): void {
  writeStored(KEYS.signedIn, principalId || 'signed-in')
}

export function clearSignedIn(): void {
  writeStored(KEYS.signedIn, null)
}

/** A token stored by an older version, removed from storage as it is read. */
export function takeLegacyToken(): string | null {
  const token = readStored(KEYS.token)
  if (token) writeStored(KEYS.token, null)
  return token
}

export function announceAuthExpired(): void {
  window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT))
}
