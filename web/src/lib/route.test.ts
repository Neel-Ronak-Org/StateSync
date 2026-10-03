import { describe, expect, it } from 'vitest'

import { hashFor, routeFromHash } from './route'

describe('routes', () => {
  it('knows the library', () => {
    expect(routeFromHash('#/library')).toBe('library')
    expect(hashFor('library')).toBe('#/library')
  })
  it('keeps the older routes', () => {
    expect(routeFromHash('#/chat')).toBe('ask')
    expect(routeFromHash('#/workspaces/ws-1')).toBe('workspaces')
    expect(routeFromHash('')).toBe('dashboards')
  })
})
