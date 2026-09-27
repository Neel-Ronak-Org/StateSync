import type { ChartSpec } from '@/api/types'
import { VegaChart } from '@/charts/VegaChart'
import { BreakdownCard } from '@/features/dashboard/cards/BreakdownCard'
import { KpiCard } from '@/features/dashboard/cards/KpiCard'
import { TileTable } from '@/features/dashboard/TileTable'
import { useElementSize } from '@/hooks/useElementSize'
import { VEGA_KINDS, type Shape, type VizKind } from '@/viz/infer'
import { buildSpec } from '@/viz/spec'

interface TileBodyProps {
  shape: Shape
  kind: VizKind
  /** Show the A7 violation list if a chart is withheld (developer mode). */
  devMode: boolean
}

/** The content of a tile, drawn as `kind` (web/DESIGN.md §6). Falls back to the table. */
export function TileBody({ shape, kind, devMode }: TileBodyProps) {
  const { ref, height } = useElementSize<HTMLDivElement>()

  if (kind === 'kpi' || kind === 'kpi_trend') return <KpiCard shape={shape} kind={kind} />
  if (kind === 'breakdown') return <BreakdownCard shape={shape} />

  const spec: ChartSpec | null = VEGA_KINDS.has(kind) ? buildSpec(kind, shape) : null
  if (!spec) {
    return <TileTable rows={shape.rows} columns={shape.columns.map((c) => c.name)} />
  }
  return (
    <div ref={ref} className="h-full min-h-0 w-full">
      {height > 0 && <VegaChart spec={spec} height={height} showViolations={devMode} />}
    </div>
  )
}
