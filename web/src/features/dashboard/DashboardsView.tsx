import { useCallback, useMemo, useState, type ComponentProps, type ReactNode } from 'react'
import { LayoutGrid, MessageSquare, MoreHorizontal, Pin, Plus, RefreshCw } from 'lucide-react'
import { motion } from 'motion/react'
import ReactGridLayout, { useContainerWidth, verticalCompactor, type EventCallback } from 'react-grid-layout'

import 'react-grid-layout/css/styles.css'
import 'react-resizable/css/styles.css'

import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Skeleton } from '@/components/ui/skeleton'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { DashboardNameDialog, DeleteDashboardDialog } from '@/features/dashboard/DashboardDialogs'
import {
  GRID_COLS,
  GRID_MARGIN,
  layoutsDiffer,
  placeTiles,
  resize,
  ROW_HEIGHT,
  type SizePreset,
  type Slot,
} from '@/features/dashboard/layout'
import { TileCard } from '@/features/dashboard/TileCard'
import { useDashboards } from '@/features/dashboard/useDashboards'
import { useTiles } from '@/features/dashboard/useTiles'
import { useNow } from '@/hooks/useNow'
import { formatRelativeTime } from '@/lib/format'
import { cn } from '@/lib/utils'
import { resolveKind, shapeOf } from '@/viz/infer'

interface DashboardsViewProps {
  /** The active workspace's database: dashboards belong to it. */
  dataset: string
  /** Its name, for the empty state. */
  workspaceName: string
  picker: ReactNode
  devMode: boolean
  onAsk: () => void
}

function HeaderButton({ label, children, ...props }: { label: string } & ComponentProps<typeof Button>) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button variant="outline" size="icon-sm" aria-label={label} {...props}>
          {children}
        </Button>
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  )
}

function RefreshIndicator({ updatedAt }: { updatedAt: number }) {
  useNow(1000)
  const updated = updatedAt ? formatRelativeTime(updatedAt).relative : ''
  return (
    <div data-testid="refresh-indicator" className="hidden items-center gap-2 text-xs text-muted-foreground md:flex">
      <span className="relative inline-flex size-2 rounded-full bg-muted-foreground" />
      <span className="font-medium text-foreground/80">Auto-refresh</span>
      {updated && <span>· Updated {updated}</span>}
    </div>
  )
}

