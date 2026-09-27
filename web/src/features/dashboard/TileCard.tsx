import { useMemo, useState } from 'react'
import { GripVertical, MoreHorizontal } from 'lucide-react'
import { motion } from 'motion/react'

import type { Dashboard, Tile, UpdateTileRequest } from '@/api/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { SIZE_PRESETS, type SizePreset } from '@/features/dashboard/layout'
import { TileBody } from '@/features/dashboard/TileBody'
import {
  RefreshIntervalDialog,
  RenameTileDialog,
  TileSqlDialog,
  UnpinTileDialog,
} from '@/features/dashboard/TileDialogs'
import { useNow } from '@/hooks/useNow'
import { formatInterval, formatRelativeTime } from '@/lib/format'
import { cn } from '@/lib/utils'
import { availableKinds, resolveKind, shapeOf, VIZ_LABELS, type VizKind } from '@/viz/infer'

interface TileCardProps {
  tile: Tile
  /** Other dashboards of the same dataset, for "Move to". */
  otherDashboards: Dashboard[]
  devMode: boolean
  editing: boolean
  isRefreshing: boolean
  /** Goes up each time a poll brought different rows; triggers the flash. */
  changeCount: number
  /** Position in the grid, for the staggered entrance. */
  index: number
  onRefresh: () => Promise<unknown>
  onConfirm: () => Promise<unknown>
  onUpdate: (req: UpdateTileRequest) => Promise<unknown>
  onDelete: () => Promise<unknown>
  onResize: (preset: SizePreset) => void
}

const ENTRANCE_STAGGER_S = 0.03
const ENTRANCE_MAX_DELAY_S = 0.3

