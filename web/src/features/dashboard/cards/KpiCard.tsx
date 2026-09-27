import { AnimatedNumber } from '@/features/dashboard/cards/AnimatedNumber'
import { Sparkline } from '@/features/dashboard/cards/Sparkline'
import { cn } from '@/lib/utils'
import { formatMeasure } from '@/viz/format'
import type { Shape, VizKind } from '@/viz/infer'
import { kpiOf } from '@/viz/kpi'

interface KpiCardProps {
  shape: Shape
  kind: VizKind
}

/** A big number, its change, and for a series its sparkline (web/DESIGN.md §6). */
export function KpiCard({ shape, kind }: KpiCardProps) {
  const kpi = kpiOf(shape)
  if (!kpi) return <p className="text-sm text-muted-foreground">No value to show.</p>

  const up = (kpi.change?.pct ?? 0) > 0
  const down = (kpi.change?.pct ?? 0) < 0
  return (
    // mt-auto, not justify-end: with no room (a banner above it) justify-end
    // overflows UPWARD over the banner; mt-auto collapses and flows down.
    <div className="flex h-full min-h-0 flex-col gap-1">
      <div className="mt-auto flex items-end justify-between gap-3">
        <div className="min-w-0">
          <AnimatedNumber
            data-testid="tile-kpi"
            value={kpi.value}
            format={(v) => formatMeasure(v, kpi.column)}
            className="block truncate text-3xl font-semibold tracking-tight text-foreground tabular-nums"
          />
          <div className="mt-1 truncate text-xs text-muted-foreground" title={kpi.label}>
            {kpi.label}
          </div>
        </div>
        {kind === 'kpi_trend' && kpi.series && (
          <Sparkline values={kpi.series} className="mb-1 h-10 w-28 shrink-0 text-primary" />
        )}
      </div>
      {kpi.change && (
        <div className="mt-1 flex items-center gap-2 text-xs">
          <span
            data-testid="tile-delta"
            aria-label={`${up ? 'up' : down ? 'down' : 'unchanged'} ${kpi.change.text.replace(/[▲▼]\s*/, '')} ${kpi.changeLabel ?? ''}`.trim()}
            className={cn(
              'rounded-full px-2 py-0.5 font-medium tabular-nums',
              up && 'bg-success/12 text-success',
              down && 'bg-destructive/12 text-destructive',
              !up && !down && 'bg-muted text-muted-foreground',
            )}
          >
            {kpi.change.text}
          </span>
          <span className="truncate text-muted-foreground">{kpi.changeLabel}</span>
        </div>
      )}
    </div>
  )
}
