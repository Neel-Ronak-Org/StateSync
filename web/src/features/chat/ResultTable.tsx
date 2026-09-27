import { useMemo, useState } from 'react'
import type { Row } from '@/api/types'
import { Button } from '@/components/ui/button'
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

interface ResultTableProps {
  rows: Row[]
  columns: string[]
  sqlRowsCount: number
  onLoadFullResult?: () => void
  isLoadingFull?: boolean
  /** The stored full result was loaded; any remaining gap was never stored. */
  fullResultLoaded?: boolean
}

export function ResultTable({
  rows,
  columns,
  sqlRowsCount,
  onLoadFullResult,
  isLoadingFull,
  fullResultLoaded = false,
}: ResultTableProps) {
  const [showAll, setShowAll] = useState(false)

  // Determine numeric columns by checking the first row with a defined value
  const numericColumns = useMemo(() => {
    const numericSet = new Set<string>()
    for (const col of columns) {
      for (const row of rows) {
        const val = row[col]
        if (val !== null && val !== undefined) {
          if (typeof val === 'number') {
            numericSet.add(col)
          }
          break
        }
      }
    }
    return numericSet
  }, [rows, columns])

  const displayedRows = showAll ? rows : rows.slice(0, 10)
  const isSingleValue = rows.length === 1 && columns.length === 1
  const singleValue = isSingleValue ? rows[0][columns[0]] : null

  const isTruncated = sqlRowsCount > rows.length

  return (
    <div className="space-y-3">
      {/* 1x1 result rendered large */}
      {isSingleValue && (
        <div className="text-3xl font-semibold tabular-nums text-foreground tracking-tight py-1">
          {typeof singleValue === 'number' ? formatNumber(singleValue) : String(singleValue ?? '')}
        </div>
      )}

      {/* Results table */}
      <div className="relative max-h-80 overflow-auto rounded-md border border-border">
        <Table>
          <TableHeader className="sticky top-0 bg-muted z-10">
            <TableRow>
              {columns.map((col) => {
                const isNum = numericColumns.has(col)
                return (
                  <TableHead
                    key={col}
                    className={cn('text-xs font-medium', isNum ? 'text-right' : 'text-left')}
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
                        'text-xs py-2',
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

      {/* Pagination toggle and caption */}
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
        <div>
          {isTruncated ? (
            <div className="flex items-center gap-2">
              <span>
                Showing {rows.length} of {sqlRowsCount} rows
                {fullResultLoaded && ' (the rest was not stored)'}
              </span>
              {onLoadFullResult && !fullResultLoaded && (
                <Button
                  variant="outline"
                  size="xs"
                  onClick={onLoadFullResult}
                  disabled={isLoadingFull}
                >
                  {isLoadingFull ? 'Loading…' : 'Load full result'}
                </Button>
              )}
            </div>
          ) : (
            <span>{rows.length} rows</span>
          )}
        </div>

        {rows.length > 10 && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setShowAll((prev) => !prev)}
            className="h-7 text-xs text-muted-foreground hover:text-foreground"
          >
            {showAll ? 'Show fewer rows' : `Show all ${rows.length} rows`}
          </Button>
        )}
      </div>
    </div>
  )
}
