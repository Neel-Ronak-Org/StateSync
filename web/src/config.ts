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
