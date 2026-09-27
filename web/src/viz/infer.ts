/**
 * Which ways a result can be drawn, and the one to use by default
 * (web/DESIGN.md §6, docs/V3_PLAN.md decision D-v3-8).
 *
 * The browser decides presentation from the rows. The server's own chart
 * proposal (what E2-V1 measures) is only a hint: it breaks ties, it never
 * forces a kind the rows cannot support. A person's choice ("Show as") wins
 * whenever the rows can be drawn that way.
 */

import type { ChartSpec } from '@/api/types'
import { describeColumns, type Column, type Row } from '@/viz/columns'

export type VizKind =
  | 'kpi' | 'kpi_trend' | 'line' | 'area' | 'bar' | 'hbar'
  | 'donut' | 'breakdown' | 'heatmap' | 'scatter' | 'table'

export const VIZ_LABELS: Record<VizKind, string> = {
  kpi: 'KPI',
  kpi_trend: 'KPI with trend',
  line: 'Line',
  area: 'Area',
  bar: 'Bar',
  hbar: 'Horizontal bar',
  donut: 'Donut',
  breakdown: 'Breakdown',
  heatmap: 'Heatmap',
  scatter: 'Scatter',
  table: 'Table',
}

/** Kinds drawn by VegaChart; the rest are React components. */
export const VEGA_KINDS: ReadonlySet<VizKind> = new Set(['line', 'area', 'bar', 'hbar', 'donut', 'heatmap', 'scatter'])

export interface Shape {
  rows: Row[]
  columns: Column[]
  /** The first time column, if any. */
  time: Column | null
  /** Category columns, in order. */
  categories: Column[]
  /** Numeric columns, in order. */
  measures: Column[]
}

const DONUT_MAX_ROWS = 8
const BREAKDOWN_MAX_ROWS = 50
const HBAR_FROM_ROWS = 9
const HBAR_FROM_LABEL_CHARS = 14
const TREND_BAR_MAX_ROWS = 12

export function shapeOf(rows: Row[], names?: string[] | null): Shape {
  const columns = describeColumns(rows, names)
  return {
    rows,
    columns,
    time: columns.find((c) => c.kind === 'temporal') ?? null,
    categories: columns.filter((c) => c.kind === 'nominal'),
    measures: columns.filter((c) => c.kind === 'quantitative'),
  }
}

function nonNegative(shape: Shape, column: Column): boolean {
  return shape.rows.every((r) => typeof r[column.name] !== 'number' || (r[column.name] as number) >= 0)
}

function longestLabel(shape: Shape, column: Column): number {
  return shape.rows.reduce((max, r) => Math.max(max, String(r[column.name] ?? '').length), 0)
}

/** Every kind these rows can be drawn as, in menu order; "table" is always last. */
export function availableKinds(shape: Shape): VizKind[] {
  const n = shape.rows.length
  const [measure] = shape.measures
  if (n === 0 || !measure) return ['table']
  if (n === 1) return shape.measures.length <= 2 ? ['kpi', 'table'] : ['table']

  const kinds: VizKind[] = []
  if (shape.time) {
    if (shape.categories.length === 0) kinds.push('kpi_trend')
    kinds.push('area', 'line', 'bar')
  } else if (shape.categories.length > 0) {
    kinds.push('bar', 'hbar')
    if (nonNegative(shape, measure)) {
      if (n <= BREAKDOWN_MAX_ROWS) kinds.push('breakdown')
      if (n <= DONUT_MAX_ROWS) kinds.push('donut')
    }
    if (shape.categories.length >= 2) kinds.push('heatmap')
  }
  if (shape.measures.length >= 2 && n >= 3) kinds.push('scatter')
  kinds.push('table')
  return kinds
}

/** The kind to show when nobody has chosen one. */
export function defaultKind(shape: Shape, hint?: ChartSpec | null): VizKind {
  const available = availableKinds(shape)
  const n = shape.rows.length
  if (available[0] === 'table' || available[0] === 'kpi') return available[0]

  const mark = typeof hint?.mark === 'string' ? hint.mark : null
  if (shape.time) {
    if (mark === 'line') return 'line'
    if (mark === 'bar' && n <= TREND_BAR_MAX_ROWS) return 'bar'
    return 'area'
  }
  if (shape.categories.length > 0) {
    if (mark === 'arc' && available.includes('donut')) return 'donut'
    if (mark === 'rect' && available.includes('heatmap')) return 'heatmap'
    const first = shape.categories[0]
    return n >= HBAR_FROM_ROWS || longestLabel(shape, first) > HBAR_FROM_LABEL_CHARS ? 'hbar' : 'bar'
  }
  if (available.includes('scatter')) return 'scatter'
  return 'table'
}

/** The kind to draw: the requested one if the rows support it, else the default. */
export function resolveKind(requested: VizKind | null | undefined, shape: Shape, hint?: ChartSpec | null): VizKind {
  if (requested && availableKinds(shape).includes(requested)) return requested
  return defaultKind(shape, hint)
}
