/**
 * The presentation layer's rules (src/viz, web/DESIGN.md §6).
 *
 * Each block pins one way a dashboard could quietly mislead: a percentage shown
 * as 0.0319, a half-finished day compared with a full one as a "collapse", bars
 * in alphabetical order under "Top 5", a chart kind offered for rows that cannot
 * support it, or a browser-built spec the A7 check would refuse.
 */

import { describe, expect, it } from 'vitest'

import { validateA7Chart } from '@/charts/a7'
import { columnKind, humanize } from '@/viz/columns'
import { formatDuration, formatMeasure, formatValue, percentChange } from '@/viz/format'
import { availableKinds, defaultKind, resolveKind, shapeOf, type VizKind } from '@/viz/infer'
import { kpiOf, periodName } from '@/viz/kpi'
import { buildSpec } from '@/viz/spec'

const HOUR = 3600_000
const NOW = Date.parse('2026-09-27T12:30:00Z')

const hourly = (n: number, lastAt: number, values?: number[]) =>
  Array.from({ length: n }, (_, i) => ({
    hour: new Date(lastAt - (n - 1 - i) * HOUR).toISOString(),
    orders: values ? values[i] : 100 + i,
  }))

const byCity = [
  { city: 'Mumbai', orders: 180 }, { city: 'Delhi', orders: 150 }, { city: 'Pune', orders: 60 },
  { city: 'Kochi', orders: 30 },
]

describe('columns', () => {
  it('humanises column names', () => {
    expect(humanize('order_count')).toBe('Order count')
    expect(humanize('total_orders')).toBe('Total orders')
    expect(humanize('avgSessionLength')).toBe('Avg session length')
    expect(humanize('sla_breach_pct')).toBe('SLA breach %')
    expect(humanize('customer_id')).toBe('Customer ID')
  })

  it('infers kinds from values', () => {
    expect(columnKind('orders', [1, 2.5, null])).toBe('quantitative')
    expect(columnKind('day', ['2026-09-26T00:00:00+00:00', '2026-09-27'])).toBe('temporal')
    expect(columnKind('city', ['Mumbai', 'Delhi'])).toBe('nominal')
    expect(columnKind('year', [2017, 2018])).toBe('nominal')     // a label, not an amount
    expect(columnKind('hour', [0, 1, 23])).toBe('nominal')
    expect(columnKind('mixed', [1, 'two'])).toBe('nominal')
    expect(columnKind('empty', [null, null])).toBe('nominal')
  })
})

describe('format', () => {
  it('shows rates as percentages and seconds as durations', () => {
    expect(formatMeasure(0.031931, 'conversion_rate')).toBe('3.19%')
    expect(formatMeasure(0.897, 'on_time_share')).toBe('89.7%')
    expect(formatMeasure(42.5, 'breach_pct')).toBe('42.5%')
    expect(formatMeasure(272, 'avg_session_s')).toBe('4m 32s')
    expect(formatDuration(3725)).toBe('1h 2m')
  })

  it('compacts big numbers and groups the rest', () => {
    expect(formatMeasure(1284590, 'revenue')).toBe('1.3M')
    expect(formatMeasure(12408, 'orders')).toBe('12,408')
    expect(formatMeasure(4.256, 'avg_rating')).toBe('4.26')
  })

  it('writes changes with an arrow, and refuses a zero base', () => {
    expect(percentChange(112, 100)?.text).toBe('▲ 12%')
    expect(percentChange(97.9, 100)?.text).toBe('▼ 2.1%')
    expect(percentChange(5, 0)).toBeNull()
  })

  it('formats dates by whether they carry a time', () => {
    const col = { name: 'day', kind: 'temporal' as const, label: 'Day' }
    expect(formatValue('2026-09-26T00:00:00Z', col)).toBe('26 Sept 2026')
    expect(formatValue(null)).toBe('—')
  })
})

describe('kinds', () => {
  it('offers only what the rows can support', () => {
    expect(availableKinds(shapeOf([{ n: 550 }]))).toEqual(['kpi', 'table'])
    expect(availableKinds(shapeOf([{ a: 1, b: 2, c: 3 }]))).toEqual(['kpi', 'table'])
    expect(availableKinds(shapeOf(hourly(24, NOW)))).toEqual(['kpi_trend', 'area', 'line', 'bar', 'table'])
    expect(availableKinds(shapeOf(byCity))).toEqual(['bar', 'hbar', 'breakdown', 'donut', 'table'])
    expect(availableKinds(shapeOf([{ city: 'a' }, { city: 'b' }]))).toEqual(['table'])
    expect(availableKinds(shapeOf([]))).toEqual(['table'])
  })

  it('never offers a donut for negative values', () => {
    const rows = [{ c: 'a', v: 5 }, { c: 'b', v: -2 }, { c: 'c', v: 1 }]
    expect(availableKinds(shapeOf(rows))).not.toContain('donut')
    expect(availableKinds(shapeOf(rows))).not.toContain('breakdown')
  })

  it('picks sensible defaults and respects the server hint only when it fits', () => {
    expect(defaultKind(shapeOf([{ total_orders: 550 }]))).toBe('kpi')
    expect(defaultKind(shapeOf(hourly(24, NOW)))).toBe('area')
    expect(defaultKind(shapeOf(hourly(24, NOW)), { mark: 'line' } as never)).toBe('line')
    expect(defaultKind(shapeOf(byCity))).toBe('bar')
    expect(defaultKind(shapeOf(byCity), { mark: 'arc' } as never)).toBe('donut')
    const many = Array.from({ length: 12 }, (_, i) => ({ state: `S${i}`, n: i + 1 }))
    expect(defaultKind(shapeOf(many))).toBe('hbar')
    expect(defaultKind(shapeOf(many), { mark: 'arc' } as never)).toBe('hbar')   // 12 slices: no donut
  })

  it('keeps a person\'s choice when possible and falls back when not', () => {
    expect(resolveKind('donut', shapeOf(byCity))).toBe('donut')
    expect(resolveKind('kpi_trend', shapeOf(byCity))).toBe('bar')
  })
})

