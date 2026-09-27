import { useState } from 'react'
import { MoreHorizontal } from 'lucide-react'

import type { Tile, UpdateTileRequest } from '@/api/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { VegaChart } from '@/charts/VegaChart'
import { formatInterval, formatNumber, formatRelativeTime } from '@/lib/format'
import {
  RefreshIntervalDialog,
  RenameTileDialog,
  TileSqlDialog,
  UnpinTileDialog,
} from '@/features/dashboard/TileDialogs'
import { TileTable } from '@/features/dashboard/TileTable'

interface TileCardProps {
  tile: Tile
  devMode: boolean
  isRefreshing: boolean
  onRefresh: () => Promise<unknown>
  onConfirm: () => Promise<unknown>
  onUpdate: (req: UpdateTileRequest) => Promise<unknown>
  onDelete: () => Promise<unknown>
}

export function TileCard({
  tile,
  devMode,
  isRefreshing,
  onRefresh,
  onConfirm,
  onUpdate,
  onDelete,
}: TileCardProps) {
  const [renameOpen, setRenameOpen] = useState(false)
  const [intervalOpen, setIntervalOpen] = useState(false)
  const [unpinOpen, setUnpinOpen] = useState(false)
  const [sqlOpen, setSqlOpen] = useState(false)
  const [isConfirming, setIsConfirming] = useState(false)
  const [isRetrying, setIsRetrying] = useState(false)

  const handleConfirm = async () => {
    setIsConfirming(true)
    try {
      await onConfirm()
    } finally {
      setIsConfirming(false)
    }
  }

  const handleRefresh = async () => {
    setIsRetrying(true)
    try {
      await onRefresh()
    } finally {
      setIsRetrying(false)
    }
  }

  // Check if result is a 1x1 scalar
  const isKpi = Boolean(
    tile.result &&
    tile.result.rows &&
    tile.result.rows.length === 1 &&
    (tile.columns ? tile.columns.length === 1 : Object.keys(tile.result.rows[0] ?? {}).length === 1)
  )

  const { relative, absolute } = tile.result?.computed_at
    ? formatRelativeTime(tile.result.computed_at)
    : { relative: '', absolute: '' }

  const intervalLabel = formatInterval(tile.refresh_interval_s)

  return (
    <div
      data-testid="tile"
      data-tile-id={tile.tile_id}
      className="rounded-lg border bg-card flex h-full flex-col overflow-hidden"
    >
      {/* Header */}
      <div className="tile-drag-handle h-10 px-4 border-b flex items-center justify-between gap-2 shrink-0 cursor-grab active:cursor-grabbing">
        <span
          className="text-sm font-medium truncate flex-1 min-w-0"
          title={tile.title}
        >
          {tile.title}
        </span>

        <div
          className="flex items-center gap-1.5 shrink-0"
          onPointerDown={(e) => e.stopPropagation()}
        >
          {tile.state === 'needs_review' && (
            <Badge
              variant="outline"
              className="text-amber-600 dark:text-amber-400 bg-amber-500/10 border-amber-500/20 text-xs shrink-0 font-normal"
            >
              Needs review
            </Badge>
          )}
          {tile.state === 'error' && (
            <Badge variant="destructive" className="text-xs shrink-0 font-normal">
              Refresh failed
            </Badge>
          )}
          {tile.state === 'ok' && tile.stale && (
            <Badge variant="outline" className="text-xs text-muted-foreground shrink-0 font-normal">
              Stale
            </Badge>
          )}

          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label="Tile actions"
              >
                <MoreHorizontal className="size-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={() => void onRefresh()}>
                Refresh now
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => setRenameOpen(true)}>
                Rename
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => setIntervalOpen(true)}>
                Refresh interval
              </DropdownMenuItem>
              {devMode && (
                <DropdownMenuItem onClick={() => setSqlOpen(true)}>
                  View SQL
                </DropdownMenuItem>
              )}
              <DropdownMenuItem
                onClick={() => setUnpinOpen(true)}
                className="text-destructive focus:text-destructive"
              >
                Unpin
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      {/* Body */}
      <div className="flex-1 p-4 overflow-auto flex flex-col gap-3 min-h-0">
        {tile.state === 'needs_review' && (
          <div className="rounded-md border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-800 dark:text-amber-300 flex flex-col gap-2 shrink-0">
            <p>The data this tile reads has changed. The numbers shown are from before the change.</p>
            <div>
              <Button
                variant="outline"
                size="sm"
                className="text-xs h-7 border-amber-500/40 hover:bg-amber-500/20"
                onClick={handleConfirm}
                disabled={isConfirming}
              >
                Review and accept
              </Button>
            </div>
          </div>
        )}

        {tile.state === 'error' && (
          <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-xs text-destructive flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2 shrink-0">
            <span>
              Last refresh failed: {tile.last_error || 'Unknown error'}
            </span>
            <Button
              variant="outline"
              size="sm"
              className="text-xs h-7 shrink-0"
              onClick={handleRefresh}
              disabled={isRetrying || isRefreshing}
            >
              Retry
            </Button>
          </div>
        )}

        {/* Tile Content */}
        {!tile.result ? (
          <p className="text-sm text-muted-foreground py-2">Not computed yet.</p>
        ) : isKpi ? (
          (() => {
            const row = tile.result.rows[0]
            const colName =
              tile.columns?.[0] ?? (row ? Object.keys(row)[0] : '') ?? ''
            const rawVal = row ? row[colName] : null
            const formattedVal =
              typeof rawVal === 'number'
                ? formatNumber(rawVal)
                : String(rawVal ?? '')

            return (
              <div className="flex flex-1 flex-col justify-center min-h-0">
                <div
                  data-testid="tile-kpi"
                  className="text-3xl font-semibold tabular-nums text-foreground tracking-tight"
                >
                  {formattedVal}
                </div>
                {colName && (
                  <div
                    className="text-xs text-muted-foreground truncate mt-1"
                    title={colName}
                  >
                    {colName}
                  </div>
                )}
              </div>
            )
          })()
        ) : tile.chart ? (
          <div className="flex-1 min-h-0 w-full overflow-hidden flex items-center justify-center">
            <VegaChart spec={tile.chart} />
          </div>
        ) : (
          <TileTable
            rows={tile.result.rows}
            columns={
              tile.columns && tile.columns.length > 0
                ? tile.columns
                : Object.keys(tile.result.rows[0] ?? {})
            }
          />
        )}
      </div>

      {/* Footer */}
      <footer className="px-4 py-2 text-xs text-muted-foreground border-t shrink-0">
        {isRefreshing ? (
          <span>Refreshing…</span>
        ) : relative ? (
          <>
            <span title={absolute}>Updated {relative}</span> · every {intervalLabel}
          </>
        ) : (
          <span>every {intervalLabel}</span>
        )}
      </footer>

      {/* Dialogs */}
      <RenameTileDialog
        open={renameOpen}
        onOpenChange={setRenameOpen}
        initialTitle={tile.title}
        onSave={(title) => onUpdate({ title })}
      />
      <RefreshIntervalDialog
        open={intervalOpen}
        onOpenChange={setIntervalOpen}
        currentInterval={tile.refresh_interval_s}
        onSave={(refresh_interval_s) => onUpdate({ refresh_interval_s })}
      />
      <UnpinTileDialog
        open={unpinOpen}
        onOpenChange={setUnpinOpen}
        onUnpin={onDelete}
      />
      {devMode && (
        <TileSqlDialog
          open={sqlOpen}
          onOpenChange={setSqlOpen}
          sql={tile.sql}
        />
      )}
    </div>
  )
}
