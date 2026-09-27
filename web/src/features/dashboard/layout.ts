/**
 * Where tiles sit on the 12-column grid (web/DESIGN.md §6).
 *
 * The server stores each tile's slot; a tile without one (just pinned from
 * chat, or moved from another dashboard) is placed here, below everything
 * that has a slot, packed left to right, at a size that suits its kind.
 */

import type { TileLayout } from '@/api/types'
import type { VizKind } from '@/viz/infer'

export const GRID_COLS = 12
export const ROW_HEIGHT = 32
export const GRID_MARGIN = 16

export type SizePreset = 'small' | 'medium' | 'large' | 'wide'

export const SIZE_PRESETS: Record<SizePreset, { w: number; h: number; label: string }> = {
  small: { w: 3, h: 5, label: 'Small' },
  medium: { w: 6, h: 8, label: 'Medium' },
  large: { w: 6, h: 12, label: 'Large' },
  wide: { w: 12, h: 8, label: 'Wide' },
}

export function sizeFor(kind: VizKind): { w: number; h: number } {
  switch (kind) {
    case 'kpi':
      return { w: 3, h: 4 }
    case 'kpi_trend':
      return { w: 3, h: 5 }
    case 'breakdown':
    case 'donut':
      return { w: 4, h: 8 }
    default:
      return { w: 6, h: 8 }
  }
}

export interface Slot extends TileLayout {
  i: string
}

export interface Placeable {
  id: string
  layout: TileLayout | null
  kind: VizKind
}

/** Every tile's slot: stored ones as they are, the rest packed below them in order. */
export function placeTiles(tiles: Placeable[]): Slot[] {
  const placed: Slot[] = []
  let bottom = 0
  for (const t of tiles) {
    if (t.layout) {
      placed.push({ i: t.id, ...t.layout })
      bottom = Math.max(bottom, t.layout.y + t.layout.h)
    }
  }
  let x = 0
  let y = bottom
  let rowHeight = 0
  for (const t of tiles) {
    if (t.layout) continue
    const { w, h } = sizeFor(t.kind)
    if (x + w > GRID_COLS) {
      x = 0
      y += rowHeight
      rowHeight = 0
    }
    placed.push({ i: t.id, x, y, w, h })
    x += w
    rowHeight = Math.max(rowHeight, h)
  }
  return placed
}

/** A preset applied to a slot, kept inside the grid. */
export function resize(slot: Slot, preset: SizePreset): Slot {
  const { w, h } = SIZE_PRESETS[preset]
  return { ...slot, w, h, x: Math.min(slot.x, GRID_COLS - w) }
}

/** Whether two layouts differ in any slot (order-insensitive). */
export function layoutsDiffer(a: readonly Slot[], b: readonly Slot[]): boolean {
  if (a.length !== b.length) return true
  const byId = new Map(b.map((s) => [s.i, s]))
  return a.some((s) => {
    const o = byId.get(s.i)
    return !o || o.x !== s.x || o.y !== s.y || o.w !== s.w || o.h !== s.h
  })
}