describe('KPI numbers', () => {
  it('compares a current and previous value on one row', () => {
    const kpi = kpiOf(shapeOf([{ orders_last_hour: 312, orders_previous_hour: 300 }]), NOW)
    expect(kpi?.value).toBe(312)
    expect(kpi?.change?.text).toBe('▲ 4.0%')
    expect(kpi?.changeLabel).toBe('vs orders previous hour')
  })

  it('leaves an unfinished period out of the number and the change', () => {
    // Last point is 12:00, and at 12:30 that hour has not finished.
    const kpi = kpiOf(shapeOf(hourly(4, Date.parse('2026-09-27T12:00:00Z'), [100, 120, 150, 40])), NOW)
    expect(kpi?.value).toBe(150)
    expect(kpi?.change?.text).toBe('▲ 25%')
    expect(kpi?.changeLabel).toBe('vs previous hour')
    expect(kpi?.label).toBe('Orders, last full hour')
    expect(kpi?.series).toEqual([100, 120, 150, 40])       // the sparkline still shows it
  })

  it('uses the last point when every period is complete', () => {
    const kpi = kpiOf(shapeOf(hourly(3, Date.parse('2026-09-27T10:00:00Z'), [90, 100, 80])), NOW)
    expect(kpi?.value).toBe(80)
    expect(kpi?.change?.text).toBe('▼ 20%')
  })

  it('names periods from the spacing of the points', () => {
    expect(periodName(3600)).toBe('hour')
    expect(periodName(86400)).toBe('day')
    expect(periodName(300)).toBe('5 minutes')
    expect(periodName(12345)).toBeNull()
  })
})

describe('specs', () => {
  const cases: [VizKind, Record<string, unknown>[]][] = [
    ['area', hourly(24, NOW)],
    ['line', hourly(24, NOW)],
    ['bar', hourly(6, NOW)],
    ['bar', byCity],
    ['hbar', byCity],
    ['donut', byCity],
    ['heatmap', [{ city: 'Mumbai', status: 'late', n: 3 }, { city: 'Delhi', status: 'ok', n: 9 }]],
    ['scatter', [{ items: 1, amount: 900 }, { items: 2, amount: 1400 }, { items: 3, amount: 2600 }]],
  ]

  it.each(cases)('%s passes the A7 check', (kind, rows) => {
    const spec = buildSpec(kind, shapeOf(rows))
    expect(spec).not.toBeNull()
    expect(validateA7Chart(spec)).toEqual([])
  })

  it('sorts a ranking by value and titles axes readably', () => {
    const spec = buildSpec('bar', shapeOf(byCity)) as { encoding: Record<string, Record<string, unknown>> }
    expect(spec.encoding.x.sort).toBe('-y')
    expect(spec.encoding.y.title).toBe('Orders')
    const h = buildSpec('hbar', shapeOf(byCity)) as { encoding: Record<string, Record<string, unknown>> }
    expect(h.encoding.y.sort).toBe('-x')
  })

  it('returns null when the rows lack what the kind needs', () => {
    expect(buildSpec('donut', shapeOf(hourly(3, NOW)))).toBeNull()
    expect(buildSpec('heatmap', shapeOf(byCity))).toBeNull()
    expect(buildSpec('scatter', shapeOf(byCity))).toBeNull()
  })
})

describe('two-row comparisons', () => {
  const rows = [{ period: 'last_24h', total_revenue: 110 }, { period: 'prev_24h', total_revenue: 100 }]

  it('are recognised and shown as a KPI whichever row comes first', () => {
    expect(availableKinds(shapeOf(rows))[0]).toBe('kpi')
    expect(defaultKind(shapeOf(rows))).toBe('kpi')
    const kpi = kpiOf(shapeOf([...rows].reverse()), NOW)
    expect(kpi?.value).toBe(110)
    expect(kpi?.change?.text).toBe('▲ 10%')
    expect(kpi?.changeLabel).toBe('vs prev 24h')
  })

  it('need exactly one row labelled as the previous period', () => {
    expect(defaultKind(shapeOf([{ c: 'north', v: 1 }, { c: 'south', v: 2 }]))).toBe('bar')
    expect(defaultKind(shapeOf([{ c: 'previous_7_days', v: 1 }, { c: 'prior_7_days', v: 2 }]))).not.toBe('kpi')
    expect(kpiOf(shapeOf([{ period: 'last_7_days', r: 0.89 }, { period: 'previous_7_days', r: 0.9 }]))?.changeLabel)
      .toBe('vs previous 7 days')
  })
})

describe('one row, several numbers', () => {
  it('shows the rate as the headline, without a change', () => {
    const kpi = kpiOf(shapeOf([{ orders_count: 550, disputes_count: 80, disputes_per_order: 0.1454 }]))
    expect(kpi?.column.name).toBe('disputes_per_order')
    expect(kpi?.change).toBeNull()
    expect(kpiOf(shapeOf([{ a: 5, b: 6, c: 7 }]))?.value).toBe(5)
  })
})

