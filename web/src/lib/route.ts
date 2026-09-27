/**
 * Hash routes (web/DESIGN.md §4): #/dashboards (the home) and #/ask. Links from
 * the Phase 2 app (#/dashboard, #/chat) still land in the right place.
 */

export type AppRoute = 'dashboards' | 'ask'

export function routeFromHash(hash: string): AppRoute {
  const path = hash.replace(/^#\/?/, '').split('/')[0]
  if (path === 'ask' || path === 'chat') return 'ask'
  return 'dashboards'
}

export function hashFor(route: AppRoute): string {
  return `#/${route}`
}
