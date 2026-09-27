import { useCallback, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { api, ApiError } from '@/api/client'
import type { Tile, UpdateTileRequest } from '@/api/types'
import { KEYS, readJson, writeJson } from '@/lib/storage'

export interface LayoutItemData {
  i: string
  x: number
  y: number
  w: number
  h: number
}

/**
 * Tell the user a tile action failed. ApiError messages are written to be shown
 * (the server's plain `detail`, or client.ts's fixed sentences); anything else is
 * a programming error, so the user gets a fixed sentence, not its raw text.
 */
function notifyFailure(err: unknown, fallback: string): void {
  toast.error(err instanceof ApiError ? err.message : fallback)
}

export function useTiles() {
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: ['tiles'],
    queryFn: api.tiles,
  })

  const [refreshingTileIds, setRefreshingTileIds] = useState<Set<string>>(new Set())
  const [isRefreshingAll, setIsRefreshingAll] = useState(false)

  // Update a single tile in TanStack query cache
  const setTileInCache = useCallback((updatedTile: Tile) => {
    queryClient.setQueryData<{ tiles: Tile[] }>(['tiles'], (old) => {
      if (!old) return { tiles: [updatedTile] }
      const idx = old.tiles.findIndex((t) => t.tile_id === updatedTile.tile_id)
      if (idx >= 0) {
        const next = [...old.tiles]
        next[idx] = updatedTile
        return { tiles: next }
      }
      return { tiles: [updatedTile, ...old.tiles] }
    })
  }, [queryClient])

  // Remove a tile from TanStack query cache
  const removeTileFromCache = useCallback((tileId: string) => {
    queryClient.setQueryData<{ tiles: Tile[] }>(['tiles'], (old) => {
      if (!old) return old
      return { tiles: old.tiles.filter((t) => t.tile_id !== tileId) }
    })
  }, [queryClient])

  const refreshTile = useCallback(async (tileId: string) => {
    setRefreshingTileIds((prev) => new Set(prev).add(tileId))
    try {
      const res = await api.refreshTile(tileId)
      setTileInCache(res.tile)
      return res.tile
    } catch (err) {
      // Called from buttons and menu items with no catch: report and resolve to
      // null rather than leave an unhandled rejection.
      notifyFailure(err, 'Failed to refresh tile.')
      return null
    } finally {
      setRefreshingTileIds((prev) => {
        const next = new Set(prev)
        next.delete(tileId)
        return next
      })
    }
  }, [setTileInCache])

  const confirmTile = useCallback(async (tileId: string) => {
    try {
      const res = await api.confirmTile(tileId)
      setTileInCache(res.tile)
      return res.tile
    } catch (err) {
      notifyFailure(err, 'Failed to confirm tile.')
      return null
    }
  }, [setTileInCache])

  const updateTile = useCallback(async (tileId: string, req: UpdateTileRequest) => {
    try {
      const res = await api.updateTile(tileId, req)
      setTileInCache(res.tile)
      return res.tile
    } catch (err) {
      notifyFailure(err, 'Failed to update tile.')
      throw err
    }
  }, [setTileInCache])

  const deleteTile = useCallback(async (tileId: string) => {
    try {
      await api.deleteTile(tileId)
      removeTileFromCache(tileId)
      // Drop from persisted layout storage
      const saved = readJson<LayoutItemData[]>(KEYS.layout, [])
      const next = saved.filter((item) => item.i !== tileId)
      writeJson(KEYS.layout, next)
    } catch (err) {
      notifyFailure(err, 'Failed to unpin tile.')
      throw err
    }
  }, [removeTileFromCache])

  const refreshAll = useCallback(async () => {
    const tiles = query.data?.tiles ?? []
    if (tiles.length === 0) return
    setIsRefreshingAll(true)

    // Concurrency pool with at most 3 in flight at once
    const executing = new Set<Promise<void>>()
    for (const tile of tiles) {
      const p: Promise<void> = (async () => {
        setRefreshingTileIds((prev) => new Set(prev).add(tile.tile_id))
        try {
          const res = await api.refreshTile(tile.tile_id)
          setTileInCache(res.tile)
        } catch (err) {
          notifyFailure(err, 'Failed to refresh a tile.')
        } finally {
          setRefreshingTileIds((prev) => {
            const next = new Set(prev)
            next.delete(tile.tile_id)
            return next
          })
        }
      })()

      executing.add(p)
      p.finally(() => executing.delete(p))
      if (executing.size >= 3) {
        await Promise.race(executing)
      }
    }

    await Promise.all(executing)
    setIsRefreshingAll(false)
  }, [query.data?.tiles, setTileInCache])

  return {
    ...query,
    tiles: query.data?.tiles ?? [],
    refreshingTileIds,
    isRefreshingAll,
    refreshTile,
    confirmTile,
    updateTile,
    deleteTile,
    refreshAll,
  }
}
