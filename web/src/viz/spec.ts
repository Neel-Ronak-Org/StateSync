/**
 * Vega-Lite specs built in the browser from a result's rows (web/DESIGN.md §6).
 *
 * Only what the A7 allowlist permits is used: a string mark, encoding channels
 * with field / type / title / sort, and inline data.values. No transforms, no
 * expressions, no tooltips. VegaChart still runs validateA7Chart on every spec
 * before Vega sees it, so a mistake here withholds a chart; it cannot run code.
 * Styling (gradients, curves, the donut hole) comes from charts/theme.ts config.
 */

import type { ChartSpec } from '@/api/types'
import { A7 } from '@/charts/a7'
import { isYearColumn, type Column } from '@/viz/columns'
import type { Shape, VizKind } from '@/viz/infer'

type Encoding = Record<string, Record<string, unknown>>

function channel(column: Column, type: string, extra: Record<string, unknown> = {}): Record<string, unknown> {
  return { field: column.name, type, title: column.label, ...extra }
}

/** A time axis: years (2018) are ordered labels; dates are a real time scale. */
function timeChannel(time: Column, shape: Shape): Record<string, unknown> {
  return isYearColumn(time, shape.rows) ? channel(time, 'ordinal', { sort: 'ascending' }) : channel(time, 'temporal')
}

/** A second category worth colouring by: fewer distinct values than the first (not a 1:1 label such as name beside ticker). */
function grouping(shape: Shape, category: Column, category2: Column | undefined): Column | undefined {
  if (!category2) return undefined
  const distinct = (c: Column) => new Set(shape.rows.map((r) => String(r[c.name]))).size
  return distinct(category2) < distinct(category) ? category2 : undefined
}

function base(mark: string, encoding: Encoding, shape: Shape): ChartSpec {
  return { $schema: A7.SCHEMA_URL, mark, encoding, data: { values: shape.rows } } as ChartSpec
}

/**
 * The spec for a Vega-drawn kind, or null if the rows do not have the columns
 * that kind needs (the caller then falls back to a table).
 */
export function buildSpec(kind: VizKind, shape: Shape): ChartSpec | null {
  const [measure, measure2] = shape.measures
  const [category, category2] = shape.categories
  const time = shape.time

  switch (kind) {
    case 'line':
    case 'area': {
      if (!measure) return null
      const x = time ? timeChannel(time, shape) : category ? channel(category, 'ordinal', { sort: null }) : null
      if (!x) return null
      const encoding: Encoding = { x, y: channel(measure, 'quantitative') }
      if (time && category) encoding.color = channel(category, 'nominal')
      return base(kind, encoding, shape)
    }
    case 'bar': {
      if (!measure) return null
      if (time) {
        const encoding: Encoding = { x: timeChannel(time, shape), y: channel(measure, 'quantitative') }
        if (category) encoding.color = channel(category, 'nominal')
        return base('bar', encoding, shape)
      }
      if (!category) return null
      const encoding: Encoding = {
        x: channel(category, 'nominal', { sort: '-y' }),
        y: channel(measure, 'quantitative'),
      }
      const group = grouping(shape, category, category2)
      if (group) encoding.color = channel(group, 'nominal')
      return base('bar', encoding, shape)
    }
    case 'hbar': {
      if (!measure || !category) return null
      const encoding: Encoding = {
        y: channel(category, 'nominal', { sort: '-x' }),
        x: channel(measure, 'quantitative'),
      }
      const group = grouping(shape, category, category2)
      if (group) encoding.color = channel(group, 'nominal')
      return base('bar', encoding, shape)
    }
    case 'donut': {
      if (!measure || !category) return null
      return base('arc', {
        theta: channel(measure, 'quantitative'),
        color: channel(category, 'nominal', { sort: null }),
      }, shape)
    }
    case 'heatmap': {
      if (!measure || !category || !category2) return null
      return base('rect', {
        x: channel(category, 'nominal'),
        y: channel(category2, 'nominal'),
        color: channel(measure, 'quantitative'),
      }, shape)
    }
    case 'scatter': {
      if (!measure || !measure2) return null
      const encoding: Encoding = { x: channel(measure, 'quantitative'), y: channel(measure2, 'quantitative') }
      if (category) encoding.color = channel(category, 'nominal')
      return base('point', encoding, shape)
    }
    default:
      return null
  }
}
