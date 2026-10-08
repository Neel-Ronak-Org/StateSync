/**
 * Runtime configuration.
 *
 * The orchestrator URL is an address the USER'S browser must reach, and it
 * differs per deployment, so it is not baked into the bundle. The container's
 * entrypoint writes /config.js (window.__STATESYNC_CONFIG__) at start-up, and
 * index.html loads it before the app. It is a same-origin file, so the CSP's
 * script-src 'self' covers it with no inline script. Local `npm run dev` falls
 * back to the orchestrator's published dev port.
 */

interface RuntimeConfig {
  orchestratorUrl?: string
  /** Optional sample questions for the chat's empty state (a deployment's own, e.g. a demo's). */
  suggestions?: unknown
}

declare global {
  interface Window {
    __STATESYNC_CONFIG__?: RuntimeConfig
  }
}

const DEV_FALLBACK = 'http://localhost:8090'

export const ORCHESTRATOR_URL: string = (
  window.__STATESYNC_CONFIG__?.orchestratorUrl || DEV_FALLBACK
).replace(/\/+$/, '')

const MAX_SUGGESTIONS = 8

/** The deployment's sample questions, when its config.js supplies a list of strings; otherwise null. */
export const RUNTIME_SUGGESTIONS: string[] | null = (() => {
  const raw = window.__STATESYNC_CONFIG__?.suggestions
  if (!Array.isArray(raw)) return null
  const list = raw.filter((q): q is string => typeof q === 'string' && q.trim().length > 0).slice(0, MAX_SUGGESTIONS)
  return list.length ? list : null
})()