export function TileCard({
  tile,
  otherDashboards,
  devMode,
  editing,
  isRefreshing,
  changeCount,
  index,
  onRefresh,
  onConfirm,
  onUpdate,
  onDelete,
  onResize,
}: TileCardProps) {
  const [renameOpen, setRenameOpen] = useState(false)
  const [intervalOpen, setIntervalOpen] = useState(false)
  const [unpinOpen, setUnpinOpen] = useState(false)
  const [sqlOpen, setSqlOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  useNow(5000) // re-render every 5 s so "Updated 12 s ago" keeps moving

  const shape = useMemo(() => shapeOf(tile.result?.rows ?? [], tile.columns), [tile.result?.rows, tile.columns])
  const kinds = useMemo(() => availableKinds(shape), [shape])
  const kind: VizKind = resolveKind(tile.viz, shape, tile.chart)

  const run = async (action: () => Promise<unknown>) => {
    setBusy(true)
    try {
      await action()
    } finally {
      setBusy(false)
    }
  }

  const updated = tile.result?.computed_at ? formatRelativeTime(tile.result.computed_at) : null

  return (
    <motion.div
      data-testid="tile"
      data-tile-id={tile.tile_id}
      data-viz={kind}
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: 'easeOut', delay: Math.min(index * ENTRANCE_STAGGER_S, ENTRANCE_MAX_DELAY_S) }}
      className={cn(
        'group relative flex h-full flex-col overflow-hidden rounded-xl border bg-card',
        'transition-[border-color,box-shadow,translate] duration-150 ease-out',
        !editing && 'hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-[0_14px_36px_-18px_var(--primary)]',
        editing && 'border-dashed',
      )}
    >
      {changeCount > 0 && (
        <span key={changeCount} aria-hidden className="tile-flash pointer-events-none absolute inset-0 z-10 rounded-xl" />
      )}

      <div
        className={cn(
          'flex h-10 shrink-0 items-center gap-1.5 border-b px-3',
          editing && 'tile-drag-handle cursor-grab active:cursor-grabbing',
        )}
      >
        {editing && <GripVertical aria-hidden className="size-4 shrink-0 text-muted-foreground" />}
        <span className="min-w-0 flex-1 truncate text-sm font-medium" title={tile.title}>
          {tile.title}
        </span>
        <div className="flex shrink-0 items-center gap-1" onPointerDown={(e) => e.stopPropagation()}>
          {tile.state === 'needs_review' && (
            <Badge variant="outline" className="border-amber-500/30 bg-amber-500/10 text-xs font-normal text-amber-600 dark:text-amber-400">
              Needs review
            </Badge>
          )}
          {tile.state === 'error' && (
            <Badge variant="destructive" className="text-xs font-normal">Refresh failed</Badge>
          )}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label="Tile actions"
                className="opacity-60 transition-opacity group-hover:opacity-100 focus-visible:opacity-100 data-[state=open]:opacity-100"
              >
                <MoreHorizontal className="size-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-48">
              <DropdownMenuSub>
                <DropdownMenuSubTrigger>Show as</DropdownMenuSubTrigger>
                <DropdownMenuSubContent>
                  <DropdownMenuRadioGroup
                    value={tile.viz ?? 'auto'}
                    onValueChange={(value) => void onUpdate({ viz: value as VizKind | 'auto' }).catch(() => undefined)}
                  >
                    <DropdownMenuRadioItem value="auto">Automatic</DropdownMenuRadioItem>
                    {kinds.map((k) => (
                      <DropdownMenuRadioItem key={k} value={k}>{VIZ_LABELS[k]}</DropdownMenuRadioItem>
                    ))}
                  </DropdownMenuRadioGroup>
                </DropdownMenuSubContent>
              </DropdownMenuSub>
              <DropdownMenuSub>
                <DropdownMenuSubTrigger>Size</DropdownMenuSubTrigger>
                <DropdownMenuSubContent>
                  {(Object.keys(SIZE_PRESETS) as SizePreset[]).map((preset) => (
                    <DropdownMenuItem key={preset} onClick={() => onResize(preset)}>
                      {SIZE_PRESETS[preset].label}
                    </DropdownMenuItem>
                  ))}
                </DropdownMenuSubContent>
              </DropdownMenuSub>
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={() => void onRefresh()}>Refresh now</DropdownMenuItem>
              <DropdownMenuItem onClick={() => setRenameOpen(true)}>Rename</DropdownMenuItem>
              <DropdownMenuItem onClick={() => setIntervalOpen(true)}>Refresh interval</DropdownMenuItem>
              {otherDashboards.length > 0 && (
                <DropdownMenuSub>
                  <DropdownMenuSubTrigger>Move to</DropdownMenuSubTrigger>
                  <DropdownMenuSubContent>
                    {otherDashboards.map((d) => (
                      <DropdownMenuItem
                        key={d.dashboard_id}
                        onClick={() => void onUpdate({ dashboard_id: d.dashboard_id }).catch(() => undefined)}
                      >
                        {d.name}
                      </DropdownMenuItem>
                    ))}
                  </DropdownMenuSubContent>
                </DropdownMenuSub>
              )}
              {devMode && <DropdownMenuItem onClick={() => setSqlOpen(true)}>View SQL</DropdownMenuItem>}
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={() => setUnpinOpen(true)} className="text-destructive focus:text-destructive">
                Unpin
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-4">
        {tile.state === 'needs_review' && (
          <div className="flex shrink-0 items-center gap-2 rounded-md border border-amber-500/30 bg-amber-500/10 px-2.5 py-1.5 text-xs text-amber-800 dark:text-amber-300">
            <span
              className="line-clamp-2 min-w-0 flex-1"
              title="The data this tile reads has changed. The numbers shown are from before the change."
            >
              The data this tile reads has changed. The numbers shown are from before the change.
            </span>
            <Button
              variant="outline"
              size="sm"
              className="h-7 shrink-0 border-amber-500/40 text-xs hover:bg-amber-500/20"
              onClick={() => void run(onConfirm)}
              disabled={busy}
            >
              Review and accept
            </Button>
          </div>
        )}
        {tile.state === 'error' && (
          <div className="flex shrink-0 items-center gap-2 rounded-md border border-destructive/30 bg-destructive/10 px-2.5 py-1.5 text-xs text-destructive">
            <span className="line-clamp-2 min-w-0 flex-1" title={`Last refresh failed: ${tile.last_error || 'Unknown error'}`}>
              Last refresh failed: {tile.last_error || 'Unknown error'}
            </span>
            <Button variant="outline" size="sm" className="h-7 shrink-0 text-xs" onClick={() => void run(onRefresh)} disabled={busy || isRefreshing}>
              Retry
            </Button>
          </div>
        )}
        <div className="min-h-0 flex-1">
          {tile.result ? (
            <TileBody shape={shape} kind={kind} devMode={devMode} />
          ) : (
            <p className="py-2 text-sm text-muted-foreground">Not computed yet.</p>
          )}
        </div>
      </div>

      <footer className="flex shrink-0 items-center justify-between gap-2 border-t px-4 py-2 text-xs text-muted-foreground">
        {isRefreshing ? (
          <span>Refreshing…</span>
        ) : updated?.relative ? (
          <span>
            <span title={updated.absolute}>Updated {updated.relative}</span> · every {formatInterval(tile.refresh_interval_s)}
          </span>
        ) : (
          <span>every {formatInterval(tile.refresh_interval_s)}</span>
        )}
      </footer>

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
      <UnpinTileDialog open={unpinOpen} onOpenChange={setUnpinOpen} onUnpin={onDelete} />
      {devMode && <TileSqlDialog open={sqlOpen} onOpenChange={setSqlOpen} sql={tile.sql} />}
    </motion.div>
  )
}
