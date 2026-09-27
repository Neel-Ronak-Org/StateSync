/**
 * The one-line insights above a dashboard (web/DESIGN.md §6).
 *
 * Every number in them is read from a tile's own rows, the same rows the tile
 * draws; nothing is estimated and no model writes them. Two kinds:
 * - a change: a KPI with a previous value, or a trend's last full period
 *   against the one before ("Orders, last full hour: 312 · ▲ 4.2% vs previous hour");
 * - a leader: the largest category's share of a breakdown ("Mumbai leads
 *   Orders by city with 18% of the total").
 * Changes rank above leaders, larger moves first. At most four are shown.
 */

import { formatMeasure } from '@/viz/format'
import { resolveKind, type Shape, type VizKind } from '@/viz/infer'
import { kpiOf } from '@/viz/kpi'

export interface InsightSource {
  tileId: string
  title: string
  shape: Shape
  kind: VizKind
}

export interface Insight {
  tileId: string
  text: string
  tone: 'up' | 'down' | 'neutral'
  /** Sort weight: larger first. */
  weight: number
}

const MAX_INSIGHTS = 4
const LEADER_MIN_CATEGORIES = 3
const LEADER_MIN_SHARE = 0.15
const LEADER_KINDS: ReadonlySet<VizKind> = new Set(['bar', 'hbar', 'breakdown', 'donut'])
// Changes always outrank leaders: a change of any size is news, a leader is context.
const CHANGE_BASE_WEIGHT = 1000

function changeInsight(source: InsightSource, now: number): Insight | null {
  if (source.kind !== 'kpi' && source.kind !== 'kpi_trend') return null
  const kpi = kpiOf(source.shape, now)
  if (!kpi || !kpi.change || !kpi.changeLabel) return null
  const label = source.kind === 'kpi_trend' && kpi.label !== kpi.column.label ? kpi.label : source.title
  return {
    tileId: source.tileId,
    text: `${label}: ${formatMeasure(kpi.value, kpi.column)} · ${kpi.change.text} ${kpi.changeLabel}`,
    tone: kpi.change.pct > 0 ? 'up' : kpi.change.pct < 0 ? 'down' : 'neutral',
    weight: CHANGE_BASE_WEIGHT + Math.abs(kpi.change.pct),
  }
}

function leaderInsight(source: InsightSource): Insight | null {
  if (!LEADER_KINDS.has(source.kind)) return null
  const [category] = source.shape.categories
  const [measure] = source.shape.measures
  if (!category || !measure || source.shape.rows.length < LEADER_MIN_CATEGORIES) return null
  const values = source.shape.rows.map((r) => ({ name: String(r[category.name] ?? ''), v: r[measure.name] }))
  if (values.some((x) => typeof x.v !== 'number' || (x.v as number) < 0)) return null
  const total = values.reduce((sum, x) => sum + (x.v as number), 0)
  if (total <= 0) return null
  const top = values.reduce((best, x) => ((x.v as number) > (best.v as number) ? x : best))
  const share = (top.v as number) / total
  if (share < LEADER_MIN_SHARE) return null
  return {
    tileId: source.tileId,
    text: `${top.name} leads ${source.title} with ${Math.round(share * 100)}% of the total`,
    tone: 'neutral',
    weight: share * 100,
  }
}

/** Up to four insights across the tiles, most notable first. */
export function insightsFor(sources: InsightSource[], now: number = Date.now()): Insight[] {
  const found: Insight[] = []
  for (const source of sources) {
    const insight = changeInsight(source, now) ?? leaderInsight(source)
    if (insight) found.push(insight)
  }
  return found.sort((a, b) => b.weight - a.weight).slice(0, MAX_INSIGHTS)
}

/** Convenience for callers holding rows and a stored choice. */
export function insightSource(tileId: string, title: string, shape: Shape, requested: VizKind | null): InsightSource {
  return { tileId, title, shape, kind: resolveKind(requested, shape) }
}
