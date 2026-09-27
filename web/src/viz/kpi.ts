/**
 * The numbers a KPI card shows (web/DESIGN.md §6).
 *
 * One row: the first measure, and when there is a second one ("this hour",
 * "the hour before") the change between them.
 * A time series: the last COMPLETE period and its change from the one before.
 * A period that has not finished yet (today at 10:00, the current hour) would
 * always look like a collapse against a finished one, so it is left out of the
 * number and the change, and only drawn in the sparkline.
 */

import { humanize, type Column } from '@/viz/columns'
import { percentChange } from '@/viz/format'
import { comparisonPreviousIndex, type Shape } from '@/viz/infer'

export interface KpiData {
  value: number
  column: Column
  /** Caption under the value. */
  label: string
  change: { pct: number; text: string } | null
  /** What the change is measured against, e.g. "vs previous hour". */
  changeLabel: string | null
  /** Sparkline points, oldest first (time series only). */
  series: number[] | null
}

const RATE_NAME = /(?:rate|ratio|share|pct|percent|percentage|_per_)/i

const PERIODS: [seconds: number, name: string][] = [
  [60, 'minute'], [300, '5 minutes'], [900, '15 minutes'], [1800, '30 minutes'], [3600, 'hour'],
  [86400, 'day'], [604800, 'week'], [2629800, 'month'], [7889400, 'quarter'], [31557600, 'year'],
]

/** "hour", "day"... for a spacing between points, or null if it is irregular. */
export function periodName(spacingSeconds: number): string | null {
  let best: [number, string] | null = null
  for (const [seconds, name] of PERIODS) {
    const ratio = spacingSeconds / seconds
    if (ratio > 0.8 && ratio < 1.25) {
      if (!best || Math.abs(1 - ratio) < Math.abs(1 - spacingSeconds / best[0])) best = [seconds, name]
    }
  }
  return best ? best[1] : null
}

function median(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b)
  return sorted[Math.floor(sorted.length / 2)] ?? 0
}

/** KPI numbers for a result, or null when it has no measure to show. */
export function kpiOf(shape: Shape, now: number = Date.now()): KpiData | null {
  const [first, second] = shape.measures
  if (!first || shape.rows.length === 0) return null

  const previousIndex = comparisonPreviousIndex(shape)
  if (previousIndex !== null) {
    const category = shape.categories[0]
    const current = shape.rows[1 - previousIndex]
    const previous = shape.rows[previousIndex]
    const value = current[first.name]
    const before = previous[first.name]
    if (typeof value !== 'number') return null
    const change = typeof before === 'number' ? percentChange(value, before) : null
    return {
      value,
      column: first,
      label: first.label,
      change,
      changeLabel: change ? `vs ${humanize(String(previous[category.name])).toLowerCase()}` : null,
      series: null,
    }
  }

  if (shape.rows.length === 1 && shape.measures.length > 2) {
    // Several numbers in one row ("disputes, orders, disputes per order"): the
    // headline is the rate if there is one, else the first. No change is shown:
    // the others are parts of it, not a previous period.
    const headline = shape.measures.find((m) => RATE_NAME.test(m.name)) ?? first
    const value = shape.rows[0][headline.name]
    if (typeof value !== 'number') return null
    return { value, column: headline, label: headline.label, change: null, changeLabel: null, series: null }
  }

  if (shape.rows.length === 1 || !shape.time) {
    const row = shape.rows[0]
    const value = row[first.name]
    if (typeof value !== 'number') return null
    const previous = second ? shape.rows[0][second.name] : undefined
    const change = typeof previous === 'number' ? percentChange(value, previous) : null
    return {
      value,
      column: first,
      label: first.label,
      change,
      changeLabel: change && second ? `vs ${second.label.toLowerCase()}` : null,
      series: null,
    }
  }

  const time = shape.time
  const points = shape.rows
    .map((r) => ({ t: Date.parse(String(r[time.name])), v: r[first.name] }))
    .filter((p): p is { t: number; v: number } => Number.isFinite(p.t) && typeof p.v === 'number')
    .sort((a, b) => a.t - b.t)
  if (points.length === 0) return null

  const gaps = points.slice(1).map((p, i) => (p.t - points[i].t) / 1000)
  const spacing = gaps.length ? median(gaps) : 0
  const last = points[points.length - 1]
  const unfinished = spacing > 0 && last.t + spacing * 1000 > now
  const complete = unfinished && points.length >= 2 ? points.slice(0, -1) : points
  const current = complete[complete.length - 1]
  const before = complete[complete.length - 2]
  const period = spacing > 0 ? periodName(spacing) : null
  const change = before ? percentChange(current.v, before.v) : null

  return {
    value: current.v,
    column: first,
    label: period && unfinished ? `${first.label}, last full ${period}` : first.label,
    change,
    changeLabel: change ? (period ? `vs previous ${period}` : 'vs previous') : null,
    series: points.map((p) => p.v),
  }
}