export function DashboardsView({ dataset, workspaceName, picker, devMode, onAsk }: DashboardsViewProps) {
  const boards = useDashboards(dataset)
  const active = boards.active
  const tiles = useTiles(active?.dashboard_id ?? null)

  const [editing, setEditing] = useState(false)
  const [newOpen, setNewOpen] = useState(false)
  const [renameOpen, setRenameOpen] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)

  const view = useMemo(
    () =>
      tiles.tiles.map((tile) => {
        const shape = shapeOf(tile.result?.rows ?? [], tile.columns)
        return { tile, shape, kind: resolveKind(tile.viz, shape, tile.chart) }
      }),
    [tiles.tiles],
  )
  const slots = useMemo(
    () => placeTiles(view.map((v) => ({ id: v.tile.tile_id, layout: v.tile.layout, kind: v.kind }))),
    [view],
  )

  const toItems = (layout: readonly Slot[]) => layout.map(({ i, x, y, w, h }) => ({ tile_id: i, x, y, w, h }))

  const onGridStop: EventCallback = useCallback(
    (layout) => {
      const next = layout.map(({ i, x, y, w, h }) => ({ i, x, y, w, h }))
      if (editing && layoutsDiffer(next, slots)) void tiles.saveLayout(toItems(next))
    },
    [editing, slots, tiles],
  )

  const onResizePreset = (tileId: string, preset: SizePreset) => {
    const slot = slots.find((s) => s.i === tileId)
    if (slot) void tiles.saveLayout(toItems([resize(slot, preset)]))
  }

  const otherDashboards = boards.dashboards.filter((d) => d.dashboard_id !== active?.dashboard_id)
  const noDashboards = !boards.isLoading && !boards.isError && boards.dashboards.length === 0

  return (
    <div className="flex h-full flex-1 flex-col overflow-hidden">
      <header className="flex h-14 shrink-0 items-center gap-3 border-b px-6">
        <div className="flex min-w-0 flex-1 items-center gap-1">
          {boards.dashboards.length === 0 ? (
            <h1 className="text-base font-semibold">Dashboards</h1>
          ) : (
            <div role="tablist" aria-label="Dashboards" className="flex min-w-0 items-center gap-1 overflow-x-auto">
              {boards.dashboards.map((d) => {
                const selected = d.dashboard_id === active?.dashboard_id
                return (
                  <button
                    key={d.dashboard_id}
                    type="button"
                    role="tab"
                    aria-selected={selected}
                    onClick={() => boards.select(d.dashboard_id)}
                    className={cn(
                      'relative h-14 shrink-0 px-3 text-sm transition-colors outline-none focus-visible:text-foreground',
                      selected ? 'font-semibold text-foreground' : 'text-muted-foreground hover:text-foreground',
                    )}
                  >
                    {d.name}
                    {selected && (
                      <motion.span
                        layoutId={`tab-underline-${dataset}`}
                        className="absolute inset-x-2 bottom-0 h-0.5 rounded-full bg-primary"
                        transition={{ type: 'spring', stiffness: 520, damping: 42 }}
                      />
                    )}
                  </button>
                )
              })}
            </div>
          )}
          <HeaderButton label="New dashboard" variant="ghost" onClick={() => setNewOpen(true)}>
            <Plus className="size-4" />
          </HeaderButton>
          {active && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon-sm" aria-label="Dashboard actions">
                  <MoreHorizontal className="size-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start">
                <DropdownMenuItem onClick={() => setRenameOpen(true)}>Rename dashboard</DropdownMenuItem>
                <DropdownMenuItem onClick={() => setDeleteOpen(true)} className="text-destructive focus:text-destructive">
                  Delete dashboard
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-2">
          {active && <RefreshIndicator updatedAt={tiles.dataUpdatedAt} />}
          {picker}
          {active && view.length > 0 && (
            <>
              <Button
                variant={editing ? 'secondary' : 'outline'}
                size="sm"
                aria-pressed={editing}
                onClick={() => setEditing((e) => !e)}
                className="h-8 gap-1.5 text-xs"
              >
                <LayoutGrid className="size-3.5" />
                {editing ? 'Done' : 'Edit layout'}
              </Button>
              <HeaderButton label="Refresh all" onClick={() => void tiles.refreshAll()} disabled={tiles.isRefreshingAll}>
                <RefreshCw className={cn('size-3.5', tiles.isRefreshingAll && 'animate-spin')} />
              </HeaderButton>
            </>
          )}
        </div>
      </header>

      <div className="flex-1 overflow-y-auto">
        {boards.isLoading || (active && tiles.isLoading) ? (
          <div className="grid gap-4 p-6 sm:grid-cols-2 xl:grid-cols-4">
            {Array.from({ length: 4 }, (_, i) => <Skeleton key={i} className="h-36 rounded-xl" />)}
            <Skeleton className="h-80 rounded-xl sm:col-span-2" />
            <Skeleton className="h-80 rounded-xl sm:col-span-2" />
          </div>
        ) : boards.isError || tiles.isError ? (
          <div className="p-6">
            <Alert variant="destructive">
              <AlertDescription className="flex items-center justify-between gap-4">
                <span>{(boards.error ?? tiles.error)?.message ?? 'Failed to load the dashboard.'}</span>
                <Button variant="outline" size="sm" onClick={() => void (boards.isError ? boards.refetch() : tiles.refetch())}>
                  Retry
                </Button>
              </AlertDescription>
            </Alert>
          </div>
        ) : noDashboards ? (
          <EmptyState
            title={<>No dashboards in <span className="text-primary">{workspaceName}</span> yet</>}
            text="Create one, then pin answers to it from Ask. A pinned answer re-runs its query on a schedule."
            primary={{ label: 'Create a dashboard', onClick: () => void boards.create('Overview'), icon: Plus }}
          />
        ) : view.length === 0 ? (
          <EmptyState
            title="This dashboard is empty"
            text="Ask a question and pin the answer to this dashboard."
            primary={{ label: 'Ask a question', onClick: onAsk, icon: MessageSquare }}
          />
        ) : (
          <DashboardGrid slots={slots} editing={editing} onStop={onGridStop}>
            {view.map(({ tile }, index) => (
              <div key={tile.tile_id} className="h-full">
                <TileCard
                  tile={tile}
                  otherDashboards={otherDashboards}
                  devMode={devMode}
                  editing={editing}
                  isRefreshing={tiles.refreshingTileIds.has(tile.tile_id)}
                  changeCount={tiles.changed[tile.tile_id] ?? 0}
                  index={index}
                  onRefresh={() => tiles.refreshTile(tile.tile_id)}
                  onConfirm={() => tiles.confirmTile(tile.tile_id)}
                  onUpdate={(req) => tiles.updateTile(tile.tile_id, req)}
                  onDelete={() => tiles.deleteTile(tile.tile_id)}
                  onResize={(preset) => onResizePreset(tile.tile_id, preset)}
                />
              </div>
            ))}
          </DashboardGrid>
        )}
      </div>

      <DashboardNameDialog
        open={newOpen}
        onOpenChange={setNewOpen}
        title="New dashboard"
        submitLabel="Create"
        initialName=""
        onSubmit={async (name) => {
          if (!(await boards.create(name))) throw new Error('not created')
        }}
      />
      {active && (
        <>
          <DashboardNameDialog
            open={renameOpen}
            onOpenChange={setRenameOpen}
            title="Rename dashboard"
            submitLabel="Save"
            initialName={active.name}
            onSubmit={(name) => boards.rename(active.dashboard_id, name)}
          />
          <DeleteDashboardDialog
            open={deleteOpen}
            onOpenChange={setDeleteOpen}
            name={active.name}
            tileCount={view.length}
            onDelete={() => boards.remove(active.dashboard_id)}
          />
        </>
      )}
    </div>
  )
}

