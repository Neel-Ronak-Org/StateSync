import { useCallback, useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import ReactGridLayout, {
  useContainerWidth,
  verticalCompactor,
  type Layout,
} from 'react-grid-layout'

import 'react-grid-layout/css/styles.css'
import 'react-resizable/css/styles.css'

import type { Tile } from '@/api/types'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { KEYS, readJson, writeJson } from '@/lib/storage'
import { cn } from '@/lib/utils'
import { TileCard } from '@/features/dashboard/TileCard'
import { useTiles, type LayoutItemData } from '@/features/dashboard/useTiles'

interface DashboardViewProps {
  devMode: boolean
}

function reconcileLayout(tiles: Tile[], savedLayout: LayoutItemData[]): LayoutItemData[] {
  const tileIds = new Set(tiles.map((t) => t.tile_id))
  // Drop saved entries for tiles that no longer exist
  const existingLayout = savedLayout.filter((item) => tileIds.has(item.i))
  const layoutTileIds = new Set(existingLayout.map((item) => item.i))

  // Find current maxY across existing layout items
  const maxY = existingLayout.reduce((max, item) => Math.max(max, item.y + item.h), 0)
  let curX = 0
  let curY = maxY
  let rowH = 0

  const reconciled = [...existingLayout]

  // Add default slot at bottom for missing tiles
  for (const tile of tiles) {
    if (!layoutTileIds.has(tile.tile_id)) {
      const isKpi = Boolean(
        tile.result?.rows?.length === 1 &&
        (tile.columns ? tile.columns.length === 1 : Object.keys(tile.result.rows[0] ?? {}).length === 1)
      )
      const w = isKpi ? 3 : 4
      const h = isKpi ? 4 : 6

      if (curX + w > 12) {
        curX = 0
        curY += rowH
        rowH = 0
      }

      reconciled.push({
        i: tile.tile_id,
        x: curX,
        y: curY,
        w,
        h,
      })

      curX += w
      rowH = Math.max(rowH, h)
    }
  }

  return reconciled
}

export function DashboardView({ devMode }: DashboardViewProps) {
  const {
    tiles,
    isLoading,
    isError,
    error,
    refetch,
    refreshingTileIds,
    isRefreshingAll,
    refreshTile,
    confirmTile,
    updateTile,
    deleteTile,
    refreshAll,
  } = useTiles()

  const { width, containerRef, mounted } = useContainerWidth()

  const [layout, setLayout] = useState<LayoutItemData[]>(() => {
    const saved = readJson<LayoutItemData[]>(KEYS.layout, [])
    return reconcileLayout(tiles, saved)
  })

  // Synchronize layout when tiles change (pinned, unpinned, or fetched)
  useEffect(() => {
    const saved = readJson<LayoutItemData[]>(KEYS.layout, [])
    const next = reconcileLayout(tiles, saved)
    setLayout((prev) => {
      const isSame =
        prev.length === next.length &&
        prev.every((p, idx) => {
          const s = next[idx]
          return s && p.i === s.i && p.x === s.x && p.y === s.y && p.w === s.w && p.h === s.h
        })
      if (!isSame) {
        writeJson(KEYS.layout, next)
        return next
      }
      return prev
    })
  }, [tiles])

  const handleLayoutChange = useCallback(
    (nextLayout: Layout) => {
      const tileIds = new Set(tiles.map((t) => t.tile_id))
      const simplified: LayoutItemData[] = nextLayout
        .filter((it) => tileIds.has(it.i))
        .map(({ i, x, y, w, h }) => ({ i, x, y, w, h }))
      writeJson(KEYS.layout, simplified)
      setLayout((prev) => {
        const isSame =
          prev.length === simplified.length &&
          prev.every((p, idx) => {
            const s = simplified[idx]
            return s && p.i === s.i && p.x === s.x && p.y === s.y && p.w === s.w && p.h === s.h
          })
        return isSame ? prev : simplified
      })
    },
    [tiles],
  )

  return (
    <div className="flex flex-1 flex-col h-full overflow-hidden">
      {/* Top bar */}
      <header className="flex h-14 shrink-0 items-center justify-between border-b px-6">
        <div className="flex items-center gap-3">
          <h1 className="text-sm font-semibold text-foreground">Dashboard</h1>
          <span className="text-xs text-muted-foreground">
            {tiles.length} {tiles.length === 1 ? 'tile' : 'tiles'}
          </span>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={refreshAll}
          disabled={isRefreshingAll}
          className="gap-2 text-xs"
        >
          <RefreshCw className={cn('size-3.5', isRefreshingAll && 'animate-spin')} />
          <span>Refresh all</span>
        </Button>
      </header>

      {/* Body States */}
      {isLoading ? (
        <div className="flex-1 overflow-auto p-6">
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            <Skeleton className="h-44 rounded-lg" />
            <Skeleton className="h-44 rounded-lg" />
            <Skeleton className="h-44 rounded-lg" />
          </div>
        </div>
      ) : isError ? (
        <div className="p-6">
          <Alert variant="destructive">
            <AlertDescription className="flex items-center justify-between">
              <span>{error instanceof Error ? error.message : 'Failed to load tiles.'}</span>
              <Button variant="outline" size="sm" onClick={() => void refetch()}>
                Retry
              </Button>
            </AlertDescription>
          </Alert>
        </div>
      ) : tiles.length === 0 ? (
        <main className="flex flex-1 flex-col items-center justify-center p-6 text-center">
          <h2 className="text-base font-medium text-foreground">No tiles yet</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Pin an answer from the chat to keep it here.
          </p>
          <Button
            variant="outline"
            size="sm"
            className="mt-4"
            onClick={() => {
              window.location.hash = '#/chat'
            }}
          >
            Go to chat
          </Button>
        </main>
      ) : (
        <div className="flex-1 overflow-auto p-4">
          <div ref={containerRef}>
            {mounted && (
              <ReactGridLayout
                width={width}
                layout={layout}
                gridConfig={{
                  cols: 12,
                  rowHeight: 40,
                  margin: [16, 16],
                }}
                dragConfig={{
                  enabled: true,
                  handle: '.tile-drag-handle',
                }}
                compactor={verticalCompactor}
                onLayoutChange={handleLayoutChange}
              >
                {tiles.map((t) => (
                  <div key={t.tile_id} className="h-full">
                    <TileCard
                      tile={t}
                      devMode={devMode}
                      isRefreshing={refreshingTileIds.has(t.tile_id)}
                      onRefresh={() => refreshTile(t.tile_id)}
                      onConfirm={() => confirmTile(t.tile_id)}
                      onUpdate={(req) => updateTile(t.tile_id, req)}
                      onDelete={() => deleteTile(t.tile_id)}
                    />
                  </div>
                ))}
              </ReactGridLayout>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
