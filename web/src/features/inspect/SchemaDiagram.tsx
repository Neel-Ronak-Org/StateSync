import { useMemo } from 'react'
import { KeyRound } from 'lucide-react'

import type { SchemaGraph } from '@/api/types'
import { layoutTables } from '@/features/inspect/inspect'
import { formatNumber } from '@/lib/format'
import { cn } from '@/lib/utils'

interface SchemaDiagramProps {
  graph: SchemaGraph
  /** Tables to highlight (the ones a query read). */
  highlighted?: string[]
  /** How many columns of tables to lay out. */
  columns?: number
}

const TABLE_WIDTH = 250
const ROW_HEIGHT = 22
const HEADER_HEIGHT = 34
const GAP = 44
const EDGE_ROOM = 36

/** The database as a diagram: one box per table with its columns, and a line per foreign key. */
export function SchemaDiagram({ graph, highlighted = [], columns = 3 }: SchemaDiagramProps) {
  const layout = useMemo(
    () => layoutTables(graph, Math.min(columns, Math.max(1, graph.tables.length)), TABLE_WIDTH, ROW_HEIGHT, HEADER_HEIGHT, GAP),
    [graph, columns],
  )
  const lit = new Set(highlighted)
  const anyLit = lit.size > 0

  const rowY = (table: string, column: string | null): number | null => {
    const pos = layout.positions.get(table)
    const t = graph.tables.find((x) => x.name === table)
    if (!pos || !t) return null
    const idx = column ? t.columns.findIndex((c) => c.name === column) : -1
    return pos.y + HEADER_HEIGHT + (idx < 0 ? 0 : idx) * ROW_HEIGHT + ROW_HEIGHT / 2
  }

  return (
    <div data-testid="schema-diagram" className="overflow-auto rounded-lg border bg-muted/20 p-4">
      {/* Extra room on the right: a link between two tables of the last column bends out past them. */}
      <div className="relative" style={{ width: layout.width + EDGE_ROOM, height: layout.height }}>
        <svg className="pointer-events-none absolute inset-0" width={layout.width + EDGE_ROOM} height={layout.height} aria-hidden="true">
          {graph.edges.map((e, i) => {
            const from = layout.positions.get(e.from_table)
            const to = layout.positions.get(e.to_table)
            const y1 = rowY(e.from_table, e.from_column)
            const y2 = rowY(e.to_table, e.to_column)
            if (!from || !to || y1 == null || y2 == null) return null
            // Leave from the side of the source that faces the target.
            const leftToRight = from.x <= to.x
            const x1 = leftToRight ? from.x + TABLE_WIDTH : from.x
            const x2 = leftToRight ? (from.x === to.x ? to.x + TABLE_WIDTH : to.x) : to.x + TABLE_WIDTH
            const bend = from.x === to.x ? 28 : Math.max(20, Math.abs(x2 - x1) / 2)
            const c1 = leftToRight ? x1 + bend : x1 - bend
            const c2 = from.x === to.x ? x2 + bend : leftToRight ? x2 - bend : x2 + bend
            const on = lit.has(e.from_table) && lit.has(e.to_table)
            return (
              <path
                key={i}
                d={`M ${x1} ${y1} C ${c1} ${y1}, ${c2} ${y2}, ${x2} ${y2}`}
                fill="none"
                className={on ? 'stroke-primary' : 'stroke-muted-foreground/50'}
                strokeWidth={on ? 2 : 1.25}
              />
            )
          })}
        </svg>
        {graph.tables.map((t) => {
          const pos = layout.positions.get(t.name)
          if (!pos) return null
          const on = lit.has(t.name)
          return (
            <div
              key={t.name}
              data-testid="schema-table"
              data-highlighted={on}
              className={cn(
                'absolute overflow-hidden rounded-md border bg-background text-xs shadow-sm',
                on ? 'border-primary ring-2 ring-primary/30' : anyLit ? 'opacity-55' : '',
              )}
              style={{ left: pos.x, top: pos.y, width: TABLE_WIDTH, height: pos.height }}
            >
              <div
                className={cn('flex items-center justify-between gap-2 border-b px-2 font-medium', on ? 'bg-primary/15' : 'bg-muted')}
                style={{ height: HEADER_HEIGHT }}
              >
                <span className="truncate font-mono" title={t.name}>{t.name}</span>
                {t.row_estimate != null && t.row_estimate > 0 && (
                  <span className="shrink-0 text-[10px] font-normal text-muted-foreground">~{formatNumber(t.row_estimate)} rows</span>
                )}
              </div>
              {t.columns.map((c) => (
                <div key={c.name} className="flex items-center justify-between gap-2 px-2" style={{ height: ROW_HEIGHT }}>
                  <span className="flex min-w-0 items-center gap-1 font-mono">
                    {c.foreign_key && <KeyRound className="size-3 shrink-0 text-primary" aria-label="foreign key" />}
                    <span className="truncate" title={c.name}>{c.name}</span>
                  </span>
                  <span className="shrink-0 truncate text-[10px] text-muted-foreground" title={c.type ?? ''}>{c.type}</span>
                </div>
              ))}
            </div>
          )
        })}
      </div>
      {graph.tables_total > graph.tables.length && (
        <p className="mt-3 text-xs text-muted-foreground">
          Showing {graph.tables.length} of {graph.tables_total} tables.
        </p>
      )}
    </div>
  )
}
