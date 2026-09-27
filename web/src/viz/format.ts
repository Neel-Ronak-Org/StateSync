/**
 * How a single value is written on a card (web/DESIGN.md §8).
 *
 * The column name decides the unit, because the database does not say:
 * a rate or share between 0 and 1 is a percentage, a column of seconds is a
 * duration, and big numbers are compacted ("1.2M") so a KPI stays readable.
 * No currency symbol is added: the datasets are in different currencies and
 * the query result does not say which.
 */

import type { Column } from '@/viz/columns'

const RATIO = /(?:^|_|\b)(?:rate|ratio|share|pct|percent|percentage|proportion|fraction)(?:$|_|\b)/i
const PERCENT = /(?:^|_|\b)(?:pct|percent|percentage)(?:$|_|\b)/i
const SECONDS = /(?:_s|_sec|_secs|_seconds|seconds|duration)$/i

const grouped = new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 })
const compact = new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 })
const dateOnly = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
const dateTime = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })

const COMPACT_FROM = 100_000

function nameOf(column?: Column | string): string {
  return typeof column === 'string' ? column : (column?.name ?? '')
}

export function formatDuration(totalSeconds: number): string {
  const s = Math.round(Math.abs(totalSeconds))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  const sign = totalSeconds < 0 ? '-' : ''
  if (h > 0) return `${sign}${h}h ${m}m`
  if (m > 0) return `${sign}${m}m ${sec}s`
  return `${sign}${sec}s`
}

/** A number the way a card shows it, given the column it came from. */
export function formatMeasure(value: number, column?: Column | string): string {
  const name = nameOf(column)
  if (RATIO.test(name) && value >= 0 && value <= 1) {
    const pct = value * 100
    return `${pct.toFixed(pct < 10 ? 2 : 1)}%`
  }
  if (PERCENT.test(name) && value > 1 && value <= 100) return `${value.toFixed(1)}%`
  if (SECONDS.test(name)) return formatDuration(value)
  if (Math.abs(value) >= COMPACT_FROM) return compact.format(value)
  return grouped.format(value)
}

/** Any cell value as text: numbers by formatMeasure, ISO timestamps as dates, the rest as is. */
export function formatValue(value: unknown, column?: Column | string): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'number') return formatMeasure(value, column)
  if (typeof value === 'string' && typeof column === 'object' && column?.kind === 'temporal') {
    const date = new Date(value)
    if (!Number.isNaN(date.getTime())) {
      const midnight = date.getUTCHours() === 0 && date.getUTCMinutes() === 0 && date.getUTCSeconds() === 0
      return midnight ? dateOnly.format(date) : dateTime.format(date)
    }
  }
  return String(value)
}

/** "▲ 4.2%" / "▼ 3.1%" / "0%": the change from `previous` to `current`, or null if it cannot be computed. */
export function percentChange(current: number, previous: number): { pct: number; text: string } | null {
  if (!Number.isFinite(current) || !Number.isFinite(previous) || previous === 0) return null
  const pct = ((current - previous) / Math.abs(previous)) * 100
  const rounded = Math.abs(pct) < 10 ? pct.toFixed(1) : pct.toFixed(0)
  if (Number(rounded) === 0) return { pct: 0, text: '0%' }
  return { pct, text: `${pct > 0 ? '▲' : '▼'} ${rounded.replace('-', '')}%` }
}
