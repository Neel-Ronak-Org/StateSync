import { useMemo } from 'react'

import type { Row } from '@/api/types'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { formatNumber } from '@/lib/format'
import { cn } from '@/lib/utils'

interface TileTableProps {
  rows: Row[]
  columns: string[]
}

export function TileTable({ rows, columns }: TileTableProps) {
  const numericColumns = useMemo(() => {
    const set = new Set<string>()
    for (const col of columns) {
      for (const row of rows) {
        const val = row[col]
        if (val !== null && val !== undefined) {
          if (typeof val === 'number') {
            set.add(col)
          }
          break
        }
      }
    }
    return set
  }, [rows, columns])

  const displayedRows = rows.slice(0, 20)

  return (
    <div className="relative flex-1 min-h-0 overflow-auto rounded-md border border-border">
      <Table>
        <TableHeader className="sticky top-0 bg-muted z-10">
          <TableRow>
            {columns.map((col) => {
              const isNum = numericColumns.has(col)
              return (
                <TableHead
                  key={col}
                  className={cn(
                    'text-xs font-medium py-1.5 h-auto',
                    isNum ? 'text-right' : 'text-left'
                  )}
                >
                  {col}
                </TableHead>
              )
            })}
          </TableRow>
        </TableHeader>
        <TableBody>
          {displayedRows.map((row, rIdx) => (
            <TableRow key={rIdx}>
              {columns.map((col) => {
                const val = row[col]
                const isNum = numericColumns.has(col)
                return (
                  <TableCell
                    key={col}
                    className={cn(
                      'text-xs py-1.5',
                      isNum ? 'text-right font-mono tabular-nums' : 'text-left'
                    )}
                  >
                    {val === null || val === undefined
                      ? '—'
                      : typeof val === 'number'
                        ? formatNumber(val)
                        : String(val)}
                  </TableCell>
                )
              })}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}
