import { describe, expect, it } from 'vitest'

import { GRID_COLS, layoutsDiffer, placeTiles, resize } from '@/features/dashboard/layout'

describe('placeTiles', () => {
  it('keeps stored slots and packs the rest below them, left to right', () => {
    const slots = placeTiles([
      { id: 'a', layout: { x: 0, y: 0, w: 6, h: 8 }, kind: 'area' },
      { id: 'b', layout: null, kind: 'kpi' },
      { id: 'c', layout: null, kind: 'kpi' },
      { id: 'd', layout: null, kind: 'bar' },
      { id: 'e', layout: null, kind: 'bar' },
    ])
    expect(slots).toEqual([
      { i: 'a', x: 0, y: 0, w: 6, h: 8 },
      { i: 'b', x: 0, y: 8, w: 3, h: 4 },
      { i: 'c', x: 3, y: 8, w: 3, h: 4 },
      { i: 'd', x: 6, y: 8, w: 6, h: 8 },
      { i: 'e', x: 0, y: 16, w: 6, h: 8 },
    ])
  })

  it('never places anything outside the grid', () => {
    const slots = placeTiles(Array.from({ length: 9 }, (_, i) => ({ id: `t${i}`, layout: null, kind: 'donut' as const })))
    expect(slots.every((s) => s.x >= 0 && s.x + s.w <= GRID_COLS)).toBe(true)
  })
})

describe('resize', () => {
  it('pulls a slot left when a preset would overflow the grid', () => {
    expect(resize({ i: 'a', x: 9, y: 2, w: 3, h: 5 }, 'wide')).toEqual({ i: 'a', x: 0, y: 2, w: 12, h: 8 })
    expect(resize({ i: 'a', x: 4, y: 2, w: 3, h: 5 }, 'medium')).toEqual({ i: 'a', x: 4, y: 2, w: 6, h: 8 })
  })
})

describe('layoutsDiffer', () => {
  it('ignores order and notices any moved slot', () => {
    const a = [{ i: 'x', x: 0, y: 0, w: 3, h: 4 }, { i: 'y', x: 3, y: 0, w: 3, h: 4 }]
    expect(layoutsDiffer(a, [...a].reverse())).toBe(false)
    expect(layoutsDiffer(a, [{ ...a[0], y: 1 }, a[1]])).toBe(true)
  })
})