interface EmptyStateProps {
  title: ReactNode
  text: string
  primary: { label: string; onClick: () => void; icon: typeof MessageSquare }
}

function EmptyState({ title, text, primary }: EmptyStateProps) {
  const Icon = primary.icon
  return (
    <motion.main
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      className="mx-auto flex max-w-md flex-col items-center px-6 py-24 text-center"
    >
      <div className="mb-5 grid size-14 place-items-center rounded-2xl bg-primary/12 text-primary ring-1 ring-primary/25">
        <Pin className="size-6" />
      </div>
      <h2 className="text-lg font-semibold">{title}</h2>
      <p className="mt-1.5 text-sm text-muted-foreground">{text}</p>
      <div className="mt-6 flex gap-2">
        <Button onClick={primary.onClick} className="gap-1.5">
          <Icon className="size-4" />
          {primary.label}
        </Button>
      </div>
    </motion.main>
  )
}

interface DashboardGridProps {
  slots: Slot[]
  editing: boolean
  onStop: EventCallback
  children: ReactNode
}

/**
 * The grid in its own component so it measures its width when IT mounts: the
 * page renders skeletons first, and a width hook in the page never saw the
 * container, which left the grid at the library's 1280px default (not filling
 * wide screens, overflowing narrow ones).
 */
function DashboardGrid({ slots, editing, onStop, children }: DashboardGridProps) {
  const { width, containerRef, mounted } = useContainerWidth()
  return (
    <div className="p-6 pt-5">
      <div ref={containerRef} className={cn('rounded-xl', editing && 'grid-guides dashboard-editing')}>
        {mounted && (
          <ReactGridLayout
            width={width}
            layout={slots}
            gridConfig={{ cols: GRID_COLS, rowHeight: ROW_HEIGHT, margin: [GRID_MARGIN, GRID_MARGIN], containerPadding: [0, 0] }}
            dragConfig={{ enabled: editing, handle: '.tile-drag-handle' }}
            resizeConfig={{ enabled: editing, handles: ['se'] }}
            compactor={verticalCompactor}
            onDragStop={onStop}
            onResizeStop={onStop}
          >
            {children}
          </ReactGridLayout>
        )}
      </div>
    </div>
  )
}

