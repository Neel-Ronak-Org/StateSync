import { motion } from 'motion/react'

import { formatMeasure } from '@/viz/format'
import type { Shape } from '@/viz/infer'

const MAX_ROWS = 6
const COLORS = ['var(--chart-c1)', 'var(--chart-c2)', 'var(--chart-c3)', 'var(--chart-c4)', 'var(--chart-c5)', 'var(--chart-c6)']

/** Label, value, share and a bar for the largest categories; the rest as "Other" (web/DESIGN.md §6). */
export function BreakdownCard({ shape }: { shape: Shape }) {
  const [category] = shape.categories
  const [measure] = shape.measures
  if (!category || !measure) return null

  const entries = shape.rows
    .map((r) => ({ label: String(r[category.name] ?? '—'), value: Number(r[measure.name]) || 0 }))
    .sort((a, b) => b.value - a.value)
  const top = entries.slice(0, MAX_ROWS)
  const rest = entries.slice(MAX_ROWS).reduce((sum, e) => sum + e.value, 0)
  if (rest > 0) top.push({ label: 'Other', value: rest })
  const total = entries.reduce((sum, e) => sum + e.value, 0) || 1
  const largest = Math.max(...top.map((e) => e.value)) || 1

  return (
    <ul data-testid="breakdown" className="flex h-full flex-col justify-center gap-3 overflow-hidden">
      {top.map((entry, i) => {
        const share = (entry.value / total) * 100
        return (
          <li key={`${entry.label}-${i}`} data-testid="breakdown-row">
            <div className="flex items-baseline justify-between gap-3 text-xs">
              <span className="truncate text-foreground/90" title={entry.label}>{entry.label}</span>
              <span className="shrink-0 tabular-nums text-muted-foreground">
                {formatMeasure(entry.value, measure)}
                <span className="ml-1.5 text-foreground/80">{share.toFixed(share < 10 ? 1 : 0)}%</span>
              </span>
            </div>
            <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-muted">
              <motion.div
                className="h-full rounded-full"
                style={{ background: entry.label === 'Other' ? 'var(--muted-foreground)' : COLORS[i % COLORS.length] }}
                initial={{ width: 0 }}
                animate={{ width: `${(entry.value / largest) * 100}%` }}
                transition={{ duration: 0.6, ease: 'easeOut' }}
              />
            </div>
          </li>
        )
      })}
    </ul>
  )
}
