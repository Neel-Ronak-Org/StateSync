/**
 * Hash routes (web/DESIGN.md §4): #/dashboards (the home) and #/ask. Links from
 * the Phase 2 app (#/dashboard, #/chat) still land in the right place.
 */

export type AppRoute = 'dashboards' | 'ask' | 'workspaces'

export function routeFromHash(hash: string): AppRoute {
  const path = hash.replace(/^#\/?/, '').split('/')[0]
  if (path === 'ask' || path === 'chat') return 'ask'
  if (path === 'workspaces') return 'workspaces'
  return 'dashboards'
}

export function workspaceIdFromHash(hash: string): string | null {
  const parts = hash.replace(/^#\/?/, '').split('/')
  if (parts[0] === 'workspaces' && parts[1]) {
    return parts[1]
  }
  return null
}

export function hashFor(route: AppRoute, workspaceId?: string): string {
  if (route === 'workspaces' && workspaceId) {
    return `#/workspaces/${workspaceId}`
  }
  return `#/${route}`
}

export function workspaceHash(workspaceId: string): string {
  return `#/workspaces/${workspaceId}`
}
